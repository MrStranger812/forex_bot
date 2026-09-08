from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bot.domain.events import Direction, PillarTwoSignal
from research.outcome_model import OutcomeModelConfig, OutcomeTree, prediction_metrics
from research.pillar_two_backtest import BacktestResult
from research.pillar_two_outcomes import Threshold, choose_threshold
from research.trade_outcomes import FEATURE_NAMES, Opportunity, Outcome


def rows(count: int, *, shift: int = 0, invert: bool = False) -> list[Outcome]:
    result = []
    for index in range(count):
        at = datetime(2026, 5, 1, tzinfo=UTC) + timedelta(hours=index + shift)
        high = index % 2 == 0
        features = (float(high),) + (0.0,) * (len(FEATURE_NAMES) - 1)
        net = (50.0 if high else -50.0) * (-1 if invert else 1)
        signal = PillarTwoSignal(
            "BTC_USDT",
            Direction.BULLISH,
            1,
            "TRENDING",
            at,
            at + timedelta(minutes=1),
            (),
            1,
            horizon_seconds=3600,
        )
        result.append(
            Outcome(
                Opportunity(at, index, signal, features),
                at,
                at + timedelta(hours=1),
                "time",
                net + 13,
                net,
                net - 13,
            )
        )
    return result


def tree() -> OutcomeTree:
    return OutcomeTree(
        OutcomeModelConfig(
            max_depth=2,
            min_fit_leaf=10,
            min_calibration_leaf=5,
            shrinkage_samples=5,
            min_fit_samples=20,
        )
    )


def test_tree_structure_is_learned_only_from_fit_and_returns_from_later_calibration() -> None:
    model = tree()
    model.fit(rows(100))
    nodes = model.to_record()["nodes"]
    assert len(nodes) == 3
    assert not model.predict(rows(1)[0].opportunity.features).supported
    model.calibrate(rows(40, shift=200))
    positive = model.predict(rows(1)[0].opportunity.features)
    negative = model.predict(rows(2)[1].opportunity.features)
    assert positive.supported and negative.supported
    assert positive.net_bps > 0 > negative.net_bps
    assert 0 < negative.probability < positive.probability < 1
    assert positive.stress_net_bps < positive.net_bps
    # Later calibration can change the leaf estimates but cannot rewrite tree splits.
    model.calibrate(rows(40, shift=300, invert=True))
    assert model.to_record()["nodes"] == nodes
    assert model.predict(rows(1)[0].opportunity.features).net_bps < 0


def test_sparse_and_unseen_calibration_leaves_fail_closed() -> None:
    model = tree()
    model.fit(rows(100))
    model.calibrate(rows(2, shift=200))
    assert not model.predict(rows(1)[0].opportunity.features).supported
    model.calibrate([])
    assert not model.predict(rows(1)[0].opportunity.features).supported


def test_deterministic_fit_and_meaningful_probabilistic_benchmark() -> None:
    left, right = tree(), tree()
    for model in (left, right):
        model.fit(rows(100))
        model.calibrate(rows(40, shift=200))
    assert left.to_record() == right.to_record()
    metrics = prediction_metrics(left, rows(40, shift=300))
    assert metrics["brier_score"] < metrics["prior_brier_score"]
    assert metrics["net_mae_bps"] < metrics["prior_net_mae_bps"]
    assert metrics["false_high_confidence_count"] == 0


def test_invalid_targets_and_unfitted_predictions_are_rejected() -> None:
    model = tree()
    with pytest.raises(ValueError, match="fitted tree"):
        model.predict(rows(1)[0].opportunity.features)
    with pytest.raises(ValueError, match="finite"):
        model.fit([replace(row, net_bps=float("nan")) for row in rows(100)])


def replay_metrics(**changes: Any) -> BacktestResult:
    metrics = {
        "trade_count": 20,
        "net_pnl": 10,
        "profit_factor": 1.5,
        "kill_switch_triggered": False,
        "net_return_on_initial_equity_pct": 0.1,
    }
    metrics.update(changes)
    return BacktestResult(metrics, [], [])


def test_threshold_selection_requires_actual_net_profit_and_stress_trades() -> None:
    generous = Threshold(2, 0)
    strict = Threshold(5, 0.5)
    selection = [
        (generous, {"base": replay_metrics(net_pnl=-5), "stress": replay_metrics()}),
        (strict, {"base": replay_metrics(), "stress": replay_metrics(trade_count=2)}),
    ]
    assert choose_threshold(selection, 10, 1.1) is None
    selection[1][1]["stress"] = replay_metrics()
    assert choose_threshold(selection, 10, 1.1) == strict


def test_threshold_ties_prefer_stricter_rule_without_validation_input() -> None:
    scenarios = {"base": replay_metrics(), "stress": replay_metrics()}
    candidates = [(Threshold(2, 0), scenarios), (Threshold(5, 0.5), scenarios)]
    assert choose_threshold(candidates, 10, 1.1) == Threshold(5, 0.5)
