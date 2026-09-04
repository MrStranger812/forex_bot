from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import mean

LABELS = ("bullish", "bearish", "neutral")


def macro_f1(actual: Iterable[str], predicted: Iterable[str]) -> float:
    actual_values = list(actual)
    predicted_values = list(predicted)
    if len(actual_values) != len(predicted_values):
        raise ValueError("actual and predicted lengths differ")
    scores = []
    for label in LABELS:
        tp = sum(
            a == label and p == label for a, p in zip(actual_values, predicted_values, strict=True)
        )
        fp = sum(
            a != label and p == label for a, p in zip(actual_values, predicted_values, strict=True)
        )
        fn = sum(
            a == label and p != label for a, p in zip(actual_values, predicted_values, strict=True)
        )
        denominator = 2 * tp + fp + fn
        scores.append(2 * tp / denominator if denominator else 0.0)
    return sum(scores) / len(scores)


def expected_calibration_error(
    correct: Iterable[bool], confidences: Iterable[float], bins: int = 10
) -> float:
    buckets: dict[int, list[tuple[bool, float]]] = defaultdict(list)
    values = list(zip(correct, confidences, strict=True))
    if not values:
        return 0.0
    for is_correct, confidence in values:
        if not 0 <= confidence <= 1:
            raise ValueError("confidence outside [0, 1]")
        buckets[min(int(confidence * bins), bins - 1)].append((is_correct, confidence))
    error = 0.0
    for bucket in buckets.values():
        accuracy = sum(item[0] for item in bucket) / len(bucket)
        mean_confidence = sum(item[1] for item in bucket) / len(bucket)
        error += len(bucket) / len(values) * abs(accuracy - mean_confidence)
    return error


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    actual: str
    predicted: str
    confidence: float
    fee_adjusted_return: float
    event_type: str
    symbol: str
    latency_ms: float


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * probability)
    return ordered[index]


def evaluation_report(
    records: Iterable[EvaluationRecord], high_confidence: float = 0.8
) -> dict[str, object]:
    values = list(records)
    correct = [row.actual == row.predicted for row in values]
    false_high = [
        row
        for row, is_correct in zip(values, correct, strict=True)
        if not is_correct and row.confidence >= high_confidence
    ]

    def grouped(field: str) -> dict[str, dict[str, float]]:
        buckets: dict[str, list[EvaluationRecord]] = defaultdict(list)
        for row in values:
            buckets[str(getattr(row, field))].append(row)
        return {
            key: {
                "count": float(len(bucket)),
                "accuracy": mean(item.actual == item.predicted for item in bucket),
                "mean_fee_adjusted_return": mean(item.fee_adjusted_return for item in bucket),
            }
            for key, bucket in buckets.items()
        }

    latencies = [row.latency_ms for row in values]
    return {
        "macro_f1": macro_f1((row.actual for row in values), (row.predicted for row in values)),
        "calibration_error": expected_calibration_error(
            correct, (row.confidence for row in values)
        ),
        "false_high_confidence_count": len(false_high),
        "mean_fee_adjusted_return": mean(row.fee_adjusted_return for row in values)
        if values
        else 0.0,
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "by_event_type": grouped("event_type"),
        "by_symbol": grouped("symbol"),
    }
