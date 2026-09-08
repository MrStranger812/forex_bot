from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.domain.events import Direction
from bot.features.bars import Bar
from bot.strategy.time_based import TimeBasedPillarTwoConfig
from research.pillar_two_backtest import CandleBacktestConfig, CandleCosts, run_backtest
from research.signal_design_study import block_diagnostics
from research.signal_designs import (
    FAMILIES,
    DesignSource,
    FeatureSettings,
    Snapshot,
    build_snapshots,
    composed_vote,
    specialist_votes,
)

START = datetime(2026, 5, 1, tzinfo=UTC)


def bars(count: int) -> list[Bar]:
    output = []
    for i in range(count):
        price = Decimal(str(100 + 0.1 * math.sin(i / 20)))
        output.append(
            Bar(
                "BTC_USDT",
                60,
                START + timedelta(minutes=i),
                START + timedelta(minutes=i + 1),
                price,
                price + 1,
                price - 1,
                price,
                Decimal(10),
                10,
            )
        )
    return output


def snapshot(**changes: object) -> Snapshot:
    base = Snapshot(
        START + timedelta(minutes=1),
        5,
        1,
        6,
        20,
        15,
        2,
        0.25,
        1.0,
        0.6,
        5,
        0.5,
        0.7,
        False,
        False,
        0,
        0,
        False,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_snapshots_are_prefix_invariant_and_flow_uses_prior_window() -> None:
    series = bars(300)
    flow = {bar.end: Decimal(7) for bar in series}
    early = build_snapshots(series[:270], series[:270], flow, FeatureSettings(), 300)
    perturbed = series[:270] + [
        replace(bar, close=bar.close + Decimal("0.5")) for bar in series[270:]
    ]
    later = build_snapshots(perturbed, series, flow, FeatureSettings(), 300)
    assert early and all(later[at] == value for at, value in early.items())
    row = next(iter(early.values()))
    assert row.relative_flow == pytest.approx(0.4)
    assert row.absolute_flow == pytest.approx(0.4)
    assert row.activity == 1
    assert row.basis_z == 0


def test_missing_spot_observation_resets_paired_warmup_and_no_forward_fill() -> None:
    series = bars(510)
    flow = {bar.end: Decimal(7) for bar in series}
    missing = series[:260] + series[261:]
    actual = build_snapshots(series, missing, flow, FeatureSettings(), 300)
    assert not any(series[260].end <= at < series[501].end for at in actual)
    assert any(at >= series[501].end for at in actual)
    with pytest.raises(ValueError, match="chronological"):
        build_snapshots(series, series[::-1], flow, FeatureSettings(), 300)
    with pytest.raises(ValueError, match="missing or invalid"):
        build_snapshots(series, series, {}, FeatureSettings(), 300)


def test_continuation_ablations_are_nested_and_mirrored() -> None:
    cfg = FeatureSettings()
    row = snapshot()
    votes = specialist_votes(row, cfg)
    assert all(votes[name] == 1 for name in FAMILIES[:3])
    low_flow = specialist_votes(replace(row, relative_flow=0.05, absolute_flow=0.1), cfg)
    assert low_flow["activity_momentum"] == 1
    assert low_flow["relative_flow_momentum"] == low_flow["absolute_flow_momentum"] == 0
    mirrored = replace(
        row,
        return_1_bps=-1,
        return_5_bps=-6,
        return_30_bps=-20,
        spot_return_30_bps=-15,
        relative_flow=-0.25,
        absolute_flow=-1,
    )
    assert all(specialist_votes(mirrored, cfg)[name] == -1 for name in FAMILIES[:3])


def test_pressure_and_failed_breakout_require_rejection() -> None:
    cfg = FeatureSettings()
    row = snapshot(return_5_bps=0.5, return_1_bps=-0.5)
    assert specialist_votes(row, cfg)["failed_pressure"] == -1
    assert specialist_votes(replace(row, return_1_bps=0.5), cfg)["failed_pressure"] == 0
    row = snapshot(failed_high=True, close_fraction=0.2)
    assert specialist_votes(row, cfg)["failed_breakout"] == -1
    assert specialist_votes(replace(row, failed_low=True), cfg)["failed_breakout"] == 0
    row = snapshot(failed_low=True, close_fraction=0.8)
    assert specialist_votes(row, cfg)["failed_breakout"] == 1


def test_consensus_does_not_count_nested_momentum_as_independent_votes() -> None:
    cfg, row = FeatureSettings(), snapshot()
    votes = dict.fromkeys(FAMILIES, 0)
    for name in FAMILIES[:3]:
        votes[name] = 1
    assert composed_vote("consensus", row, votes, cfg) == 0
    votes["failed_breakout"] = 1
    assert composed_vote("consensus", row, votes, cfg) == 1
    votes["failed_pressure"] = -1
    assert composed_vote("consensus", row, votes, cfg) == 0


def test_router_abstains_in_ambiguous_regime() -> None:
    cfg, row = FeatureSettings(), snapshot()
    votes = dict.fromkeys(FAMILIES, 0)
    votes["absolute_flow_momentum"], votes["failed_breakout"] = 1, -1
    assert composed_vote("regime_router", row, votes, cfg) == 1
    assert composed_vote("regime_router", replace(row, efficiency=0.1), votes, cfg) == -1
    assert composed_vote("regime_router", replace(row, efficiency=0.3), votes, cfg) == 0


def test_consensus_preserves_small_basis_movement_cost_gate() -> None:
    row = snapshot(
        basis_z=3,
        basis_deviation_bps=3,
        basis_shrinking=True,
        failed_high=True,
        close_fraction=0.2,
        activity=2,
        absolute_flow=0,
    )
    source = DesignSource({row.at: row}, FeatureSettings(), "consensus", 1800)
    with pytest.raises(ValueError, match="last consumed"):
        source.signal(row.at)
    source.on_bar(bars(1)[0])
    result = source.signal(row.at)
    assert result.direction == Direction.BEARISH
    assert result.expected_move_bps == 3
    assert result.expected_net_return_bps is None and result.probability is None
    with pytest.raises(ValueError, match="last consumed"):
        source.signal(row.at + timedelta(minutes=1))


def test_new_signal_enters_next_open_and_pays_both_fees() -> None:
    series = bars(3)
    row = snapshot(atr_bps=20)
    result = run_backtest(
        series,
        symbol="BTC_USDT",
        engine_config=TimeBasedPillarTwoConfig(horizon_seconds=60),
        costs=CandleCosts(Decimal(4), Decimal(0), Decimal(0), Decimal(2)),
        config=CandleBacktestConfig(stop_bps=Decimal(500), target_bps=Decimal(500)),
        source_factory=lambda: DesignSource(
            {row.at: row}, FeatureSettings(), "activity_momentum", 60
        ),
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_at == series[1].start and trade.entry_fill == series[1].open
    assert trade.exit_at == series[1].end
    expected = (trade.entry_fill + trade.exit_fill) * trade.quantity * Decimal("0.0004")
    assert abs(trade.fees - expected) < Decimal("1e-24")


def test_bootstrap_is_deterministic_and_does_not_make_losers_significant() -> None:
    series = {"a": [-0.1] * 13, "b": [-0.2] * 13}
    result = block_diagnostics(series, draws=200, fold_block_length=2, seed=42)
    assert result == block_diagnostics(series, draws=200, fold_block_length=2, seed=42)
    assert result["centered_max_mean_tail_fraction"] == 1
    assert result["mean_fold_return_95_interval_pct"]["a"] == pytest.approx([-0.1, -0.1])
    with pytest.raises(ValueError):
        block_diagnostics({"a": [1], "b": [1, 2]}, draws=200, fold_block_length=1, seed=42)
