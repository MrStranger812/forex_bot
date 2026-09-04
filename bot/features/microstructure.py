from __future__ import annotations

from collections import deque
from decimal import Decimal


def book_imbalance(bid_depth: Decimal, ask_depth: Decimal) -> float:
    total = bid_depth + ask_depth
    if total <= 0:
        return 0.0
    return float((bid_depth - ask_depth) / total)


def microprice(
    bid_price: Decimal,
    bid_quantity: Decimal,
    ask_price: Decimal,
    ask_quantity: Decimal,
) -> Decimal:
    total = bid_quantity + ask_quantity
    if total <= 0:
        return (bid_price + ask_price) / 2
    return (ask_price * bid_quantity + bid_price * ask_quantity) / total


class TradeFlow:
    def __init__(self, window: int = 100) -> None:
        self.signed_quantities: deque[Decimal] = deque(maxlen=window)

    def update(self, side: str, quantity: Decimal) -> float:
        signed = (
            quantity
            if side.lower() == "buy"
            else -quantity
            if side.lower() == "sell"
            else Decimal("0")
        )
        self.signed_quantities.append(signed)
        gross = sum((abs(value) for value in self.signed_quantities), Decimal("0"))
        return float(sum(self.signed_quantities, Decimal("0")) / gross) if gross else 0.0


def liquidity_quality(
    spread_bps: float, max_spread_bps: float, depth_notional: float, min_depth: float
) -> float:
    if max_spread_bps <= 0 or min_depth <= 0:
        raise ValueError("liquidity thresholds must be positive")
    spread_score = max(0.0, 1.0 - spread_bps / max_spread_bps)
    depth_score = min(1.0, depth_notional / min_depth)
    return spread_score * depth_score
