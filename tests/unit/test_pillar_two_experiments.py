from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from bot.domain.events import Direction
from bot.features.bars import Bar
from bot.strategy.time_based import TimeBasedPillarTwoConfig, TimeBasedPillarTwoEngine
from research.pillar_two_backtest import CandleBacktestConfig, CandleCosts
from research.pillar_two_experiments import (
    CachedCandidateSource,
    Candidate,
    SelectionPolicy,
    build_signal_cache,
    resample_complete_bars,
    robust_candidates,
    rolling_folds,
    run_experiments,
    select_candidate,
)

START = datetime(2026, 5, 1, tzinfo=UTC)
CONFIG = TimeBasedPillarTwoConfig()
CANDIDATE = Candidate("trend", 300, Decimal(20), Decimal(30), 0.5, ("trend",))


def bars(count: int) -> list[Bar]:
    result = []
    for index in range(count):
        price = Decimal(10000 + index)
        result.append(
            Bar(
                "BTC_USDT",
                60,
                START + timedelta(minutes=index),
                START + timedelta(minutes=index + 1),
                price,
                price + 2,
                price - 2,
                price + 1,
                Decimal(5),
                2,
            )
        )
    return result


def metrics(**changes: Any) -> dict[str, Any]:
    result = {
        "trade_count": 100,
        "profit_factor": 1.5,
        "net_pnl": 100,
        "net_return_on_initial_equity_pct": 1.0,
        "kill_switch_triggered": False,
        "positive_folds": 8,
        "fold_count": 10,
        "halted_folds": 0,
        "mean_fold_return_pct": 0.1,
    }
    result.update(changes)
    return result


def test_resampling_waits_for_complete_bar_and_discards_partial_gap_groups() -> None:
    data = bars(15)
    assert resample_complete_bars(data[:4], 300) == []
    complete = resample_complete_bars(data[:5], 300)
    assert len(complete) == 1
    assert complete[0].end == data[4].end
    assert complete[0].volume == 25
    assert complete[0].trades == 10
    assert complete[0].close == data[4].close
    with_gap = resample_complete_bars(data[:7] + data[8:], 300)
    assert [bar.start for bar in with_gap] == [data[0].start, data[10].start]


def test_rolling_folds_have_purged_boundaries_and_disjoint_validation() -> None:
    folds = rolling_folds(
        START,
        START + timedelta(days=86),
        training=timedelta(days=21),
        validation=timedelta(days=5),
        embargo=timedelta(hours=1),
    )
    assert len(folds) >= 12
    for fold in folds:
        assert fold.train_end - fold.train_start == timedelta(days=21)
        assert fold.test_start - fold.train_end == timedelta(hours=1)
    for before, after in zip(folds, folds[1:], strict=False):
        assert before.test_end <= after.test_start


def test_cached_signals_match_prefix_and_cannot_query_a_future_close() -> None:
    data = bars(80)
    cache = build_signal_cache(data, CONFIG)
    prefix = build_signal_cache(data[:65], CONFIG)
    assert all(cache.signals[at] == signal for at, signal in prefix.signals.items())
    source = CachedCandidateSource(cache, CANDIDATE)
    source.on_bar(data[64])
    assert source.signal(data[64].end) == cache.signals[data[64].end]
    with pytest.raises(ValueError, match="last consumed"):
        source.signal(data[65].end)
    with pytest.raises(ValueError, match="chronological"):
        source.on_bar(data[63])


def test_horizon_rescaling_matches_real_engine_for_trend_and_rejects_range() -> None:
    data = bars(80)
    cache = build_signal_cache(data, CONFIG)
    longer = TimeBasedPillarTwoEngine("BTC_USDT", replace(CONFIG, horizon_seconds=3600))
    source = CachedCandidateSource(cache, replace(CANDIDATE, horizon_seconds=3600))
    for bar in data:
        longer.on_bar(bar)
        source.on_bar(bar)
    actual, cached = longer.signal(data[-1].end), source.signal(data[-1].end)
    assert actual.model == "trend"
    assert actual.direction == cached.direction
    assert actual.strength == cached.strength
    assert actual.expected_move_bps == pytest.approx(cached.expected_move_bps)
    with pytest.raises(ValueError, match="mean-reversion"):
        CachedCandidateSource(
            cache, replace(CANDIDATE, horizon_seconds=3600, models=("mean_reversion",))
        )


