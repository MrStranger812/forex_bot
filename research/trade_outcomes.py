"""Causal opportunity features and matured, cost-adjusted counterfactual labels."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import fmean, pstdev

from bot.domain.events import Direction, PillarTwoSignal
from bot.features.bars import Bar
from research.pillar_two_backtest import CandleCosts
from research.pillar_two_experiments import CachedCandidateSource, Candidate, SignalCache

FEATURE_NAMES = (
    "strength",
    "long",
    "breakout",
    "atr_bps",
    "return_volatility_bps",
    "aligned_return_1_bps",
    "aligned_return_5_bps",
    "aligned_return_15_bps",
    "aligned_return_60_bps",
    "efficiency_20",
    "aligned_deviation_z",
    "aligned_close_location",
    "volume_ratio",
    "trade_count_ratio",
    "higher_timeframe_agreement",
    "utc_hour_sin",
    "utc_hour_cos",
    "weekend",
    "aligned_taker_flow_1",
    "aligned_taker_flow_5",
    "aligned_taker_flow_15",
    "flow_available",
)


@dataclass(frozen=True, slots=True)
class Opportunity:
    decision_at: datetime
    bar_index: int
    signal: PillarTwoSignal
    features: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Outcome:
    opportunity: Opportunity
    entry_at: datetime
    known_at: datetime
    exit_reason: str
    gross_bps: float
    net_bps: float
    stress_net_bps: float


def opportunity_features(
    bars: Sequence[Bar],
    cache: SignalCache,
    candidate: Candidate,
    stride_seconds: int,
    taker_buy: Mapping[datetime, Decimal] | None = None,
) -> list[Opportunity]:
    """Sample decisions on a fixed clock, using only closed historical candles."""
    if stride_seconds <= 0 or stride_seconds % cache.config.interval_seconds:
        raise ValueError("opportunity stride must be a positive multiple of the bar duration")
    source = CachedCandidateSource(cache, candidate)
    history: deque[Bar] = deque(maxlen=61)
    rows: list[Opportunity] = []
    previous: Bar | None = None
    for index, bar in enumerate(bars):
        if previous is not None and bar.start != previous.end:
            history.clear()
        history.append(bar)
        previous = bar
        source.on_bar(bar)
        if len(history) < 61 or int(bar.end.timestamp()) % stride_seconds:
            continue
        signal = source.signal(bar.end)
        if signal.direction == Direction.NEUTRAL or signal.strength < candidate.min_strength:
            continue
        values = list(history)
        closes = [float(item.close) for item in values]
        side = 1 if signal.direction == Direction.BULLISH else -1
        returns = [math.log(b / a) * 10000 for a, b in zip(closes, closes[1:], strict=False)]
        ranges = [
            max(
                float(item.high - item.low),
                abs(float(item.high) - before),
                abs(float(item.low) - before),
            )
            for before, item in zip(closes, values[1:], strict=False)
        ]
        window = closes[-21:]
        distance = sum(abs(b - a) for a, b in zip(window, window[1:], strict=False))
        deviation = pstdev(window[1:])
        width = float(bar.high - bar.low)
        mean_volume = fmean(float(item.volume) for item in values[-21:-1])
        mean_trades = fmean(item.trades for item in values[-21:-1])
        hour = bar.end.hour + bar.end.minute / 60
        flow_features: list[float] = []
        for window_size in (1, 5, 15):
            flow_bars = values[-window_size:]
            volume = sum((item.volume for item in flow_bars), Decimal(0))
            if taker_buy is None:
                flow_features.append(0.0)
            else:
                amounts = [taker_buy[item.end] for item in flow_bars]
                if any(
                    not amount.is_finite() or not 0 <= amount <= item.volume
                    for amount, item in zip(amounts, flow_bars, strict=True)
                ):
                    raise ValueError("invalid measured taker volume")
                buying = sum(amounts, Decimal(0))
                flow_features.append(
                    side * float((2 * buying - volume) / volume) if volume else 0.0
                )
        features = (
            signal.strength,
            float(side == 1),
            float(signal.model == "breakout"),
            fmean(ranges[-14:]) / closes[-1] * 10000,
            pstdev(returns[-20:]),
            *(side * math.log(closes[-1] / closes[-1 - n]) * 10000 for n in (1, 5, 15, 60)),
            abs(window[-1] - window[0]) / distance if distance else 0.0,
            side * (closes[-1] - fmean(window[1:])) / deviation if deviation else 0.0,
            side * (2 * (closes[-1] - float(bar.low)) / width - 1) if width else 0.0,
            min(20.0, float(bar.volume) / mean_volume) if mean_volume else 0.0,
            min(20.0, bar.trades / mean_trades) if mean_trades else 0.0,
            float(cache.confirmations[bar.end] == signal.direction),
            math.sin(2 * math.pi * hour / 24),
            math.cos(2 * math.pi * hour / 24),
            float(bar.end.weekday() >= 5),
            *flow_features,
            float(taker_buy is not None),
        )
        if len(features) != len(FEATURE_NAMES) or not all(math.isfinite(v) for v in features):
            raise ValueError("invalid outcome feature vector")
        rows.append(Opportunity(bar.end, index, signal, features))
    return rows


def label_opportunity(
    bars: Sequence[Bar],
    opportunity: Opportunity,
    candidate: Candidate,
    costs: CandleCosts,
    stress_costs: CandleCosts,
) -> Outcome | None:
    """One-unit counterfactual; unresolved tails and data gaps produce no label.

    Stops and targets follow the research executor's reference-price conventions.
    A label is unavailable until its exit candle closes (or opening gap is seen).
    These overlapping opportunities are not an executable multi-position ledger.
    """
    entry_index = opportunity.bar_index + 1
    if entry_index >= len(bars) or bars[entry_index].start != opportunity.decision_at:
        return None
    entry = bars[entry_index]
    side = 1 if opportunity.signal.direction == Direction.BULLISH else -1
    stop = entry.open * (Decimal(1) - Decimal(side) * candidate.stop_bps / 10000)
    target = entry.open * (Decimal(1) + Decimal(side) * candidate.target_bps / 10000)
    expires_at = entry.start + timedelta(seconds=candidate.horizon_seconds)
    previous_end = entry.start
    for bar in bars[
        entry_index : entry_index + candidate.horizon_seconds // entry.interval_seconds
    ]:
        if bar.start != previous_end:
            return None
        previous_end = bar.end
        reference: Decimal | None = None
        at = bar.end
        reason = ""
        if bar.open <= stop if side == 1 else bar.open >= stop:
            reference, at, reason = bar.open, bar.start, "stop_gap"
        elif bar.open >= target if side == 1 else bar.open <= target:
            reference, at, reason = target, bar.start, "target_gap"
        elif bar.low <= stop if side == 1 else bar.high >= stop:
            reference, reason = stop, "stop"
        elif bar.high >= target if side == 1 else bar.low <= target:
            reference, reason = target, "target"
        elif bar.end >= expires_at:
            reference, reason = bar.close, "time"
        if reference is None:
            continue

        def net(model: CandleCosts, exit_reference: Decimal) -> float:
            opening, closing = model.fill(entry.open, side), model.fill(exit_reference, -side)
            pnl = Decimal(side) * (closing - opening) - model.fee(opening, Decimal(1))
            pnl -= model.fee(closing, Decimal(1))
            return float(pnl / opening * 10000)

        return Outcome(
            opportunity,
            entry.start,
            at,
            reason,
            float(Decimal(side) * (reference - entry.open) / entry.open * 10000),
            net(costs, reference),
            net(stress_costs, reference),
        )
    return None


def mature_outcomes(outcomes: Sequence[Outcome], start: datetime, end: datetime) -> list[Outcome]:
    """The entire label interval must be inside its fitting/calibration segment."""
    return [
        row
        for row in outcomes
        if start <= row.opportunity.decision_at < end
        and row.known_at <= end
        and row.opportunity.decision_at + timedelta(seconds=row.opportunity.signal.horizon_seconds)
        <= end
    ]


def outcome_record(row: Outcome) -> Mapping[str, object]:
    return {
        "decision_at": row.opportunity.decision_at.isoformat(),
        "entry_at": row.entry_at.isoformat(),
        "known_at": row.known_at.isoformat(),
        "direction": row.opportunity.signal.direction.value,
        "model": row.opportunity.signal.model,
        "features": row.opportunity.features,
        "gross_bps": row.gross_bps,
        "net_bps": row.net_bps,
        "stress_net_bps": row.stress_net_bps,
        "exit_reason": row.exit_reason,
    }
