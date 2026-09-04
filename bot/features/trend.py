from __future__ import annotations

import math
from collections import deque


class Ema:
    def __init__(self, period: int) -> None:
        if period < 2:
            raise ValueError("EMA period must be at least 2")
        self.alpha = 2 / (period + 1)
        self.value: float | None = None

    def update(self, price: float) -> float:
        self.value = (
            price if self.value is None else self.alpha * price + (1 - self.alpha) * self.value
        )
        return self.value


class TrendFeatures:
    def __init__(
        self, fast_period: int = 8, slow_period: int = 21, normalize_window: int = 120
    ) -> None:
        self.fast = Ema(fast_period)
        self.slow = Ema(slow_period)
        self.previous_slow: float | None = None
        self.returns: deque[float] = deque(maxlen=normalize_window)
        self.previous_price: float | None = None

    def update(self, price: float) -> tuple[float, float]:
        if price <= 0:
            raise ValueError("price must be positive")
        if self.previous_price is not None:
            self.returns.append(math.log(price / self.previous_price))
        self.previous_price = price
        fast = self.fast.update(price)
        previous_slow = self.slow.value
        slow = self.slow.update(price)
        slope = 0.0 if previous_slow is None else (slow - previous_slow) / slow
        scale = _robust_scale(self.returns)
        ema_alignment = (fast - slow) / slow
        normalized_ema = math.tanh((ema_alignment + slope) / max(scale, 1e-8))
        directional_strength = math.tanh(slope / max(scale, 1e-8))
        self.previous_slow = previous_slow
        return normalized_ema, directional_strength


def _robust_scale(values: deque[float]) -> float:
    if len(values) < 5:
        return 1e-4
    ordered = sorted(values)
    median = ordered[len(ordered) // 2]
    deviations = sorted(abs(value - median) for value in ordered)
    return max(deviations[len(deviations) // 2] * 1.4826, 1e-8)
