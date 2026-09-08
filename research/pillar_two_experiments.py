"""Development-only, chronological Pillar Two hypothesis comparisons.

Candidate and fold selection is research, never live configuration promotion.
Every fold selects using earlier training results; an unqualified fold stays flat.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from bisect import bisect_left
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.config import load_config
from bot.domain.events import Direction, PillarTwoSignal, ensure_utc
from bot.features.bars import Bar
from bot.strategy.time_based import TimeBasedPillarTwoConfig, TimeBasedPillarTwoEngine
from research.market_data import bars_quality, read_bars
from research.pillar_two_backtest import (
    BacktestResult,
    BacktestTrade,
    CandleBacktestConfig,
    CandleCosts,
    _trade_metrics,
    run_backtest,
)
from research.walk_forward import WalkForwardFold


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    horizon_seconds: int
    stop_bps: Decimal
    target_bps: Decimal
    min_strength: float
    models: tuple[str, ...]
    confirmation_interval_seconds: int = 0

    def __post_init__(self) -> None:
        if not self.name or not all(c.isalnum() or c == "_" for c in self.name):
            raise ValueError("candidate name must use letters, digits, or underscores")
        if type(self.horizon_seconds) is not int or self.horizon_seconds <= 0:
            raise ValueError("candidate horizon must be a positive integer")
        if self.confirmation_interval_seconds not in (0, 300):
            raise ValueError("only no confirmation or closed five-minute confirmation is supported")
        if not self.models or set(self.models) - {"trend", "breakout", "mean_reversion"}:
            raise ValueError("unknown or empty candidate models")
        if len(set(self.models)) != len(self.models):
            raise ValueError("candidate models must be unique")
        object.__setattr__(self, "models", tuple(self.models))
        for name in ("stop_bps", "target_bps"):
            value = Decimal(str(getattr(self, name)))
            if not value.is_finite() or not 0 < value < 10000:
                raise ValueError("stop and target must be finite and within (0, 10000)")
            object.__setattr__(self, name, value)
        if not math.isfinite(self.min_strength) or not 0 <= self.min_strength <= 1:
            raise ValueError("min_strength must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    min_training_trades: int = 30
    min_training_profit_factor: float = 1.1
    require_positive_stress: bool = True
    min_validation_trades: int = 100
    min_positive_fold_fraction: float = 0.6

    def __post_init__(self) -> None:
        for count in (self.min_training_trades, self.min_validation_trades):
            if type(count) is not int or count <= 0:
                raise ValueError("trade minimums must be positive integers")
        if type(self.require_positive_stress) is not bool:
            raise ValueError("require_positive_stress must be a boolean")
        if (
            not math.isfinite(self.min_training_profit_factor)
            or self.min_training_profit_factor < 1
        ):
            raise ValueError("training profit factor must be finite and at least one")
        if not math.isfinite(self.min_positive_fold_fraction) or not (
            0 < self.min_positive_fold_fraction <= 1
        ):
            raise ValueError("positive fold fraction must be within (0, 1]")


def rolling_folds(
    start: datetime,
    end: datetime,
    *,
    training: timedelta,
    validation: timedelta,
    embargo: timedelta,
) -> list[WalkForwardFold]:
    start, end = ensure_utc(start), ensure_utc(end)
    if start >= end or min(training, validation) <= timedelta(0) or embargo < timedelta(0):
        raise ValueError("invalid rolling-fold boundaries")
    folds: list[WalkForwardFold] = []
    train_end = start + training
    while train_end + embargo + validation <= end:
        test_start = train_end + embargo
        folds.append(
            WalkForwardFold(train_end - training, train_end, test_start, test_start + validation)
        )
        train_end += validation
    return folds


def resample_complete_bars(bars: Sequence[Bar], interval_seconds: int) -> list[Bar]:
    """Aggregate only contiguous, aligned, complete groups; never fill a gap."""
    if not bars or interval_seconds < bars[0].interval_seconds:
        raise ValueError("resampling needs bars and an interval at least their duration")
    base = bars[0].interval_seconds
    if interval_seconds % base:
        raise ValueError("resampling interval must be an exact multiple")
    working: list[Bar] = []
    output: list[Bar] = []
    previous: Bar | None = None
    for bar in bars:
        if bar.symbol != bars[0].symbol or bar.interval_seconds != base:
            raise ValueError("resampling requires one symbol and interval")
        if previous is not None and bar.start < previous.end:
            raise ValueError("resampling requires unique chronological bars")
        if previous is not None and bar.start != previous.end:
            working.clear()
        if int(bar.start.timestamp()) % interval_seconds == 0:
            working = [bar]
        elif working:
            working.append(bar)
        if working and len(working) == interval_seconds // base:
            first, last = working[0], working[-1]
            output.append(
                Bar(
                    first.symbol,
                    interval_seconds,
                    first.start,
                    last.end,
                    first.open,
                    max(item.high for item in working),
                    min(item.low for item in working),
                    last.close,
                    sum((item.volume for item in working), Decimal(0)),
                    sum(item.trades for item in working),
                )
            )
            working.clear()
        previous = bar
    return output


@dataclass(frozen=True, slots=True)
class SignalCache:
    config: TimeBasedPillarTwoConfig
    signals: Mapping[datetime, PillarTwoSignal]
    confirmations: Mapping[datetime, Direction]


def build_signal_cache(bars: Sequence[Bar], config: TimeBasedPillarTwoConfig) -> SignalCache:
    """One forward pass. A cache lookup exposes only the last consumed bar's close."""
    if not bars or config.interval_seconds != 60:
        raise ValueError("experiments require one-minute input")
    engine = TimeBasedPillarTwoEngine(bars[0].symbol, config)
    slow = TimeBasedPillarTwoEngine(
        bars[0].symbol,
        replace(
            config,
            interval_seconds=300,
            horizon_seconds=max(300, config.horizon_seconds),
            signal_ttl_seconds=300,
        ),
    )
    complete_slow = {bar.end: bar for bar in resample_complete_bars(bars, 300)}
    signals: dict[datetime, PillarTwoSignal] = {}
    confirmations: dict[datetime, Direction] = {}
    latest: PillarTwoSignal | None = None
    for bar in bars:
        engine.on_bar(bar)
        signals[bar.end] = engine.signal(bar.end)
        if bar.end in complete_slow:
            slow.on_bar(complete_slow[bar.end])
            latest = slow.signal(bar.end)
        confirmations[bar.end] = (
            latest.direction
            if latest is not None and latest.model == "trend" and latest.is_fresh(bar.end)
            else Direction.NEUTRAL
        )
    return SignalCache(config, signals, confirmations)


