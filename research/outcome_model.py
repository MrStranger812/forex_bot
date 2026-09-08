"""Small deterministic outcome tree; structure and leaf calibration use disjoint data."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any

from research.trade_outcomes import FEATURE_NAMES, Outcome


@dataclass(frozen=True, slots=True)
class OutcomeModelConfig:
    max_depth: int = 3
    min_fit_leaf: int = 100
    min_calibration_leaf: int = 40
    shrinkage_samples: int = 40
    min_fit_samples: int = 300

    def __post_init__(self) -> None:
        if not 1 <= self.max_depth <= 5:
            raise ValueError("outcome tree depth must be within [1, 5]")
        for count in (
            self.min_fit_leaf,
            self.min_calibration_leaf,
            self.shrinkage_samples,
            self.min_fit_samples,
        ):
            if type(count) is not int or count < 1:
                raise ValueError("outcome sample limits must be positive integers")


@dataclass(frozen=True, slots=True)
class Prediction:
    probability: float
    net_bps: float
    stress_net_bps: float
    calibration_samples: int
    supported: bool


@dataclass(slots=True)
class Node:
    feature: int = -1
    threshold: float = 0.0
    left: int = -1
    right: int = -1
    fit_samples: int = 0


class OutcomeTree:
    def __init__(self, config: OutcomeModelConfig) -> None:
        self.config = config
        self.nodes: list[Node] = []
        self.leaves: dict[int, Prediction] = {}
        self.prior = Prediction(0.5, 0, 0, 0, False)

    def fit(self, rows: Sequence[Outcome]) -> None:
        if len(rows) < self.config.min_fit_samples:
            raise ValueError("insufficient mature fitting outcomes")
        if any(
            len(row.opportunity.features) != len(FEATURE_NAMES)
            or not all(
                math.isfinite(value)
                for value in (*row.opportunity.features, row.net_bps, row.stress_net_bps)
            )
            for row in rows
        ):
            raise ValueError("outcomes require finite features and targets")
        self.nodes.clear()
        self.leaves.clear()
        self.prior = Prediction(
            fmean(row.net_bps > 0 for row in rows),
            fmean(row.net_bps for row in rows),
            fmean(row.stress_net_bps for row in rows),
            len(rows),
            False,
        )

        def grow(subset: list[Outcome], depth: int) -> int:
            index = len(self.nodes)
            self.nodes.append(Node(fit_samples=len(subset)))
            if depth >= self.config.max_depth or len(subset) < 2 * self.config.min_fit_leaf:
                return index
            total = sum(row.net_bps for row in subset)
            best_gain = 1e-9
            best: tuple[int, float] | None = None
            for feature in range(len(FEATURE_NAMES)):
                ordered = sorted(subset, key=lambda row: row.opportunity.features[feature])
                left_sum = 0.0
                # At most four quantile boundaries per feature limits search and variance.
                feature_values = [row.opportunity.features[feature] for row in ordered]
                boundaries = {
                    bisect_right(feature_values, feature_values[len(subset) * q // 5])
                    for q in (1, 2, 3, 4)
                }
                for n, row in enumerate(ordered[:-1], 1):
                    left_sum += row.net_bps
                    if n not in boundaries or min(n, len(subset) - n) < self.config.min_fit_leaf:
                        continue
                    before = row.opportunity.features[feature]
                    after = ordered[n].opportunity.features[feature]
                    if before == after:
                        continue
                    gain = left_sum**2 / n + (total - left_sum) ** 2 / (len(subset) - n)
                    gain -= total**2 / len(subset)
                    if gain > best_gain:
                        best_gain, best = gain, (feature, (before + after) / 2)
            if best is not None:
                feature, threshold = best
                left = [row for row in subset if row.opportunity.features[feature] <= threshold]
                right = [row for row in subset if row.opportunity.features[feature] > threshold]
                self.nodes[index].feature, self.nodes[index].threshold = feature, threshold
                self.nodes[index].left = grow(left, depth + 1)
                self.nodes[index].right = grow(right, depth + 1)
            return index

        grow(list(rows), 0)

    def leaf(self, features: Sequence[float]) -> int:
        if (
            not self.nodes
            or len(features) != len(FEATURE_NAMES)
            or not all(math.isfinite(value) for value in features)
        ):
            raise ValueError("a fitted tree and finite matching features are required")
        index = 0
        while self.nodes[index].feature >= 0:
            node = self.nodes[index]
            index = node.left if features[node.feature] <= node.threshold else node.right
        return index

    def calibrate(self, rows: Sequence[Outcome]) -> None:
        if not self.nodes:
            raise ValueError("fit the tree before calibration")
        groups: dict[int, list[Outcome]] = defaultdict(list)
        for row in rows:
            if not math.isfinite(row.net_bps) or not math.isfinite(row.stress_net_bps):
                raise ValueError("calibration outcomes must be finite")
            groups[self.leaf(row.opportunity.features)].append(row)
        self.leaves.clear()
        prior_count = self.config.shrinkage_samples
        for leaf, group in groups.items():
            count = len(group)
            denominator = count + prior_count
            self.leaves[leaf] = Prediction(
                (sum(row.net_bps > 0 for row in group) + prior_count * self.prior.probability)
                / denominator,
                (sum(row.net_bps for row in group) + prior_count * self.prior.net_bps)
                / denominator,
                (sum(row.stress_net_bps for row in group) + prior_count * self.prior.stress_net_bps)
                / denominator,
                count,
                count >= self.config.min_calibration_leaf,
            )

    def predict(self, features: Sequence[float]) -> Prediction:
        return self.leaves.get(self.leaf(features), self.prior)

    def to_record(self) -> dict[str, Any]:
        return {
            "config": asdict(self.config),
            "features": FEATURE_NAMES,
            "nodes": [asdict(node) for node in self.nodes],
            "leaves": {str(leaf): asdict(value) for leaf, value in self.leaves.items()},
            "fitting_prior": asdict(self.prior),
        }


def prediction_metrics(model: OutcomeTree, rows: Sequence[Outcome]) -> dict[str, Any]:
    if not rows:
        return {"samples": 0, "brier_score": None, "prior_brier_score": None}
    pairs = [(row, model.predict(row.opportunity.features)) for row in rows]
    buckets: dict[int, list[tuple[Outcome, Prediction]]] = defaultdict(list)
    for row, prediction in pairs:
        buckets[min(int(prediction.probability * 10), 9)].append((row, prediction))
    ece = sum(
        abs(sum(float(row.net_bps > 0) - pred.probability for row, pred in group))
        for group in buckets.values()
    ) / len(rows)
    return {
        "samples": len(rows),
        "actual_win_rate_pct": 100 * fmean(row.net_bps > 0 for row in rows),
        "brier_score": fmean(
            (pred.probability - float(row.net_bps > 0)) ** 2 for row, pred in pairs
        ),
        "prior_brier_score": fmean(
            (model.prior.probability - float(row.net_bps > 0)) ** 2 for row in rows
        ),
        "calibration_error": ece,
        "net_mae_bps": fmean(abs(row.net_bps - pred.net_bps) for row, pred in pairs),
        "prior_net_mae_bps": fmean(abs(row.net_bps - model.prior.net_bps) for row in rows),
        "supported_samples": sum(pred.supported for _, pred in pairs),
        "predicted_positive_samples": sum(pred.supported and pred.net_bps > 0 for _, pred in pairs),
        "unconditional_net_bps": fmean(row.net_bps for row in rows),
        "high_confidence_count": sum(pred.probability >= 0.7 for _, pred in pairs),
        "false_high_confidence_count": sum(
            pred.probability >= 0.7 and row.net_bps <= 0 for row, pred in pairs
        ),
        "reliability": [
            {
                "bin": key / 10,
                "samples": len(group),
                "mean_probability": fmean(pred.probability for _, pred in group),
                "actual_win_fraction": fmean(row.net_bps > 0 for row, _ in group),
            }
            for key, group in sorted(buckets.items())
        ],
    }
