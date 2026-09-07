"""Deterministic, closed-bar Pillar Two baseline with no trained probabilities.

OHLCV cannot establish spread, executable liquidity, order flow, or news shocks.
Those checks belong to the independent runtime entry gates. Expected movement
is an ATR/distance heuristic for cost screening, not a fitted return forecast.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta
from math import isfinite, sqrt
from statistics import fmean, pstdev

from bot.domain.events import Direction, PillarTwoSignal, ensure_utc
from bot.features.bars import Bar


@dataclass(frozen=True, slots=True)
class TimeBasedPillarTwoConfig:
    interval_seconds: int = 60
    warmup_bars: int = 60
    horizon_seconds: int = 300
    signal_ttl_seconds: int = 60
    fast_ema_period: int = 10
    slow_ema_period: int = 26
    lookback_bars: int = 20
    atr_period: int = 14
    compression_baseline_bars: int = 40
    breakout_persistence_bars: int = 2
    min_atr_bps: float = 1.0
    max_atr_bps: float = 60.0
    max_bar_range_atr: float = 4.0
    trend_efficiency: float = 0.35
    trend_separation_atr: float = 0.20
    range_efficiency: float = 0.30
    min_reversion_z: float = 1.0
    compression_atr_ratio: float = 0.80
    compression_range_atr: float = 4.0
    breakout_buffer_atr: float = 0.10
    breakout_volume_ratio: float = 1.10
    trend_move_atr: float = 0.80
    breakout_move_atr: float = 1.0

    def __post_init__(self) -> None:
        integer_fields = (
            "interval_seconds", "warmup_bars", "horizon_seconds", "signal_ttl_seconds",
            "fast_ema_period", "slow_ema_period", "lookback_bars", "atr_period",
            "compression_baseline_bars", "breakout_persistence_bars",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for field in fields(self):
            if field.name not in integer_fields:
                value = getattr(self, field.name)
                if isinstance(value, bool) or not isfinite(value) or value <= 0:
                    raise ValueError(f"{field.name} must be finite and positive")
        if self.fast_ema_period >= self.slow_ema_period:
            raise ValueError("fast_ema_period must be less than slow_ema_period")
        if self.lookback_bars < 2 or self.atr_period < 2:
            raise ValueError("lookback_bars and atr_period must be at least two")
        if self.compression_baseline_bars < self.lookback_bars:
            raise ValueError("compression baseline must cover the channel lookback")
        if self.breakout_persistence_bars < 2:
            raise ValueError("breakouts require at least two closed bars of persistence")
        minimum = max(
            self.slow_ema_period + 1,
            self.lookback_bars + 1,
            self.atr_period + 1,
            self.compression_baseline_bars + self.breakout_persistence_bars + 1,
        )
        if self.warmup_bars < minimum:
            raise ValueError(f"warmup_bars must be at least {minimum} for these lookbacks")
        if self.horizon_seconds < self.interval_seconds:
            raise ValueError("horizon must cover at least one bar")
        if self.min_atr_bps >= self.max_atr_bps:
            raise ValueError("min_atr_bps must be less than max_atr_bps")
        if not self.range_efficiency < self.trend_efficiency <= 1:
            raise ValueError("efficiencies must satisfy range < trend <= 1")
        if self.compression_atr_ratio > 1:
            raise ValueError("compression_atr_ratio must not exceed one")


class TimeBasedPillarTwoEngine:
    """Consume complete chronological bars; evaluate using the caller's event time.

    ``on_bar`` has no wall clock: the producer guarantees that the bar is closed.
    ``signal`` refuses to use a bar ending after the requested evaluation time.
    Missing intervals reset all state and require an entirely new warmup.
    """

    def __init__(self, symbol: str, config: TimeBasedPillarTwoConfig | None = None) -> None:
        if not symbol:
            raise ValueError("symbol must not be empty")
        self.symbol = symbol
        self.config = config or TimeBasedPillarTwoConfig()
        self._bars: deque[Bar] = deque(maxlen=self.config.warmup_bars)
        self._fast: float | None = None
        self._slow: float | None = None
        self._previous_fast: float | None = None
        self._gap_warmup = False

    def on_bar(self, bar: Bar) -> None:
        if bar.symbol != self.symbol:
            raise ValueError("symbol mismatch")
        if bar.interval_seconds != self.config.interval_seconds:
            raise ValueError("bar interval mismatch")
        start, end = ensure_utc(bar.start), ensure_utc(bar.end)
        if end - start != timedelta(seconds=self.config.interval_seconds):
            raise ValueError("bar duration must equal the configured interval")
        prices = (bar.open, bar.high, bar.low, bar.close)
        if any(
            not price.is_finite() or not isfinite(float(price)) or float(price) <= 0
            for price in prices
        ):
            raise ValueError("bar prices must be finite and positive")
        if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close):
            raise ValueError("invalid bar OHLC bounds")
        if not bar.volume.is_finite() or not isfinite(float(bar.volume)) or bar.volume < 0:
            raise ValueError("bar volume must be finite and nonnegative")
        if isinstance(bar.trades, bool) or not isinstance(bar.trades, int) or bar.trades < 0:
            raise ValueError("bar trades must be a nonnegative integer")
        if self._bars and start < self._bars[-1].end:
            raise ValueError("bars must be chronological, nonoverlapping, and unique")
        if self._bars and start > self._bars[-1].end:
            self._bars.clear()
            self._fast = self._slow = self._previous_fast = None
            self._gap_warmup = True
        self._bars.append(replace(bar, start=start, end=end))
        close = float(bar.close)
        self._previous_fast = self._fast
        self._fast = self._ema(self._fast, close, self.config.fast_ema_period)
        self._slow = self._ema(self._slow, close, self.config.slow_ema_period)
        if len(self._bars) >= self.config.warmup_bars:
            self._gap_warmup = False

    def signal(self, now: datetime) -> PillarTwoSignal:
        now = ensure_utc(now)
        if not self._bars:
            return self._signal(now, reason="warmup")
        closed_at = self._bars[-1].end
        if now < closed_at:
            return self._signal(closed_at, reason="future_bar")
        if now > closed_at + timedelta(seconds=self.config.signal_ttl_seconds):
            return self._signal(closed_at, reason="stale_bar")
        if len(self._bars) < self.config.warmup_bars:
            reason = "gap_warmup" if self._gap_warmup else "warmup"
            return self._signal(closed_at, reason=reason)

        bars = list(self._bars)
        closes = [float(bar.close) for bar in bars]
        ranges = [
            max(float(bar.high - bar.low), abs(float(bar.high) - previous),
                abs(float(bar.low) - previous))
            for previous, bar in zip(closes, bars[1:], strict=False)
        ]
        atr = fmean(ranges[-self.config.atr_period:])
        atr_bps = atr / closes[-1] * 10_000
        if atr_bps < self.config.min_atr_bps:
            return self._signal(closed_at, reason="insufficient_movement")
        # Compare the newest bar with an ATR that excludes it to detect shocks.
        previous_atr = fmean(ranges[-self.config.atr_period - 1:-1])
        if atr_bps > self.config.max_atr_bps or (
            previous_atr > 0 and ranges[-1] > previous_atr * self.config.max_bar_range_atr
        ):
            return self._signal(closed_at, regime="HIGH_VOLATILITY", reason="volatility_gate")

        window = closes[-self.config.lookback_bars - 1:]
        distance = sum(abs(right - left) for left, right in zip(window, window[1:], strict=False))
        efficiency = abs(window[-1] - window[0]) / distance if distance else 0.0
        assert self._fast is not None and self._slow is not None
        assert self._previous_fast is not None
        separation = (self._fast - self._slow) / atr
        slope = (self._fast - self._previous_fast) / atr
        horizon_scale = sqrt(self.config.horizon_seconds / self.config.interval_seconds)

        compressed, breakout = self._breakout(bars, ranges, atr)
        if breakout:
            strength = min(1.0, 0.60 + 0.25 * efficiency + 0.15 * min(abs(separation), 1.0))
            return self._signal(
                closed_at, regime="BREAKOUT_COMPRESSION", reason="persistent_channel_breakout",
                score=breakout * strength, model="breakout",
                expected_move_bps=atr_bps * horizon_scale * self.config.breakout_move_atr,
            )
        if (
            efficiency >= self.config.trend_efficiency
            and abs(separation) >= self.config.trend_separation_atr
            and separation * slope > 0
            and (closes[-1] - self._fast) * separation > 0
        ):
            strength = min(1.0, 0.40 + 0.30 * efficiency + 0.30 * min(abs(separation), 1.0))
            return self._signal(
                closed_at, regime="TRENDING", reason="aligned_ema_and_efficient_path",
                score=strength if separation > 0 else -strength, model="trend",
                expected_move_bps=atr_bps * horizon_scale * self.config.trend_move_atr,
            )
        if efficiency <= self.config.range_efficiency:
            mean = fmean(window[1:])
            deviation = pstdev(window[1:])
            z_score = (closes[-1] - mean) / deviation if deviation else 0.0
            reverses = (closes[-1] - closes[-2]) * z_score < 0
            if abs(z_score) >= self.config.min_reversion_z and reverses:
                strength = min(1.0, 0.50 + 0.30 * min(abs(z_score) / 2, 1.0)
                               + 0.20 * (1 - efficiency / self.config.range_efficiency))
                return self._signal(
                    closed_at, regime="RANGING", reason="range_deviation_reversing",
                    score=-strength if z_score > 0 else strength, model="mean_reversion",
                    expected_move_bps=min(abs(closes[-1] - mean), atr * horizon_scale)
                    / closes[-1] * 10_000,
                )
            return self._signal(
                closed_at, regime="BREAKOUT_COMPRESSION" if compressed else "RANGING",
                reason="awaiting_breakout" if compressed else "no_confirmed_reversion",
            )
        if compressed:
            return self._signal(
                closed_at, regime="BREAKOUT_COMPRESSION", reason="awaiting_breakout",
            )
        return self._signal(closed_at, reason="ambiguous_regime")

    def _breakout(self, bars: list[Bar], ranges: list[float], atr: float) -> tuple[bool, int]:
        persistence = self.config.breakout_persistence_bars
        prior = bars[-self.config.compression_baseline_bars - persistence:-persistence]
        channel = prior[-self.config.lookback_bars:]
        prior_ranges = ranges[-self.config.compression_baseline_bars - persistence:-persistence]
        baseline_atr = fmean(prior_ranges)
        recent_atr = fmean(prior_ranges[-max(2, self.config.lookback_bars // 2):])
        upper = float(max(bar.high for bar in channel))
        lower = float(min(bar.low for bar in channel))
        compressed = baseline_atr > 0 and (
            recent_atr <= baseline_atr * self.config.compression_atr_ratio
            or upper - lower <= baseline_atr * self.config.compression_range_atr
        )
        if not compressed:
            return False, 0
        latest = bars[-persistence:]
        volume = fmean(float(bar.volume) for bar in latest)
        prior_volume = fmean(float(bar.volume) for bar in channel)
        if prior_volume <= 0 or volume < prior_volume * self.config.breakout_volume_ratio:
            return True, 0
        buffer = atr * self.config.breakout_buffer_atr
        if all(float(bar.close) > upper + buffer for bar in latest):
            return True, 1
        if all(float(bar.close) < lower - buffer for bar in latest):
            return True, -1
        return True, 0

    def _signal(
        self, generated_at: datetime, *, reason: str, regime: str = "UNTRADEABLE",
        score: float = 0.0, model: str = "none", expected_move_bps: float = 0.0,
    ) -> PillarTwoSignal:
        return PillarTwoSignal(
            symbol=self.symbol,
            direction=Direction.BULLISH if score > 0 else Direction.BEARISH if score < 0
            else Direction.NEUTRAL,
            strength=abs(score), regime=regime, generated_at=generated_at,
            valid_until=generated_at + timedelta(seconds=self.config.signal_ttl_seconds),
            reasons=(reason, "microstructure_unavailable", "uncalibrated_strength"), score=score,
            expected_move_bps=expected_move_bps, horizon_seconds=self.config.horizon_seconds,
            model=model, probability=None,
        )

    @staticmethod
    def _ema(previous: float | None, value: float, period: int) -> float:
        if previous is None:
            return value
        return previous + 2 / (period + 1) * (value - previous)
