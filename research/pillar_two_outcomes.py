"""Purged outcome learning, later calibration, threshold selection, and forward replay."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import fmean
from typing import Any

from bot.config import load_config
from bot.domain.events import Direction, PillarTwoSignal, ensure_utc
from bot.strategy.time_based import TimeBasedPillarTwoConfig
from research.archived_flow import read_archived_flow
from research.market_data import bars_quality, read_bars
from research.outcome_model import OutcomeModelConfig, OutcomeTree, prediction_metrics
from research.pillar_two_backtest import (
    BacktestResult,
    BacktestTrade,
    CandleBacktestConfig,
    CandleCosts,
    run_backtest,
)
from research.pillar_two_experiments import (
    CachedCandidateSource,
    Candidate,
    SignalCache,
    build_signal_cache,
    rolling_folds,
    summarize_folds,
)
from research.trade_outcomes import (
    FEATURE_NAMES,
    Opportunity,
    label_opportunity,
    mature_outcomes,
    opportunity_features,
    outcome_record,
)


@dataclass(frozen=True, slots=True)
class Threshold:
    min_net_bps: float
    min_probability: float

    def __post_init__(self) -> None:
        if not 0 <= self.min_probability <= 1 or not 0 <= self.min_net_bps < 10000:
            raise ValueError("invalid outcome threshold")


class OutcomeSource(CachedCandidateSource):
    def __init__(
        self,
        cache: SignalCache,
        candidate: Candidate,
        opportunities: Mapping[datetime, Opportunity],
        model: OutcomeTree | None,
        threshold: Threshold | None,
        scenario: str,
        *,
        baseline: bool = False,
    ) -> None:
        super().__init__(cache, candidate)
        self.opportunities = opportunities
        self.model = model
        self.threshold = threshold
        self.scenario = scenario
        self.baseline = baseline

    def signal(self, now: datetime) -> PillarTwoSignal:
        signal = super().signal(now)
        opportunity = self.opportunities.get(now)
        if opportunity is not None and self.baseline:
            return signal
        if opportunity is not None and self.model is not None and self.threshold is not None:
            prediction = self.model.predict(opportunity.features)
            net = prediction.net_bps if self.scenario == "base" else prediction.stress_net_bps
            if (
                prediction.supported
                and net > self.threshold.min_net_bps
                and prediction.probability >= self.threshold.min_probability
            ):
                return replace(
                    signal,
                    probability=prediction.probability,
                    expected_net_return_bps=net,
                    reasons=signal.reasons + ("learned_net_outcome",),
                )
        return replace(
            signal,
            direction=Direction.NEUTRAL,
            strength=0,
            score=0,
            expected_move_bps=0,
            expected_net_return_bps=None,
            reasons=signal.reasons + ("net_outcome_abstention",),
        )


def choose_threshold(
    selection: Sequence[tuple[Threshold, dict[str, BacktestResult]]],
    min_trades: int,
    min_profit_factor: float,
) -> Threshold | None:
    eligible: list[tuple[float, Threshold]] = []
    for threshold, scenarios in selection:
        if set(scenarios) != {"base", "stress"}:
            raise ValueError("threshold selection requires base and stress replay")
        metrics = [result.metrics for result in scenarios.values()]
        if all(
            m["trade_count"] >= min_trades
            and m["net_pnl"] > 0
            and not m["kill_switch_triggered"]
            and (m["profit_factor"] is None or m["profit_factor"] >= min_profit_factor)
            for m in metrics
        ):
            eligible.append(
                (min(m["net_return_on_initial_equity_pct"] for m in metrics), threshold)
            )
    if not eligible:
        return None
    return sorted(
        eligible, key=lambda item: (-item[0], -item[1].min_net_bps, -item[1].min_probability)
    )[0][1]


def _json(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported {type(value).__name__}")


def evaluate(input_path: Path, config_path: Path, output_dir: Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    if set(cfg) - {"use_archived_taker_volume"} != {
        "baseline_config",
        "development_end",
        "training_days",
        "calibration_days",
        "selection_days",
        "validation_days",
        "embargo_seconds",
        "opportunity_stride_seconds",
        "candidate",
        "model",
        "thresholds",
        "selection",
        "promotion",
    }:
        raise ValueError("outcome configuration contains missing or unknown keys")
    baseline_path = Path(cfg["baseline_config"])
    baseline = load_config(baseline_path)
    if baseline["symbol"] != "BTC_USDT":
        raise ValueError("outcome milestone is limited to BTC_USDT")
    end = ensure_utc(datetime.fromisoformat(cfg["development_end"]))
    engine_config = TimeBasedPillarTwoConfig(**baseline["engine"])
    candidate = Candidate(**cfg["candidate"])
    costs = CandleCosts(**baseline["costs"])
    stress = costs.stressed(Decimal(str(baseline["evaluation"]["stress_cost_multiplier"])))
    risk = replace(
        CandleBacktestConfig(**baseline["backtest"]),
        stop_bps=candidate.stop_bps,
        target_bps=candidate.target_bps,
        min_strength=candidate.min_strength,
    )
    model_config = OutcomeModelConfig(**cfg["model"])
    embargo = timedelta(seconds=cfg["embargo_seconds"])
    if embargo.total_seconds() < candidate.horizon_seconds:
        raise ValueError("embargo must cover the label horizon")
    if not (
        cfg["calibration_days"] > 0
        and cfg["selection_days"] > 0
        and cfg["training_days"] > cfg["calibration_days"] + cfg["selection_days"]
    ):
        raise ValueError("training must contain fitting, calibration, and selection periods")
    if cfg["selection"]["min_trades"] < 1 or cfg["selection"]["min_profit_factor"] < 1:
        raise ValueError("selection requires positive trade count and profit factor at least one")
    thresholds = [
        Threshold(float(edge), float(probability))
        for edge in cfg["thresholds"]["min_net_bps"]
        for probability in cfg["thresholds"]["min_probability"]
    ]
    if not thresholds or len(set(thresholds)) != len(thresholds):
        raise ValueError("thresholds must be present and unique")
    bars = [bar for bar in read_bars(input_path) if bar.end <= end]
    if not bars:
        raise ValueError("no bars within development period")
    folds = rolling_folds(
        bars[0].start,
        bars[-1].end,
        training=timedelta(days=cfg["training_days"]),
        validation=timedelta(days=cfg["validation_days"]),
        embargo=embargo,
    )
    if not folds:
        raise ValueError("no complete outcome evaluation folds")
    print(
        f"Building causal features and outcome labels on {len(bars)} development bars", flush=True
    )
    cache = build_signal_cache(bars, engine_config)
    flow = None
    flow_provenance = None
    if cfg.get("use_archived_taker_volume", False):
        flow, flow_provenance = read_archived_flow(input_path, bars)
    opportunities = opportunity_features(
        bars, cache, candidate, cfg["opportunity_stride_seconds"], flow
    )
    by_time = {row.decision_at: row for row in opportunities}
    outcomes = [
        label
        for row in opportunities
        if (label := label_opportunity(bars, row, candidate, costs, stress)) is not None
    ]
    print(
        f"{len(opportunities)} opportunities, {len(outcomes)} mature labels; {len(folds)} folds",
        flush=True,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    labels_path = output_dir / "outcomes.jsonl"
    with labels_path.open("w", encoding="utf-8", newline="\n") as handle:
        for outcome in outcomes:
            handle.write(json.dumps(outcome_record(outcome), allow_nan=False) + "\n")
    starts = [bar.start for bar in bars]
    ledgers: list[dict[str, Any]] = []
    fold_reports: list[dict[str, Any]] = []
    results: dict[str, dict[str, list[BacktestResult]]] = {
        name: {scenario: [] for scenario in ("base", "stress")}
        for name in ("baseline", "model_diagnostic", "selected")
    }

    def replay(
        start: datetime,
        finish: datetime,
        scenario: str,
        model: OutcomeTree | None,
        threshold: Threshold | None,
        *,
        benchmark: bool = False,
    ) -> BacktestResult:
        return run_backtest(
            bars[bisect_left(starts, start) : bisect_left(starts, finish)],
            symbol="BTC_USDT",
            engine_config=replace(engine_config, horizon_seconds=candidate.horizon_seconds),
            costs=costs if scenario == "base" else stress,
            config=risk,
            start=start,
            end=finish,
            source_factory=lambda: OutcomeSource(
                cache, candidate, by_time, model, threshold, scenario, baseline=benchmark
            ),
        )

    for index, fold in enumerate(folds, 1):
        calibration_end = fold.train_end - timedelta(days=cfg["selection_days"])
        calibration_start = calibration_end - timedelta(days=cfg["calibration_days"])
        fit_end = calibration_start - embargo
        selection_start = calibration_end + embargo
        if fit_end <= fold.train_start or selection_start >= fold.train_end:
            raise ValueError("purge leaves no fitting or selection window")
        fit = mature_outcomes(outcomes, fold.train_start, fit_end)
        calibration = mature_outcomes(outcomes, calibration_start, calibration_end)
        test_labels = mature_outcomes(outcomes, fold.test_start, fold.test_end)
        model: OutcomeTree | None = None
        selection: list[tuple[Threshold, dict[str, BacktestResult]]] = []
        selected: Threshold | None = None
        if len(fit) >= model_config.min_fit_samples:
            model = OutcomeTree(model_config)
            model.fit(fit)
            model.calibrate(calibration)
            selection = [
                (
                    threshold,
                    {
                        scenario: replay(
                            selection_start, fold.train_end, scenario, model, threshold
                        )
                        for scenario in ("base", "stress")
                    },
                )
                for threshold in thresholds
            ]
            selected = choose_threshold(selection, **cfg["selection"])
        fold_result: dict[str, Any] = {}
        for name in results:
            fold_result[name] = {}
            for scenario in ("base", "stress"):
                threshold = selected if name == "selected" else Threshold(2, 0)
                result = replay(
                    fold.test_start,
                    fold.test_end,
                    scenario,
                    model,
                    threshold,
                    benchmark=name == "baseline",
                )
                results[name][scenario].append(result)
                fold_result[name][scenario] = result.metrics
                for trade in result.trades:
                    ledgers.append(
                        {"fold": index, "strategy": name, "scenario": scenario, **asdict(trade)}
                    )
        fold_reports.append(
            {
                "fold": index,
                **asdict(fold),
                "fit_end": fit_end,
                "calibration_start": calibration_start,
                "calibration_end": calibration_end,
                "selection_start": selection_start,
                "fit_samples": len(fit),
                "calibration_samples": len(calibration),
                "last_fit_label_known_at": max((row.known_at for row in fit), default=None),
                "last_calibration_label_known_at": max(
                    (row.known_at for row in calibration), default=None
                ),
                "selected_threshold": asdict(selected) if selected else None,
                "selection": [
                    {
                        "threshold": asdict(threshold),
                        "metrics": {
                            scenario: result.metrics for scenario, result in values.items()
                        },
                    }
                    for threshold, values in selection
                ],
                "model": model.to_record() if model is not None else None,
                "prediction_validation": prediction_metrics(model, test_labels) if model else None,
                "validation": fold_result,
            }
        )
        print(
            f"Fold {index}/{len(folds)}: fit={len(fit)}, calibration={len(calibration)}, "
            f"selected={selected or 'CASH'}",
            flush=True,
        )
    summaries = {
        name: {scenario: summarize_folds(values) for scenario, values in scenarios.items()}
        for name, scenarios in results.items()
    }
    selection_summary = summaries["selected"]
    passed = all(
        values["trade_count"] >= cfg["promotion"]["min_validation_trades"]
        and values["net_pnl"] > 0
        and not values["halted_folds"]
        and values["positive_folds"] / len(folds) >= cfg["promotion"]["min_positive_fold_fraction"]
        for values in selection_summary.values()
    )
    source_paths = [
        Path(__file__),
        Path("research/trade_outcomes.py"),
        Path("research/outcome_model.py"),
        Path("research/pillar_two_experiments.py"),
        Path("research/pillar_two_backtest.py"),
        Path("bot/strategy/time_based.py"),
        Path("bot/domain/events.py"),
        Path("research/market_data.py"),
        Path("research/archived_flow.py"),
    ]
    report: dict[str, Any] = {
        "mode": "pillar_two_net_outcome_development",
        "llm_used": False,
        "verdict": "ready_for_new_holdout" if passed else "no_validated_net_edge",
        "dataset": {
            "archived_taker_volume": flow_provenance,
            "quality": bars_quality(bars),
            "opportunities": len(opportunities),
            "mature_outcomes": len(outcomes),
            "unresolved_or_gapped_outcomes": len(opportunities) - len(outcomes),
            "features": FEATURE_NAMES,
            "mean_label_net_bps": fmean(row.net_bps for row in outcomes) if outcomes else None,
            "outcomes_sha256": hashlib.sha256(labels_path.read_bytes()).hexdigest(),
        },
        "summaries": summaries,
        "folds": fold_reports,
        "provenance": {
            "config": cfg,
            "baseline": baseline,
            "resolved_engine": asdict(engine_config),
            "resolved_risk": asdict(risk),
            "resolved_model": asdict(model_config),
            "resolved_costs": asdict(costs),
            "resolved_stress_costs": asdict(stress),
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "source_sha256": {
                str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths
            },
        },
        "limitations": [
            "Development reuse: this model follows earlier experiments on these periods.",
            "Counterfactual labels overlap; their count is not an independent trade sample size.",
            "Label splits purge both actual exits and full possible holding horizons.",
            "Features use completed candles, with no executable quotes or microstructure.",
            "Leaf probabilities and returns are empirical estimates with uncertain accuracy.",
            "Probabilities target a base-cost net win; stress gating uses stress net expectancy.",
            "Thresholds are chosen on the later selection segment, before validation starts.",
            "Diagnostic model uses predicted net >2 bps, probability>=0; it is not promoted.",
            "Unsupported models and failed selection cause abstention; zero PnL is not edge.",
            "Fold accounts reset: mean fold return is not a continuous account return.",
            "Fresh final holdout and target-venue trade/quote execution checks remain required.",
        ],
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, default=_json, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    with (output_dir / "validation_trades.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["fold", "strategy", "scenario"] + list(BacktestTrade.__dataclass_fields__),
        )
        writer.writeheader()
        writer.writerows(ledgers)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=Path("configs/pillar_two_outcomes.yaml"))
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    report = evaluate(args.input, args.config, args.output_dir)
    print(json.dumps({"verdict": report["verdict"], "summaries": report["summaries"]}, indent=2))


if __name__ == "__main__":
    main()
