"""Deterministic, LLM-free candle research. This is not an executable L2 replay."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import yaml

from bot.domain.events import Direction, PillarTwoSignal, ensure_utc
from bot.features.bars import Bar
from bot.strategy.time_based import TimeBasedPillarTwoConfig, TimeBasedPillarTwoEngine

BPS = Decimal(10_000)
ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class CandleCosts:
    fee_bps_per_side: Decimal
    spread_bps: Decimal
    slippage_bps_per_side: Decimal
    safety_margin_bps: Decimal

    def __post_init__(self) -> None:
        for item in fields(self):
            value = Decimal(str(getattr(self, item.name)))
            if not value.is_finite() or not ZERO <= value < BPS:
                raise ValueError(f"{item.name} must be finite and within [0, 10000)")
            object.__setattr__(self, item.name, value)
        if self.execution_bps_per_side >= BPS:
            raise ValueError("combined execution costs must be below 10000 bps per side")

    @property
    def execution_bps_per_side(self) -> Decimal:
        return self.spread_bps / 2 + self.slippage_bps_per_side

    @property
    def required_edge_bps(self) -> Decimal:
        return 2 * (self.fee_bps_per_side + self.execution_bps_per_side) + self.safety_margin_bps

    def fill(self, reference: Decimal, side: int) -> Decimal:
        return reference * (ONE + Decimal(side) * self.execution_bps_per_side / BPS)

    def fee(self, price: Decimal, quantity: Decimal) -> Decimal:
        return price * quantity * self.fee_bps_per_side / BPS

    def stressed(self, multiplier: Decimal) -> CandleCosts:
        if not multiplier.is_finite() or multiplier < ONE:
            raise ValueError("stress multiplier must be finite and at least one")
        return replace(
            self,
            fee_bps_per_side=self.fee_bps_per_side * multiplier,
            spread_bps=self.spread_bps * multiplier,
            slippage_bps_per_side=self.slippage_bps_per_side * multiplier,
        )


@dataclass(frozen=True, slots=True)
class CandleBacktestConfig:
    initial_equity: Decimal = Decimal("10000")
    risk_fraction: Decimal = Decimal("0.0005")
    max_notional_fraction: Decimal = ONE
    stop_bps: Decimal = Decimal("20")
    target_bps: Decimal = Decimal("30")
    min_strength: float = 0.5
    daily_loss_fraction: Decimal = Decimal("0.01")
    max_drawdown_fraction: Decimal = Decimal("0.03")
    max_consecutive_losses: int = 3
    cooldown_seconds: int = 900

    def __post_init__(self) -> None:
        for name in (
            "initial_equity",
            "risk_fraction",
            "max_notional_fraction",
            "stop_bps",
            "target_bps",
            "daily_loss_fraction",
            "max_drawdown_fraction",
        ):
            value = Decimal(str(getattr(self, name)))
            if not value.is_finite() or value <= ZERO:
                raise ValueError(f"{name} must be finite and positive")
            object.__setattr__(self, name, value)
        for name in (
            "risk_fraction",
            "max_notional_fraction",
            "daily_loss_fraction",
            "max_drawdown_fraction",
        ):
            if getattr(self, name) > ONE:
                raise ValueError(f"{name} cannot exceed one")
        if self.risk_fraction > Decimal("0.01"):
            raise ValueError("risk_fraction cannot exceed the repository limit of 0.01")
        if max(self.stop_bps, self.target_bps) >= BPS:
            raise ValueError("stop and target must be below 10000 bps")
        if not math.isfinite(self.min_strength) or not 0 <= self.min_strength <= 1:
            raise ValueError("min_strength must be within [0, 1]")
        if (
            type(self.max_consecutive_losses) is not int
            or self.max_consecutive_losses < 1
            or type(self.cooldown_seconds) is not int
            or self.cooldown_seconds < 0
        ):
            raise ValueError("loss count and cooldown must be valid integers")


class SignalSource(Protocol):
    def on_bar(self, bar: Bar) -> None: ...

    def signal(self, now: datetime) -> PillarTwoSignal: ...


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    signal_at: datetime
    entry_at: datetime
    exit_at: datetime
    direction: str
    regime: str
    model: str
    quantity: Decimal
    entry_reference: Decimal
    exit_reference: Decimal
    entry_fill: Decimal
    exit_fill: Decimal
    gross_pnl: Decimal
    execution_cost: Decimal
    fees: Decimal
    net_pnl: Decimal
    net_return_on_entry_notional_pct: Decimal
    exit_reason: str
    ambiguous_stop_target: bool


@dataclass(slots=True)
class _Position:
    signal: PillarTwoSignal
    entry_at: datetime
    reference: Decimal
    fill: Decimal
    quantity: Decimal
    fee: Decimal
    side: int
    stop: Decimal
    target: Decimal
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class BacktestResult:
    metrics: dict[str, Any]
    trades: list[BacktestTrade]
    risk_events: list[dict[str, str]]


def _trade_metrics(trades: Sequence[BacktestTrade]) -> dict[str, Any]:
    count = len(trades)
    wins = sum(trade.net_pnl > ZERO for trade in trades)
    losses = sum(trade.net_pnl < ZERO for trade in trades)
    positive = sum((max(ZERO, trade.net_pnl) for trade in trades), ZERO)
    negative = -sum((min(ZERO, trade.net_pnl) for trade in trades), ZERO)
    net = positive - negative
    interval: list[float] | None = None
    if count:
        # Wilson interval is descriptive; serially dependent trades violate iid assumptions.
        proportion, z = wins / count, 1.959963984540054
        denominator = 1 + z * z / count
        center = (proportion + z * z / (2 * count)) / denominator
        radius = (
            z
            * math.sqrt(proportion * (1 - proportion) / count + z * z / (4 * count * count))
            / denominator
        )
        interval = [100 * max(0, center - radius), 100 * min(1, center + radius)]
    return {
        "trade_count": count,
        "wins": wins,
        "losses": losses,
        "breakeven": count - wins - losses,
        "win_rate_pct": 100 * wins / count if count else None,
        "win_rate_wilson_95_pct": interval,
        "gross_pnl": float(sum((trade.gross_pnl for trade in trades), ZERO)),
        "execution_cost": float(sum((trade.execution_cost for trade in trades), ZERO)),
        "fees": float(sum((trade.fees for trade in trades), ZERO)),
        "net_pnl": float(net),
        "profit_factor": float(positive / negative) if negative else None,
        "profit_factor_status": "defined" if negative else "undefined_no_losses",
        "expectancy_net_per_trade": float(net / count) if count else None,
        "average_net_return_on_entry_notional_pct": float(
            sum((trade.net_return_on_entry_notional_pct for trade in trades), ZERO) / count
        )
        if count
        else None,
        "ambiguous_stop_target_count": sum(trade.ambiguous_stop_target for trade in trades),
    }


def run_backtest(
    bars: Sequence[Bar],
    *,
    symbol: str,
    engine_config: TimeBasedPillarTwoConfig,
    costs: CandleCosts,
    config: CandleBacktestConfig,
    start: datetime | None = None,
    end: datetime | None = None,
    source_factory: Callable[[], SignalSource] | None = None,
) -> BacktestResult:
    """Run one fresh account; preceding bars warm features but cannot create entries.

    Stops/targets use reference OHLC. Fills include half spread and adverse slippage.
    Ambiguous intrabar hits choose the stop. Gap stops fill at the adverse opening.
    Intrabar exit timestamps denote the candle end, not a fabricated exact fill time.
    """
    if not bars:
        raise ValueError("at least one bar is required")
    start = ensure_utc(start or bars[0].start)
    end = ensure_utc(end or bars[-1].end)
    if start >= end:
        raise ValueError("evaluation start must precede end")
    if engine_config.horizon_seconds % engine_config.interval_seconds:
        raise ValueError("holding horizon must be an exact number of candles")
    source = source_factory() if source_factory else TimeBasedPillarTwoEngine(symbol, engine_config)
    cash = config.initial_equity
    peak, boundary_peak = cash, cash
    max_drawdown, boundary_drawdown = ZERO, ZERO
    position: _Position | None = None
    pending: PillarTwoSignal | None = None
    previous: Bar | None = None
    trades: list[BacktestTrade] = []
    risk_events: list[dict[str, str]] = []
    rejections: Counter[str] = Counter()
    session_date = start.date()
    day_equity = cash
    daily_halt = False
    killed = False
    losses = 0
    cooldown_until: datetime | None = None
    evaluated_bars = 0

    def liquidation(reference: Decimal) -> Decimal:
        if position is None:
            return cash
        exit_fill = costs.fill(reference, -position.side)
        return (
            cash
            + Decimal(position.side) * (exit_fill - position.fill) * position.quantity
            - (costs.fee(exit_fill, position.quantity))
        )

    def observe(equity: Decimal, *, boundary: bool = True) -> None:
        nonlocal peak, boundary_peak, max_drawdown, boundary_drawdown
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
        if boundary:
            boundary_peak = max(boundary_peak, equity)
            boundary_drawdown = max(boundary_drawdown, (boundary_peak - equity) / boundary_peak)

    def check_risk(now: datetime, equity: Decimal) -> None:
        nonlocal killed, daily_halt
        if not killed and equity <= boundary_peak * (ONE - config.max_drawdown_fraction):
            killed = True
            risk_events.append({"at": now.isoformat(), "reason": "max_drawdown_kill_switch"})
        if not daily_halt and equity <= day_equity * (ONE - config.daily_loss_fraction):
            daily_halt = True
            risk_events.append({"at": now.isoformat(), "reason": "daily_loss_limit"})

    def close(reference: Decimal, now: datetime, reason: str, ambiguous: bool = False) -> None:
        nonlocal position, cash, losses, cooldown_until
        assert position is not None
        exit_fill = costs.fill(reference, -position.side)
        fee = costs.fee(exit_fill, position.quantity)
        gross = Decimal(position.side) * (reference - position.reference) * position.quantity
        fill_pnl = Decimal(position.side) * (exit_fill - position.fill) * position.quantity
        net = fill_pnl - position.fee - fee
        cash += fill_pnl - fee
        trades.append(
            BacktestTrade(
                signal_at=position.signal.generated_at,
                entry_at=position.entry_at,
                exit_at=now,
                direction=position.signal.direction.value,
                regime=position.signal.regime,
                model=position.signal.model or "none",
                quantity=position.quantity,
                entry_reference=position.reference,
                exit_reference=reference,
                entry_fill=position.fill,
                exit_fill=exit_fill,
                gross_pnl=gross,
                execution_cost=gross - fill_pnl,
                fees=position.fee + fee,
                net_pnl=net,
                net_return_on_entry_notional_pct=net / (position.fill * position.quantity) * 100,
                exit_reason=reason,
                ambiguous_stop_target=ambiguous,
            )
        )
        position = None
        losses = losses + 1 if net < ZERO else 0 if net > ZERO else losses
        if net < ZERO and losses >= config.max_consecutive_losses:
            cooldown_until = now + timedelta(seconds=config.cooldown_seconds)
            losses = 0
            risk_events.append({"at": now.isoformat(), "reason": "loss_cooldown"})
        observe(cash)
        check_risk(now, cash)

    for bar in bars:
        if bar.start >= end or bar.end > end:
            break
        if bar.symbol != symbol or bar.interval_seconds != engine_config.interval_seconds:
            raise ValueError("bar symbol or interval does not match the configuration")
        if previous is not None and bar.start < previous.end:
            raise ValueError("bars must be ordered without duplicates or overlaps")
        active = bar.start >= start and bar.end <= end
        gap = previous is not None and bar.start != previous.end
        if active:
            evaluated_bars += 1
            opening_equity = liquidation(bar.open)
            if bar.start.date() != session_date:
                session_date = bar.start.date()
                day_equity = opening_equity
                daily_halt = False
            observe(opening_equity)
            check_risk(bar.start, opening_equity)
            if gap:
                pending = None
                rejections["data_gap"] += 1
                if position is not None:
                    close(bar.open, bar.start, "data_gap")
            if position is not None and (killed or daily_halt):
                close(bar.open, bar.start, "risk_halt")
            if pending is not None and position is None:
                reason = (
                    "max_drawdown_kill_switch"
                    if killed
                    else "daily_loss_limit"
                    if daily_halt
                    else "loss_cooldown"
                    if cooldown_until and bar.start < cooldown_until
                    else "stale_signal"
                    if not pending.is_fresh(bar.start)
                    else "cost_gate"
                    if (
                        Decimal(str(pending.expected_net_return_bps)) <= costs.safety_margin_bps
                        if pending.expected_net_return_bps is not None
                        else Decimal(str(pending.expected_move_bps)) <= costs.required_edge_bps
                    )
                    else None
                )
                if reason:
                    rejections[reason] += 1
                else:
                    side = 1 if pending.direction == Direction.BULLISH else -1
                    fill = costs.fill(bar.open, side)
                    stop = bar.open * (ONE - Decimal(side) * config.stop_bps / BPS)
                    target = bar.open * (ONE + Decimal(side) * config.target_bps / BPS)
                    stop_fill = costs.fill(stop, -side)
                    loss_per_unit = (
                        Decimal(side) * (fill - stop_fill)
                        + costs.fee(fill, ONE)
                        + costs.fee(stop_fill, ONE)
                    )
                    quantity = min(
                        cash * config.risk_fraction / loss_per_unit,
                        cash * config.max_notional_fraction / (fill + costs.fee(fill, ONE)),
                    )
                    entry_fee = costs.fee(fill, quantity)
                    position = _Position(
                        pending,
                        bar.start,
                        bar.open,
                        fill,
                        quantity,
                        entry_fee,
                        side,
                        stop,
                        target,
                        bar.start + timedelta(seconds=engine_config.horizon_seconds),
                    )
                    cash -= entry_fee
                    observe(liquidation(bar.open))
            pending = None
            if position is not None:
                side, stop, target = position.side, position.stop, position.target
                opening_stop = bar.open <= stop if side == 1 else bar.open >= stop
                opening_target = bar.open >= target if side == 1 else bar.open <= target
                hit_stop = bar.low <= stop if side == 1 else bar.high >= stop
                hit_target = bar.high >= target if side == 1 else bar.low <= target
                if opening_stop:
                    close(bar.open, bar.start, "stop_gap")
                elif opening_target:
                    # Do not credit favorable gap improvement to a standing target.
                    close(target, bar.start, "target_gap")
                else:
                    adverse = max(bar.low, stop) if side == 1 else min(bar.high, stop)
                    observe(liquidation(adverse), boundary=False)
                    if hit_stop:
                        close(stop, bar.end, "stop", hit_target)
                    elif hit_target:
                        close(target, bar.end, "target")
                    elif bar.end >= position.expires_at:
                        close(bar.close, bar.end, "time")
                    else:
                        closing_equity = liquidation(bar.close)
                        observe(closing_equity)
                        check_risk(bar.end, closing_equity)
                        if killed or daily_halt:
                            close(bar.close, bar.end, "risk_halt")
            if position is not None and (bar.end == end or bar is bars[-1]):
                close(bar.close, bar.end, "end_of_window")
        source.on_bar(bar)
        if active:
            signal = source.signal(bar.end)
            if (
                signal.direction != Direction.NEUTRAL
                and signal.strength >= config.min_strength
                and signal.regime
                not in {
                    "UNTRADEABLE",
                    "ILLIQUID",
                    "NEWS_SHOCK",
                    "HIGH_VOLATILITY",
                }
                and signal.generated_at == bar.end
            ):
                pending = signal
            else:
                rejections["signal_gate"] += 1
        previous = bar
    if position is not None and previous is not None:
        close(previous.close, previous.end, "end_of_window")
    if not evaluated_bars:
        raise ValueError("evaluation window contains no complete candles")
    metrics = _trade_metrics(trades)
    metrics.update(
        {
            "start": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "evaluated_bars": evaluated_bars,
            "initial_equity": float(config.initial_equity),
            "final_equity": float(cash),
            "net_return_on_initial_equity_pct": float((cash / config.initial_equity - ONE) * 100),
            "max_mtm_drawdown_pct": float(max_drawdown * 100),
            "max_boundary_mtm_drawdown_pct": float(boundary_drawdown * 100),
            "max_mtm_drawdown_method": "boundaries plus adverse candle excursion before exit",
            "risk_event_counts": dict(Counter(event["reason"] for event in risk_events)),
            "entry_rejection_counts": dict(rejections),
            "kill_switch_triggered": killed,
            "by_regime": {
                regime: _trade_metrics([t for t in trades if t.regime == regime])
                for regime in sorted({t.regime for t in trades})
            },
            "by_model": {
                model: _trade_metrics([t for t in trades if t.model == model])
                for model in sorted({t.model for t in trades})
            },
        }
    )
    return BacktestResult(metrics, trades, risk_events)


def chronological_windows(
    bars: Sequence[Bar],
    *,
    development_fraction: float,
    embargo_seconds: int,
    horizon_seconds: int,
) -> dict[str, tuple[datetime, datetime]]:
    if not math.isfinite(development_fraction) or not 0 < development_fraction < 1:
        raise ValueError("development_fraction must be within (0, 1)")
    if type(embargo_seconds) is not int or embargo_seconds < horizon_seconds:
        raise ValueError("embargo must be at least the holding horizon")
    boundary = int(len(bars) * development_fraction)
    if not 0 < boundary < len(bars):
        raise ValueError("not enough candles for a chronological split")
    development_end = bars[boundary - 1].end
    eligible = [
        bar
        for bar in bars[boundary:]
        if bar.start >= development_end + timedelta(seconds=embargo_seconds)
    ]
    if not eligible:
        raise ValueError("embargo leaves no test candles")
    return {
        "development": (bars[0].start, development_end),
        "test": (eligible[0].start, bars[-1].end),
    }


def _section(document: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name)
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be a mapping")
    return value


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def evaluate_file(input_path: Path, config_path: Path, output_dir: Path) -> dict[str, Any]:
    from research.market_data import read_bars

    config_bytes = config_path.read_bytes()
    raw = yaml.safe_load(config_bytes)
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a mapping")
    unknown = set(raw) - {"symbol", "engine", "costs", "backtest", "evaluation", "cost_evidence"}
    if unknown:
        raise ValueError(f"unknown configuration keys: {sorted(unknown)}")
    symbol = raw.get("symbol")
    if not isinstance(symbol, str) or symbol != "BTC_USDT":
        raise ValueError("this research milestone is limited to BTC_USDT")
    engine_config = TimeBasedPillarTwoConfig(**_section(raw, "engine"))
    costs = CandleCosts(**_section(raw, "costs"))
    backtest_config = CandleBacktestConfig(**_section(raw, "backtest"))
    evaluation = _section(raw, "evaluation")
    if set(evaluation) != {"development_fraction", "embargo_seconds", "stress_cost_multiplier"}:
        raise ValueError(
            "evaluation requires development_fraction, embargo_seconds, and "
            "stress_cost_multiplier only"
        )
    bars = read_bars(input_path)
    windows = chronological_windows(
        bars,
        development_fraction=evaluation["development_fraction"],
        embargo_seconds=evaluation["embargo_seconds"],
        horizon_seconds=engine_config.horizon_seconds,
    )
    scenarios = {
        "base": costs,
        "stress": costs.stressed(Decimal(str(evaluation["stress_cost_multiplier"]))),
    }
    results = {
        scenario: {
            split: run_backtest(
                bars,
                symbol=symbol,
                engine_config=engine_config,
                costs=scenario_costs,
                config=backtest_config,
                start=start,
                end=end,
            )
            for split, (start, end) in windows.items()
        }
        for scenario, scenario_costs in scenarios.items()
    }
    provenance_path = Path(str(input_path) + ".provenance.json")
    input_digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
    provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else None
    if provenance and provenance.get("normalized_sha256") != input_digest:
        raise ValueError("input hash differs from provenance sidecar")
    repository = Path(__file__).resolve().parents[1]
    source_hashes = {
        source: hashlib.sha256((repository / source).read_bytes()).hexdigest()
        for source in (
            "research/pillar_two_backtest.py",
            "research/market_data.py",
            "bot/strategy/time_based.py",
            "bot/domain/events.py",
            "bot/features/bars.py",
        )
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "pillar_two_only_candle_research",
        "symbol": symbol,
        "llm_used": False,
        "input": {
            "path": str(input_path),
            "sha256": input_digest,
            "bars": len(bars),
            "first_start": bars[0].start.isoformat(),
            "last_end": bars[-1].end.isoformat(),
            "provenance": provenance,
        },
        "configuration": {
            "path": str(config_path),
            "sha256": hashlib.sha256(config_bytes).hexdigest(),
            "values": raw,
            "resolved": {
                "engine": asdict(engine_config),
                "costs": asdict(costs),
                "backtest": asdict(backtest_config),
            },
        },
        "source_sha256": source_hashes,
        "split_policy": {
            "method": "fixed chronological development/test holdout; no fitting or search",
            "embargo_seconds": evaluation["embargo_seconds"],
            "warmup": "earlier candles feed features; positions and signals reset per split",
            "stress": "same signals; fees/spread/slippage multiplied; cost gate reapplied",
        },
        "results": {
            scenario: {
                split: {"metrics": result.metrics, "risk_events": result.risk_events}
                for split, result in splits.items()
            }
            for scenario, splits in results.items()
        },
        "limitations": [
            "Candle proxy research is not Ourbit perpetual executable bid/ask or L2 evidence.",
            "No LLM, book/flow confirmation, calibrated probabilities, or live authorization.",
            "Expected movement is a heuristic, not a calibrated conditional expectation.",
            "Fees, spread and slippage are explicit assumptions, not verified account metadata.",
            "No funding, queue position, depth impact, latency simulation, or lot-size rounding.",
            "No liquidation or margin-tier modeling; research exposure is capped at 1x.",
            "Hypothetical short exposure can lose more than initial equity on extreme gaps.",
            "Unknown intrabar path: stop wins ambiguous hits; intrabar exits timestamp bar end.",
            "Drawdown uses adverse candle excursion; intrabar peaks/exit ordering are unknown.",
            "Gaps may exceed stop/risk budgets; risk checks run at observed boundaries.",
            "Entry assumes the next opening is available immediately after signal close.",
            "Wilson win-rate intervals assume independent trades; signals may violate this.",
            "Small/single-period results cannot prove profitability; test reuse needs new data.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, default=_json_default, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["scenario", "split"] + [field.name for field in fields(BacktestTrade)],
        )
        writer.writeheader()
        for scenario, splits in results.items():
            for split, result in splits.items():
                for trade in result.trades:
                    writer.writerow({"scenario": scenario, "split": split, **asdict(trade)})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_file(args.input, args.config, args.output_dir)
    print(
        json.dumps(
            {scenario: splits["test"]["metrics"] for scenario, splits in report["results"].items()},
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
