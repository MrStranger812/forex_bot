from __future__ import annotations

import math
from collections import deque


class VolatilityFeatures:
    def __init__(
        self, window: int = 60, target_low: float = 0.00002, target_high: float = 0.003
    ) -> None:
        self.returns: deque[float] = deque(maxlen=window)
        self.previous_price: float | None = None
        self.target_low = target_low
        self.target_high = target_high

    def update(self, price: float) -> tuple[float, float]:
        if self.previous_price is not None:
            self.returns.append(math.log(price / self.previous_price))
        self.previous_price = price
        if len(self.returns) < 5:
            return 0.0, 0.0
        mean = sum(self.returns) / len(self.returns)
        variance = sum((value - mean) ** 2 for value in self.returns) / (len(self.returns) - 1)
        realized = math.sqrt(variance)
        if realized < self.target_low:
            suitability = realized / self.target_low
        elif realized > self.target_high:
            suitability = max(0.0, self.target_high / realized)
        else:
            suitability = 1.0
        return realized, min(1.0, max(0.0, suitability))
