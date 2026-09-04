import math
from collections import deque


class MomentumFeatures:
    def __init__(self, period: int = 12) -> None:
        if period < 2:
            raise ValueError("period must be at least 2")
        self.required_values = period + 1
        self.prices: deque[float] = deque(maxlen=period + 1)

    def update(self, price: float) -> float:
        self.prices.append(price)
        if len(self.prices) < self.required_values:
            return 0.0
        roc = math.log(self.prices[-1] / self.prices[0])
        return math.tanh(roc * 250)