class CachedCandidateSource:
    def __init__(self, cache: SignalCache, candidate: Candidate) -> None:
        if candidate.horizon_seconds % cache.config.interval_seconds:
            raise ValueError("candidate horizon must be aligned to candles")
        if (
            "mean_reversion" in candidate.models
            and candidate.horizon_seconds != cache.config.horizon_seconds
        ):
            raise ValueError("mean-reversion movement cannot be rescaled from the cached horizon")
        self.cache = cache
        self.candidate = candidate
        self.last_end: datetime | None = None

    def on_bar(self, bar: Bar) -> None:
        known = self.cache.signals.get(bar.end)
        if known is None or bar.symbol != known.symbol:
            raise ValueError("bar is outside the causal signal cache")
        if self.last_end is not None and bar.end <= self.last_end:
            raise ValueError("cached replay must be chronological")
        self.last_end = bar.end

    def signal(self, now: datetime) -> PillarTwoSignal:
        if now != self.last_end:
            raise ValueError("cache only exposes the last consumed closed bar")
        signal = self.cache.signals[now]
        allowed = signal.model in self.candidate.models
        if self.candidate.confirmation_interval_seconds:
            allowed = allowed and self.cache.confirmations[now] == signal.direction
        if not allowed:
            return replace(
                signal,
                direction=Direction.NEUTRAL,
                strength=0,
                score=0,
                expected_move_bps=0,
                reasons=signal.reasons + ("candidate_filter",),
            )
        # Trend/breakout horizons enter the original engine only through sqrt(horizon).
        # This is exact for those models; range deviation has a cap and is excluded above.
        move = signal.expected_move_bps
        if signal.model in {"trend", "breakout"}:
            move *= math.sqrt(self.candidate.horizon_seconds / self.cache.config.horizon_seconds)
        return replace(
            signal, expected_move_bps=move, horizon_seconds=self.candidate.horizon_seconds
        )