def test_higher_timeframe_confirmation_does_not_use_unfinished_group() -> None:
    data = bars(330)
    cache = build_signal_cache(data, CONFIG)
    prefix = build_signal_cache(data[:303], CONFIG)
    assert cache.confirmations[data[302].end] == prefix.confirmations[data[302].end]
    # Fewer than 60 complete five-minute bars cannot qualify confirmation.
    assert all(cache.confirmations[bar.end] == Direction.NEUTRAL for bar in data[:299])
    assert cache.confirmations[data[304].end] == Direction.BULLISH


@pytest.mark.parametrize(
    "changed",
    [
        {"trade_count": 29},
        {"net_pnl": -1},
        {"profit_factor": 1.01},
        {"kill_switch_triggered": True},
    ],
)
def test_training_gate_stays_flat_when_base_fails(changed: dict[str, Any]) -> None:
    assert (
        select_candidate(
            {"a": {"base": metrics(**changed), "stress": metrics()}}, SelectionPolicy()
        )
        is None
    )


def test_training_selection_requires_stress_and_breaks_ties_stably() -> None:
    training = {
        "b": {"base": metrics(), "stress": metrics()},
        "a": {"base": metrics(), "stress": metrics()},
        "lucky": {"base": metrics(net_pnl=500), "stress": metrics(net_pnl=-1)},
    }
    assert select_candidate(training, SelectionPolicy()) == "a"
    assert select_candidate(dict(reversed(list(training.items()))), SelectionPolicy()) == "a"


def test_robustness_does_not_promote_positive_profit_with_few_trades_or_folds() -> None:
    candidates = {
        "a": {"base": metrics(), "stress": metrics()},
        "few": {"base": metrics(trade_count=5), "stress": metrics()},
        "one_lucky_fold": {"base": metrics(positive_folds=1), "stress": metrics()},
        "cost_sensitive": {"base": metrics(), "stress": metrics(net_pnl=-1)},
    }
    assert robust_candidates(candidates, SelectionPolicy()) == ["a"]
    with pytest.raises(ValueError, match="both base and stress"):
        robust_candidates({"missing": {"base": metrics()}}, SelectionPolicy())


def test_development_cutoff_excludes_future_prices_and_cash_is_not_positive_edge() -> None:
    data = bars(200)
    cutoff = data[179].end
    kwargs: dict[str, Any] = {
        "engine_config": CONFIG,
        "costs": CandleCosts(Decimal(5), Decimal(1), Decimal(1), Decimal(2)),
        "backtest_config": CandleBacktestConfig(),
        "candidates": [CANDIDATE],
        "development_end": cutoff,
        "training": timedelta(minutes=90),
        "validation": timedelta(minutes=30),
        "embargo": timedelta(minutes=5),
        "policy": SelectionPolicy(min_training_trades=100000),
    }
    report, ledger = run_experiments(data, **kwargs)
    poisoned = data[:180] + [replace(bar, close=Decimal("NaN")) for bar in data[180:]]
    other, other_ledger = run_experiments(poisoned, **kwargs)
    assert report == other
    assert ledger == other_ledger
    assert report["data_quality"]["bars"] == 180
    assert all(fold["selected_from_training"] is None for fold in report["folds"])
    selected = report["training_selected_validation"]["base"]
    assert selected["trade_count"] == 0
    assert selected["win_rate_pct"] is None
    assert selected["mean_fold_return_pct"] == 0
    assert selected["cash_folds"] == selected["fold_count"]


def test_embargo_must_cover_longest_candidate_horizon() -> None:
    with pytest.raises(ValueError, match="longest candidate"):
        run_experiments(
            bars(200),
            engine_config=CONFIG,
            costs=CandleCosts(Decimal(5), Decimal(1), Decimal(1), Decimal(2)),
            backtest_config=CandleBacktestConfig(),
            candidates=[replace(CANDIDATE, horizon_seconds=3600)],
            development_end=START + timedelta(minutes=200),
            training=timedelta(minutes=90),
            validation=timedelta(minutes=30),
            embargo=timedelta(minutes=5),
            policy=SelectionPolicy(),
        )
