"""Registered signal-design comparison with chronological selection and net-cost replay."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import fmean
from typing import Any

from bot.config import load_config
from bot.domain.events import Direction, PillarTwoSignal, ensure_utc
from bot.strategy.time_based import TimeBasedPillarTwoConfig
from research.archived_flow import read_archived_flow
from research.market_data import bars_quality, provenance_path, read_bars
from research.pillar_two_backtest import (
    BacktestResult,
    BacktestTrade,
    CandleBacktestConfig,
    CandleCosts,
    SignalSource,
    run_backtest,
)
from research.pillar_two_experiments import (
    CachedCandidateSource,
    Candidate,
    SelectionPolicy,
    build_signal_cache,
    robust_candidates,
    rolling_folds,
    select_candidate,
    summarize_folds,
)
from research.signal_designs import (
    ARCHITECTURES,
    FAMILIES,
    DesignSource,
    FeatureSettings,
    build_snapshots,
)


@dataclass(frozen=True, slots=True)
class DesignCandidate:
    name: str
    design: str
    horizon_seconds: int
    stop_bps: Decimal
    target_bps: Decimal

    def __post_init__(self) -> None:
        if self.design not in FAMILIES + ARCHITECTURES + ("previous_baseline",):
            raise ValueError("unknown candidate design")
        if type(self.horizon_seconds) is not int or self.horizon_seconds <= 0:
            raise ValueError("horizon must be a positive integer")
        if self.horizon_seconds % 60:
            raise ValueError("horizon must be aligned to minute bars")
        for name in ("stop_bps", "target_bps"):
            value = Decimal(str(getattr(self, name)))
            if not value.is_finite() or not 0 < value < 10000:
                raise ValueError("invalid protective-exit distance")
            object.__setattr__(self, name, value)


def block_diagnostics(
    series: Mapping[str, Sequence[float]],
    *,
    draws: int,
    fold_block_length: int,
    seed: int,
) -> dict[str, Any]:
    """Exploratory common-index circular block bootstrap of fold returns.

    The maximum centered mean considers the current registered family together.
    This is not a formal PBO estimate, nor correction for unknown prior trials.
    """
    lengths = {len(values) for values in series.values()}
    if len(lengths) != 1 or not series or not next(iter(lengths)):
        raise ValueError("bootstrap requires equally sized nonempty series")
    n = next(iter(lengths))
    if draws < 100 or not 1 <= fold_block_length <= n:
        raise ValueError("invalid bootstrap draws or block length")
    if any(not all(math.isfinite(x) for x in values) for values in series.values()):
        raise ValueError("bootstrap returns must be finite")
    means = {name: fmean(values) for name, values in series.items()}
    samples: dict[str, list[float]] = {name: [] for name in series}
    centered_max: list[float] = []
    rng = random.Random(seed)
    for _ in range(draws):
        indices: list[int] = []
        while len(indices) < n:
            start = rng.randrange(n)
            indices.extend((start + j) % n for j in range(fold_block_length))
        indices = indices[:n]
        values = {name: fmean(row[i] for i in indices) for name, row in series.items()}
        for name, mean in values.items():
            samples[name].append(mean)
        centered_max.append(max(values[name] - means[name] for name in series))
    intervals = {}
    for name, draws_for_name in samples.items():
        draws_for_name.sort()
        intervals[name] = [
            draws_for_name[int(0.025 * draws)],
            draws_for_name[min(draws - 1, int(0.975 * draws))],
        ]
    observed = max(0.0, max(means.values()))
    return {
        "draws": draws,
        "fold_block_length": fold_block_length,
        "seed": seed,
        "series_count": len(series),
        "fold_count": n,
        "mean_fold_return_95_interval_pct": intervals,
        "observed_best_mean_fold_return_pct": max(means.values()),
        "centered_max_mean_tail_fraction": (1 + sum(value >= observed for value in centered_max))
        / (draws + 1),
        "limitations": [
            "Few folds; approximate dependence/stationarity assumptions may fail.",
            "Intervals are descriptive, not simultaneous confidence bounds.",
            "Maximum comparison covers this registry only, not earlier project searches.",
            "This diagnostic is not an exact implementation of White's Reality Check or PBO.",
        ],
    }


def _json(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported report value: {type(value).__name__}")


def evaluate(
    futures_path: Path, spot_path: Path, config_path: Path, output_dir: Path
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError("use a new output directory; prior registrations must be preserved")
    cfg = load_config(config_path)
    required = {
        "baseline_config",
        "development_end",
        "training_days",
        "validation_days",
        "embargo_seconds",
        "decision_stride_seconds",
        "families",
        "architectures",
        "exits",
        "features",
        "selection",
        "bootstrap",
    }
    if set(cfg) != required or cfg["families"] != list(FAMILIES):
        raise ValueError("study configuration must contain the full registered family")
    if cfg["architectures"] != list(ARCHITECTURES):
        raise ValueError("study requires router and consensus designs")
    baseline_path = Path(cfg["baseline_config"])
    baseline = load_config(baseline_path)
    if baseline["symbol"] != "BTC_USDT":
        raise ValueError("study is limited to BTC_USDT")
    settings, policy = FeatureSettings(**cfg["features"]), SelectionPolicy(**cfg["selection"])
    end = ensure_utc(datetime.fromisoformat(cfg["development_end"]))
    engine = TimeBasedPillarTwoConfig(**baseline["engine"])
    risk = CandleBacktestConfig(**baseline["backtest"])
    costs = CandleCosts(**baseline["costs"])
    scenarios = {
        "fees_only": replace(costs, spread_bps=Decimal(0), slippage_bps_per_side=Decimal(0)),
        "base": costs,
        "stress": costs.stressed(Decimal(2)),
    }
    candidates = [
        DesignCandidate(
            f"{family}_{exit_cfg['name']}",
            family,
            exit_cfg["horizon_seconds"],
            exit_cfg["stop_bps"],
            exit_cfg["target_bps"],
        )
        for family in FAMILIES + ARCHITECTURES
        for exit_cfg in cfg["exits"]
    ]
    candidates.append(
        DesignCandidate(
            "previous_baseline_60m",
            "previous_baseline",
            3600,
            Decimal(80),
            Decimal(120),
        )
    )
    if len({candidate.name for candidate in candidates}) != len(candidates):
        raise ValueError("candidate names must be unique")
    if cfg["embargo_seconds"] < max(candidate.horizon_seconds for candidate in candidates):
        raise ValueError("embargo must cover the longest holding horizon")
    input_metadata = {}
    for label, path, source in (
        ("futures", futures_path, "binance_um_klines"),
        ("spot", spot_path, "binance_spot_klines"),
    ):
        manifest = json.loads(provenance_path(path).read_text(encoding="utf-8"))
        if manifest.get("source") != source:
            raise ValueError(f"{label} input must identify its correct market")
        input_metadata[label] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "provenance": manifest,
        }
    futures = [bar for bar in read_bars(futures_path) if bar.end <= end]
    spot = [bar for bar in read_bars(spot_path) if bar.end <= end]
    if not futures or not spot:
        raise ValueError("no development bars")
    folds = rolling_folds(
        futures[0].start,
        futures[-1].end,
        training=timedelta(days=cfg["training_days"]),
        validation=timedelta(days=cfg["validation_days"]),
        embargo=timedelta(seconds=cfg["embargo_seconds"]),
    )
    if not folds:
        raise ValueError("no complete development folds")
    source_paths = (
        "research/signal_designs.py",
        "research/signal_design_study.py",
        "research/pillar_two_backtest.py",
        "research/pillar_two_experiments.py",
        "research/market_data.py",
        "research/archived_flow.py",
        "bot/strategy/time_based.py",
        "bot/domain/events.py",
        "bot/features/bars.py",
        "bot/config.py",
    )
    registration = {
        "registered_at": datetime.now(UTC).isoformat(),
        "performance_inspected": False,
        "config": cfg,
        "baseline": baseline,
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "source_sha256": {
            path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in source_paths
        },
        "inputs": input_metadata,
        "candidates": [asdict(candidate) for candidate in candidates],
        "resolved_costs": {name: asdict(value) for name, value in scenarios.items()},
        "resolved_risk": asdict(risk),
        "resolved_engine": asdict(engine),
        "limitations": [
            "Registration precedes this run, not earlier project exposure to development data.",
            "All parameters are research choices, not published profitable trading rules.",
        ],
    }
    output_dir.mkdir(parents=True)
    registration_path = output_dir / "registration.json"
    registration_path.write_text(
        json.dumps(registration, default=_json, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Registered {len(candidates)} candidates before evaluating performance", flush=True)
    flow, flow_provenance = read_archived_flow(futures_path, futures)
    snapshots = build_snapshots(futures, spot, flow, settings, cfg["decision_stride_seconds"])
    cache = build_signal_cache(futures, engine)
    starts = [bar.start for bar in futures]
    all_results: dict[str, dict[str, list[BacktestResult]]] = {
        candidate.name: {scenario: [] for scenario in scenarios} for candidate in candidates
    }
    selected_results: dict[str, list[BacktestResult]] = {scenario: [] for scenario in scenarios}
    fold_reports: list[dict[str, Any]] = []
    ledgers: list[dict[str, Any]] = []

    class ScheduledBaseline(CachedCandidateSource):
        def signal(self, now: datetime) -> PillarTwoSignal:
            signal = super().signal(now)
            if int(now.timestamp()) % cfg["decision_stride_seconds"]:
                return replace(signal, direction=Direction.NEUTRAL, strength=0, score=0)
            return signal

    def replay(
        candidate: DesignCandidate, start: datetime, finish: datetime, scenario: str
    ) -> BacktestResult:
        def source() -> SignalSource:
            if candidate.design == "previous_baseline":
                return ScheduledBaseline(
                    cache,
                    Candidate(
                        candidate.name,
                        candidate.horizon_seconds,
                        candidate.stop_bps,
                        candidate.target_bps,
                        0.7,
                        ("trend", "breakout"),
                    ),
                )
            return DesignSource(snapshots, settings, candidate.design, candidate.horizon_seconds)

        return run_backtest(
            futures[bisect_left(starts, start) : bisect_left(starts, finish)],
            symbol="BTC_USDT",
            engine_config=replace(engine, horizon_seconds=candidate.horizon_seconds),
            costs=scenarios[scenario],
            config=replace(
                risk,
                stop_bps=candidate.stop_bps,
                target_bps=candidate.target_bps,
                min_strength=0.7 if candidate.design == "previous_baseline" else 0.5,
            ),
            start=start,
            end=finish,
            source_factory=source,
        )

    for index, fold in enumerate(folds, 1):
        training: dict[str, dict[str, dict[str, Any]]] = {}
        for candidate in candidates:
            training[candidate.name] = {
                scenario: replay(candidate, fold.train_start, fold.train_end, scenario).metrics
                for scenario in ("base", "stress")
            }
        selected = select_candidate(training, policy)
        validation: dict[str, dict[str, dict[str, Any]]] = {}
        for candidate in candidates:
            validation[candidate.name] = {}
            for scenario in scenarios:
                result = replay(candidate, fold.test_start, fold.test_end, scenario)
                all_results[candidate.name][scenario].append(result)
                validation[candidate.name][scenario] = result.metrics
                if candidate.name == selected:
                    selected_results[scenario].append(result)
                ledgers.extend(
                    {
                        "fold": index,
                        "candidate": candidate.name,
                        "scenario": scenario,
                        "selected": candidate.name == selected,
                        **asdict(trade),
                    }
                    for trade in result.trades
                )
        if selected is None:
            # Flat results preserve the full thirteen-fold denominator.
            for scenario in scenarios:
                flat = BacktestResult(
                    trades=[],
                    metrics={
                        "net_return_on_initial_equity_pct": 0.0,
                        "max_mtm_drawdown_pct": 0.0,
                        "kill_switch_triggered": False,
                    },
                    risk_events=[],
                )
                selected_results[scenario].append(flat)
        fold_reports.append(
            {
                "fold": index,
                **asdict(fold),
                "selected_from_training": selected,
                "training": training,
                "validation": validation,
            }
        )
        print(f"Fold {index}/{len(folds)}: earlier-data selector={selected or 'CASH'}", flush=True)
    summaries = {
        name: {scenario: summarize_folds(results) for scenario, results in by_scenario.items()}
        for name, by_scenario in all_results.items()
    }
    selected_summary = {
        name: summarize_folds(results) for name, results in selected_results.items()
    }
    qualified = robust_candidates(
        {
            name: {scenario: row[scenario] for scenario in ("base", "stress")}
            for name, row in summaries.items()
        },
        policy,
    )
    bootstrap = {
        scenario: block_diagnostics(
            {
                name: [
                    result.metrics["net_return_on_initial_equity_pct"] for result in rows[scenario]
                ]
                for name, rows in all_results.items()
            },
            **cfg["bootstrap"],
        )
        for scenario in scenarios
    }
    report: dict[str, Any] = {
        "mode": "registered_signal_design_development",
        "llm_used": False,
        "verdict": "development_candidate_requires_fresh_confirmation"
        if qualified
        else "no_validated_net_edge",
        "qualified_fixed_candidates": qualified,
        "registration_sha256": hashlib.sha256(registration_path.read_bytes()).hexdigest(),
        "dataset": {
            "futures": bars_quality(futures),
            "spot": bars_quality(spot),
            "paired_snapshots": len(snapshots),
            "flow": flow_provenance,
        },
        "summaries": summaries,
        "selected_summary": selected_summary,
        "folds": fold_reports,
        "bootstrap": bootstrap,
        "limitations": [
            "Reused development dates; no fresh holdout performance claimed.",
            "Binance futures proxy; Ourbit account fees and executable quotes remain unverified.",
            "No funding, latency, queue, depth impact, or exchange lot-size execution model.",
            "Rules emit uncalibrated volatility-budget heuristics, not expected-return estimates.",
            "The basis rule is an unhedged futures trade, not an arbitrage portfolio.",
            "Fold accounts reset; mean fold return is not a continuous account return.",
            "Fixed candidate rankings are exploratory; the selector sees earlier training only.",
            "Bootstrap cannot undo prior project research or prove future profitability.",
        ],
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, default=_json, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "validation_trades.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["fold", "candidate", "scenario", "selected"]
            + list(BacktestTrade.__dataclass_fields__),
        )
        writer.writeheader()
        writer.writerows(ledgers)
    with (output_dir / "comparison.csv").open("w", encoding="utf-8", newline="") as handle:
        columns = [
            "candidate",
            "scenario",
            "trade_count",
            "win_rate_pct",
            "profit_factor",
            "average_net_return_on_entry_notional_pct",
            "mean_fold_return_pct",
            "positive_folds",
            "worst_fold_drawdown_pct",
            "net_pnl",
        ]
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for name, rows in {**summaries, "training_selector": selected_summary}.items():
            for scenario, values in rows.items():
                writer.writerow({"candidate": name, "scenario": scenario, **values})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--futures", type=Path, required=True)
    parser.add_argument("--spot", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path, default=Path("configs/pillar_two_signal_designs.yaml")
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.futures, args.spot, args.config, args.output_dir)
    print(
        json.dumps(
            {"verdict": report["verdict"], "qualified": report["qualified_fixed_candidates"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
