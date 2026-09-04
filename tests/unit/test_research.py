from datetime import UTC, datetime, timedelta

from research.backtest import DeterministicReplay
from research.stress_tests import scenario_matrix
from research.walk_forward import expanding_folds
from training.evaluate_model import (
    EvaluationRecord,
    evaluation_report,
    expected_calibration_error,
    macro_f1,
)


def test_replay_is_stable_for_equal_timestamps() -> None:
    records = [
        {"timestamp": "2", "id": 2},
        {"timestamp": "1", "id": 1},
        {"timestamp": "1", "id": 3},
    ]
    seen: list[int] = []
    DeterministicReplay(records).run(lambda record: seen.append(record["id"]))
    assert seen == [1, 3, 2]


def test_stress_matrix_has_288_unique_scenarios() -> None:
    scenarios = scenario_matrix()
    assert len(scenarios) == 288
    assert len(set(scenarios)) == 288


def test_twelve_walk_forward_folds() -> None:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    folds = expanding_folds(
        start,
        start + timedelta(days=500),
        initial_train=timedelta(days=100),
        test_window=timedelta(days=30),
        embargo=timedelta(days=1),
    )
    assert len(folds) >= 12
    assert all(fold.train_end < fold.test_start for fold in folds)


def test_metrics() -> None:
    assert macro_f1(["bullish", "bearish", "neutral"], ["bullish", "bearish", "neutral"]) == 1
    assert expected_calibration_error([True, False], [0.75, 0.25], bins=2) == 0.25


def test_full_evaluation_report_groups_and_flags_confident_errors() -> None:
    records = [
        EvaluationRecord("bullish", "bullish", 0.8, 0.001, "macro", "BTC_USDT", 100),
        EvaluationRecord("bearish", "bullish", 0.9, -0.002, "macro", "ETH_USDT", 500),
        EvaluationRecord("neutral", "neutral", 0.6, 0.0, "regulatory", "BTC_USDT", 200),
    ]
    report = evaluation_report(records)
    assert report["false_high_confidence_count"] == 1
    assert report["latency_p95_ms"] == 500
    assert set(report["by_symbol"]) == {"BTC_USDT", "ETH_USDT"}  # type: ignore[arg-type]
