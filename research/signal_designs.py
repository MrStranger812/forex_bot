"""Causal, predeclared signal mechanisms and composition patterns for BTC research.

Trade-flow imbalance is measured taker volume, not quote-based order-flow
imbalance. None of these rules is a calibrated prediction or live permission.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import fmean, pstdev

from bot.domain.events import Direction, PillarTwoSignal
from bot.features.bars import Bar

FAMILIES = (
    "activity_momentum",
    "relative_flow_momentum",
    "absolute_flow_momentum",
    "failed_pressure",
    "failed_breakout",
    "vwap_reversion",
    "basis_reversion",
)
ARCHITECTURES = ("regime_router", "consensus")


@dataclass(frozen=True, slots=True)
class FeatureSettings:
    activity_min: float = 1.25
    relative_flow_min: float = 0.2
    absolute_flow_min: float = 0.5
    momentum_atr_min: float = 1.0
    failed_progress_atr_max: float = 0.25
    rejection_close_fraction: float = 0.35
    range_efficiency_max: float = 0.25
    trend_efficiency_min: float = 0.35
    vwap_z_min: float = 2.0
    basis_z_min: float = 2.0
    basis_std_floor_bps: float = 0.5
    min_atr_bps: float = 1.0
    max_atr_bps: float = 60.0
    consensus_votes: int = 2

    def __post_init__(self) -> None:
        if any(
            not math.isfinite(getattr(self, f.name)) or getattr(self, f.name) <= 0
            for f in fields(self)
        ):
            raise ValueError("feature settings must be finite and positive")
        if not (
            self.relative_flow_min <= 1
            and self.rejection_close_fraction < 0.5
            and self.range_efficiency_max < self.trend_efficiency_min <= 1
            and self.min_atr_bps < self.max_atr_bps
            and type(self.consensus_votes) is int
            and 2 <= self.consensus_votes <= 5
        ):
            raise ValueError("invalid feature thresholds")


@dataclass(frozen=True, slots=True)
class Snapshot:
    at: datetime
    atr_bps: float
    return_1_bps: float
    return_5_bps: float
    return_30_bps: float
    spot_return_30_bps: float
    activity: float
    relative_flow: float
    absolute_flow: float
    efficiency: float
    vwap_distance_bps: float
    vwap_z: float
    close_fraction: float
    failed_high: bool
    failed_low: bool
    basis_deviation_bps: float
    basis_z: float
    basis_shrinking: bool


def build_snapshots(
    futures: Sequence[Bar],
    spot: Sequence[Bar],
    flow: Mapping[datetime, Decimal],
    settings: FeatureSettings,
    stride_seconds: int,
) -> dict[datetime, Snapshot]:
    """Use synchronized completed bars; baselines exclude the signal window.

    A missing spot observation resets paired warmup. No later spot candle is
    substituted, and no trade/volume baseline is fitted on a future interval.
    """
    if stride_seconds < 60 or stride_seconds % 60:
        raise ValueError("decision stride must be a positive multiple of one minute")
    for series in (futures, spot):
        previous: Bar | None = None
        for bar in series:
            if bar.symbol != "BTC_USDT" or bar.interval_seconds != 60:
                raise ValueError("snapshots require BTC_USDT one-minute bars")
            if previous is not None and bar.start < previous.end:
                raise ValueError("snapshot inputs must be unique and chronological")
            previous = bar
    spot_by_end = {bar.end: bar for bar in spot}
    history: deque[Bar] = deque(maxlen=241)
    spot_history: deque[Bar] = deque(maxlen=241)
    basis_history: deque[float] = deque(maxlen=241)
    output: dict[datetime, Snapshot] = {}
    for bar in futures:
        other = spot_by_end.get(bar.end)
        if other is None or other.start != bar.start:
            history.clear()
            spot_history.clear()
            basis_history.clear()
            continue
        if history and bar.start != history[-1].end:
            history.clear()
            spot_history.clear()
            basis_history.clear()
        buy = flow.get(bar.end)
        if buy is None or not buy.is_finite() or not 0 <= buy <= bar.volume:
            raise ValueError("measured futures taker volume is missing or invalid")
        history.append(bar)
        spot_history.append(other)
        basis_history.append(math.log(float(bar.close / other.close)) * 10000)
        if len(history) < 241 or int(bar.end.timestamp()) % stride_seconds:
            continue
        values, bases = list(history), list(basis_history)
        closes = [float(item.close) for item in values]
        prior = values[-61:-1]
        prior_closes = closes[-61:-1]
        volume = sum((item.volume for item in prior), Decimal(0))
        if volume <= 0:
            continue
        vwap = float(sum((item.close * item.volume for item in prior), Decimal(0)) / volume)
        deviation = pstdev(prior_closes)
        true_ranges = [
            max(
                float(item.high - item.low),
                abs(float(item.high) - before),
                abs(float(item.low) - before),
            )
            for item, before in zip(values[-14:], closes[-15:-1], strict=True)
        ]
        signal_window, baseline_window = values[-5:], values[-65:-5]
        volume5 = sum((item.volume for item in signal_window), Decimal(0))
        prior5_volume = sum((item.volume for item in baseline_window), Decimal(0)) / 12
        prior5_count = sum(item.trades for item in baseline_window) / 12
        if volume5 <= 0 or prior5_volume <= 0 or prior5_count <= 0:
            continue
        signed = sum((2 * flow[item.end] - item.volume for item in signal_window), Decimal(0))
        activity = min(
            float(volume5 / prior5_volume),
            sum(item.trades for item in signal_window) / prior5_count,
        )
        distance = sum(abs(b - a) for a, b in zip(closes[-61:-1], closes[-60:], strict=True))
        width = float(bar.high - bar.low)
        base_mean = fmean(bases[:-1])
        base_std = max(pstdev(bases[:-1]), settings.basis_std_floor_bps)
        deviation_bps = bases[-1] - base_mean
        row = Snapshot(
            at=bar.end,
            atr_bps=fmean(true_ranges) / closes[-1] * 10000,
            return_1_bps=math.log(closes[-1] / closes[-2]) * 10000,
            return_5_bps=math.log(closes[-1] / closes[-6]) * 10000,
            return_30_bps=math.log(closes[-1] / closes[-31]) * 10000,
            spot_return_30_bps=math.log(float(spot_history[-1].close / spot_history[-31].close))
            * 10000,
            activity=activity,
            relative_flow=float(signed / volume5),
            absolute_flow=float(signed / prior5_volume),
            efficiency=abs(closes[-1] - closes[-61]) / distance if distance else 0,
            vwap_distance_bps=(closes[-1] - vwap) / closes[-1] * 10000,
            vwap_z=(closes[-1] - vwap) / deviation if deviation else 0,
            close_fraction=(closes[-1] - float(bar.low)) / width if width else 0.5,
            failed_high=bar.high > max(item.high for item in prior)
            and bar.close < max(item.high for item in prior),
            failed_low=bar.low < min(item.low for item in prior)
            and bar.close > min(item.low for item in prior),
            basis_deviation_bps=deviation_bps,
            basis_z=deviation_bps / base_std,
            basis_shrinking=abs(deviation_bps) < abs(bases[-2] - base_mean),
        )
        if not all(math.isfinite(getattr(row, f.name)) for f in fields(row) if f.name != "at"):
            raise ValueError("nonfinite signal snapshot")
        output[bar.end] = row
    return output


def sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def specialist_votes(row: Snapshot, cfg: FeatureSettings) -> dict[str, int]:
    votes = dict.fromkeys(FAMILIES, 0)
    if not cfg.min_atr_bps <= row.atr_bps <= cfg.max_atr_bps:
        return votes
    active = row.activity >= cfg.activity_min
    direction = sign(row.return_30_bps)
    momentum = (
        active
        and abs(row.return_30_bps) >= cfg.momentum_atr_min * row.atr_bps
        and sign(row.return_5_bps) == direction
        and sign(row.spot_return_30_bps) == direction
    )
    if momentum:
        votes["activity_momentum"] = direction
        if row.relative_flow * direction >= cfg.relative_flow_min:
            votes["relative_flow_momentum"] = direction
        if row.absolute_flow * direction >= cfg.absolute_flow_min:
            votes["absolute_flow_momentum"] = direction
    pressure = sign(row.absolute_flow)
    if (
        active
        and abs(row.absolute_flow) >= cfg.absolute_flow_min
        and abs(row.return_5_bps) <= cfg.failed_progress_atr_max * row.atr_bps
        and sign(row.return_1_bps) == -pressure
    ):
        votes["failed_pressure"] = -pressure
    # Reject bars that sweep both sides: their intrabar sequence is unknown.
    if active and row.failed_high != row.failed_low:
        if row.failed_high and row.close_fraction <= cfg.rejection_close_fraction:
            votes["failed_breakout"] = -1
        elif row.failed_low and row.close_fraction >= 1 - cfg.rejection_close_fraction:
            votes["failed_breakout"] = 1
    if (
        row.efficiency <= cfg.range_efficiency_max
        and abs(row.vwap_z) >= cfg.vwap_z_min
        and sign(row.return_1_bps) == -sign(row.vwap_z)
    ):
        votes["vwap_reversion"] = -sign(row.vwap_z)
    if abs(row.basis_z) >= cfg.basis_z_min and row.basis_shrinking:
        votes["basis_reversion"] = -sign(row.basis_z)
    return votes


def composed_vote(name: str, row: Snapshot, votes: Mapping[str, int], cfg: FeatureSettings) -> int:
    if name in FAMILIES:
        return votes[name]
    if name == "regime_router":
        if row.efficiency >= cfg.trend_efficiency_min:
            return votes["absolute_flow_momentum"]
        if row.efficiency <= cfg.range_efficiency_max:
            for family in ("failed_breakout", "vwap_reversion", "failed_pressure"):
                if votes[family]:
                    return votes[family]
        return 0
    if name == "consensus":
        # The three nested momentum ablations must never count as three votes.
        independent = [
            votes[family]
            for family in (
                "absolute_flow_momentum",
                "failed_pressure",
                "failed_breakout",
                "vwap_reversion",
                "basis_reversion",
            )
            if votes[family]
        ]
        if len(independent) >= cfg.consensus_votes and len(set(independent)) == 1:
            return independent[0]
        return 0
    raise ValueError("unknown signal design")


class DesignSource:
    def __init__(
        self,
        snapshots: Mapping[datetime, Snapshot],
        settings: FeatureSettings,
        design: str,
        horizon_seconds: int,
    ) -> None:
        if design not in FAMILIES + ARCHITECTURES:
            raise ValueError("unknown signal design")
        if type(horizon_seconds) is not int or horizon_seconds <= 0 or horizon_seconds % 60:
            raise ValueError("signal horizon must be a positive multiple of one minute")
        self.snapshots, self.settings = snapshots, settings
        self.design, self.horizon = design, horizon_seconds
        self.last_end: datetime | None = None

    def on_bar(self, bar: Bar) -> None:
        if bar.symbol != "BTC_USDT" or bar.interval_seconds != 60:
            raise ValueError("signal source requires BTC_USDT one-minute bars")
        if self.last_end is not None and bar.end <= self.last_end:
            raise ValueError("signal source requires chronological bars")
        self.last_end = bar.end

    def signal(self, now: datetime) -> PillarTwoSignal:
        if now != self.last_end:
            raise ValueError("only the last consumed completed bar is visible")
        row = self.snapshots.get(now)
        side, move, regime = 0, 0.0, "ambiguous"
        if row is not None:
            votes = specialist_votes(row, self.settings)
            side = composed_vote(self.design, row, votes, self.settings)
            regime = "trend" if row.efficiency >= self.settings.trend_efficiency_min else "range"
            # A volatility budget is an uncalibrated eligibility heuristic, not alpha.
            budget = row.atr_bps * math.sqrt(self.horizon / 60)

            def movement(family: str) -> float:
                if family == "vwap_reversion":
                    return min(budget, abs(row.vwap_distance_bps))
                if family == "basis_reversion":
                    return min(budget, abs(row.basis_deviation_bps))
                return budget

            move = movement(self.design)
            if side and self.design == "regime_router" and regime == "range":
                family = next(
                    name
                    for name in ("failed_breakout", "vwap_reversion", "failed_pressure")
                    if votes[name]
                )
                move = movement(family)
            elif side and self.design == "consensus":
                move = min(
                    movement(name)
                    for name in (
                        "absolute_flow_momentum",
                        "failed_pressure",
                        "failed_breakout",
                        "vwap_reversion",
                        "basis_reversion",
                    )
                    if votes[name]
                )
        return PillarTwoSignal(
            "BTC_USDT",
            Direction.BULLISH if side > 0 else Direction.BEARISH if side < 0 else Direction.NEUTRAL,
            1.0 if side else 0.0,
            regime,
            now,
            now + timedelta(seconds=60),
            (self.design, "unvalidated_rule_hypothesis"),
            float(side),
            move if side else 0,
            self.horizon,
            self.design,
        )