def select_candidate(
    training: Mapping[str, Mapping[str, dict[str, Any]]],
    policy: SelectionPolicy,
) -> str | None:
    """Ranks earlier training evidence only. No validation argument is accepted."""
    eligible: list[tuple[float, str]] = []
    for name, scenarios in training.items():
        base, stress = scenarios["base"], scenarios["stress"]
        factor = base["profit_factor"]
        if (
            base["trade_count"] < policy.min_training_trades
            or base["net_pnl"] <= 0
            or base["kill_switch_triggered"]
            or (factor is not None and factor < policy.min_training_profit_factor)
        ):
            continue
        if policy.require_positive_stress and (
            stress["net_pnl"] <= 0
            or stress["trade_count"] < policy.min_training_trades
            or stress["kill_switch_triggered"]
        ):
            continue
        eligible.append(
            (
                min(
                    base["net_return_on_initial_equity_pct"],
                    stress["net_return_on_initial_equity_pct"],
                ),
                name,
            )
        )
    return sorted(eligible, key=lambda item: (-item[0], item[1]))[0][1] if eligible else None


def summarize_folds(results: Sequence[BacktestResult]) -> dict[str, Any]:
    """Aggregate trade evidence; fold accounts reset, so never claim a continuous return."""
    trades = [trade for result in results for trade in result.trades]
    metrics = _trade_metrics(trades)
    returns = [result.metrics["net_return_on_initial_equity_pct"] for result in results]
    metrics.update(
        {
            "fold_count": len(results),
            "positive_folds": sum(value > 0 for value in returns),
            "mean_fold_return_pct": sum(returns) / len(returns) if returns else 0.0,
            "worst_fold_return_pct": min(returns) if returns else 0.0,
            "worst_fold_drawdown_pct": max(
                (result.metrics["max_mtm_drawdown_pct"] for result in results),
                default=0.0,
            ),
            "halted_folds": sum(result.metrics["kill_switch_triggered"] for result in results),
            "accounting": "independent equal-starting-equity fold accounts; not continuous PnL",
        }
    )
    return metrics


def robust_candidates(
    summaries: Mapping[str, Mapping[str, dict[str, Any]]],
    policy: SelectionPolicy,
) -> list[str]:
    eligible: list[str] = []
    for name, scenarios in summaries.items():
        if set(scenarios) != {"base", "stress"}:
            raise ValueError("robustness requires both base and stress results")
        if all(
            values["trade_count"] >= policy.min_validation_trades
            and values["net_pnl"] > 0
            and values["positive_folds"] / max(1, values["fold_count"])
            >= policy.min_positive_fold_fraction
            and not values["halted_folds"]
            for values in scenarios.values()
        ):
            eligible.append(name)
    return sorted(
        eligible,
        key=lambda name: (
            -min(
                summaries[name][scenario]["mean_fold_return_pct"] for scenario in ("base", "stress")
            ),
            name,
        ),
    )


def run_experiments(
    bars: Sequence[Bar],
    *,
    engine_config: TimeBasedPillarTwoConfig,
    costs: CandleCosts,
    backtest_config: CandleBacktestConfig,
    candidates: Sequence[Candidate],
    development_end: datetime,
    training: timedelta,
    validation: timedelta,
    embargo: timedelta,
    policy: SelectionPolicy,
    stress_multiplier: Decimal = Decimal(2),
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    development_end = ensure_utc(development_end)
    if not candidates or len({candidate.name for candidate in candidates}) != len(candidates):
        raise ValueError("candidate names must be present and unique")
    if embargo.total_seconds() < max(candidate.horizon_seconds for candidate in candidates):
        raise ValueError("embargo must cover the longest candidate holding horizon")
    development = [bar for bar in bars if bar.end <= development_end]
    if not development:
        raise ValueError("no development bars before cutoff")
    folds = rolling_folds(
        development[0].start,
        development[-1].end,
        training=training,
        validation=validation,
        embargo=embargo,
    )
    if not folds:
        raise ValueError("not enough development history for a complete fold")
    if progress:
        progress(
            f"Caching causal signals for {len(development)} development bars; {len(folds)} folds"
        )
    cache = build_signal_cache(development, engine_config)
    starts = [bar.start for bar in development]
    scenario_costs = {"base": costs, "stress": costs.stressed(stress_multiplier)}
    validation_results: dict[str, dict[str, list[BacktestResult]]] = {
        candidate.name: {scenario: [] for scenario in scenario_costs} for candidate in candidates
    }
    selected_results: dict[str, list[BacktestResult]] = {
        scenario: [] for scenario in scenario_costs
    }
    fold_records: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []

    def evaluate(
        candidate: Candidate, start: datetime, end: datetime, scenario: str
    ) -> BacktestResult:
        # Cache has already warmed from preceding history; no later bar is exposed by its source.
        subset = development[bisect_left(starts, start) : bisect_left(starts, end)]
        return run_backtest(
            subset,
            symbol=development[0].symbol,
            engine_config=replace(engine_config, horizon_seconds=candidate.horizon_seconds),
            costs=scenario_costs[scenario],
            config=replace(
                backtest_config,
                stop_bps=candidate.stop_bps,
                target_bps=candidate.target_bps,
                min_strength=candidate.min_strength,
            ),
            start=start,
            end=end,
            source_factory=lambda: CachedCandidateSource(cache, candidate),
        )

    for index, fold in enumerate(folds):
        train_metrics: dict[str, dict[str, dict[str, Any]]] = {}
        for candidate in candidates:
            train_metrics[candidate.name] = {
                scenario: evaluate(candidate, fold.train_start, fold.train_end, scenario).metrics
                for scenario in scenario_costs
            }
        selected = select_candidate(train_metrics, policy)
        fold_validation: dict[str, dict[str, dict[str, Any]]] = {}
        for candidate in candidates:
            fold_validation[candidate.name] = {}
            for scenario in scenario_costs:
                result = evaluate(candidate, fold.test_start, fold.test_end, scenario)
                validation_results[candidate.name][scenario].append(result)
                fold_validation[candidate.name][scenario] = result.metrics
                if candidate.name == selected:
                    selected_results[scenario].append(result)
                for trade in result.trades:
                    ledger.append(
                        {
                            "fold": index + 1,
                            "candidate": candidate.name,
                            "scenario": scenario,
                            "selected": candidate.name == selected,
                            **asdict(trade),
                        }
                    )
        fold_records.append(
            {
                "fold": index + 1,
                **asdict(fold),
                "selected_from_training": selected,
                "selection_reason": "training_gate_passed"
                if selected
                else "no_qualified_candidate",
                "training": train_metrics,
                "validation": fold_validation,
            }
        )
        if progress:
            progress(f"Fold {index + 1}/{len(folds)}: selected {selected or 'CASH'} from training")
    summaries = {
        name: {scenario: summarize_folds(results) for scenario, results in scenarios.items()}
        for name, scenarios in validation_results.items()
    }
    qualified = robust_candidates(summaries, policy)
    selected_summaries = {}
    for scenario, results in selected_results.items():
        summary = summarize_folds(results)
        summary["traded_folds"] = len(results)
        summary["cash_folds"] = len(folds) - len(results)
        summary["mean_fold_return_pct"] *= len(results) / len(folds)
        if summary["cash_folds"]:
            summary["worst_fold_return_pct"] = min(0, summary["worst_fold_return_pct"])
        summary["fold_count"] = len(folds)
        selected_summaries[scenario] = summary
    return {
        "mode": "pillar_two_development_walk_forward",
        "llm_used": False,
        "development_end_exclusive": development_end,
        "data_quality": bars_quality(development),
        "candidates": [asdict(candidate) for candidate in candidates],
        "selection_policy": asdict(policy),
        "folds": fold_records,
        "candidate_validation_summaries": summaries,
        "training_selected_validation": selected_summaries,
        "qualified_for_new_holdout": qualified,
        "selected_for_new_holdout": qualified[0] if qualified else None,
        "verdict": "candidate_needs_new_holdout" if qualified else "no_robust_candidate",
        "limitations": [
            "Hypotheses follow an observed baseline; this is development research.",
            "Only bars ending before the explicit development cutoff enter features or selection.",
            "Training windows overlap; validation windows are disjoint with a horizon embargo.",
            "Each fold resets equity and risk state; average fold return is not continuous return.",
            "All-candidate validation is exploratory and subject to multiple comparisons.",
            "A selected development candidate still requires new untouched market data.",
            "No candidate passing the training gates means no entry; cash is not evidence of edge.",
            "Five-minute confirmation uses complete contiguous bars; missing groups are dropped.",
            "Expected movement is a volatility heuristic; probabilities are not calibrated.",
            "Candles contain no executable quotes, funding, order flow, depth, or latency.",
        ],
    }, ledger


def _serialize(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def evaluate_experiment_file(
    input_path: Path, config_path: Path, output_dir: Path
) -> dict[str, Any]:
    experiment = load_config(config_path)
    if set(experiment) != {
        "baseline_config",
        "development_end",
        "training_days",
        "validation_days",
        "embargo_seconds",
        "selection",
        "candidates",
    }:
        raise ValueError("experiment configuration has missing or unknown keys")
    baseline_path = Path(experiment["baseline_config"])
    baseline = load_config(baseline_path)
    if baseline.get("symbol") != "BTC_USDT":
        raise ValueError("experiment is limited to BTC_USDT")
    bars = read_bars(input_path)
    report, ledger = run_experiments(
        bars,
        engine_config=TimeBasedPillarTwoConfig(**baseline["engine"]),
        costs=CandleCosts(**baseline["costs"]),
        backtest_config=CandleBacktestConfig(**baseline["backtest"]),
        candidates=[Candidate(**value) for value in experiment["candidates"]],
        development_end=datetime.fromisoformat(experiment["development_end"]),
        training=timedelta(days=experiment["training_days"]),
        validation=timedelta(days=experiment["validation_days"]),
        embargo=timedelta(seconds=experiment["embargo_seconds"]),
        policy=SelectionPolicy(**experiment["selection"]),
        stress_multiplier=Decimal(str(baseline["evaluation"]["stress_cost_multiplier"])),
        progress=lambda message: print(message, flush=True),
    )
    source_paths = [
        Path(__file__),
        Path("research/pillar_two_backtest.py"),
        Path("bot/strategy/time_based.py"),
        Path("research/market_data.py"),
    ]
    report["provenance"] = {
        "input": str(input_path),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "experiment": experiment,
        "experiment_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "baseline": baseline,
        "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "resolved_engine": asdict(TimeBasedPillarTwoConfig(**baseline["engine"])),
        "resolved_backtest": asdict(CandleBacktestConfig(**baseline["backtest"])),
        "source_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, default=_serialize, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "validation_trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["fold", "candidate", "scenario", "selected"]
            + list(BacktestTrade.__dataclass_fields__),
        )
        writer.writeheader()
        writer.writerows(ledger)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/pillar_two_experiments.yaml"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_experiment_file(args.input, args.config, args.output_dir)
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "selected_for_new_holdout": report["selected_for_new_holdout"],
                "training_selected_validation": report["training_selected_validation"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
