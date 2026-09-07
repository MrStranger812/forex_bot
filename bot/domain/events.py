from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from typing import Any


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class MarketEvent:
    symbol: str
    exchange_ts: datetime
    received_ts: datetime
    sequence: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange_ts", ensure_utc(self.exchange_ts))
        object.__setattr__(self, "received_ts", ensure_utc(self.received_ts))

    @property
    def latency_ms(self) -> float:
        return (self.received_ts - self.exchange_ts).total_seconds() * 1000

    def to_record(self) -> dict[str, Any]:
        result = asdict(self)
        result["event_type"] = type(self).__name__
        result["exchange_ts"] = self.exchange_ts.isoformat()
        result["received_ts"] = self.received_ts.isoformat()
        for key, value in tuple(result.items()):
            if isinstance(value, Decimal):
                result[key] = str(value)
        return result


@dataclass(frozen=True, slots=True)
class TradeEvent(MarketEvent):
    price: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")
    aggressor_side: str = "unknown"


@dataclass(frozen=True, slots=True)
class BboEvent(MarketEvent):
    bid_price: Decimal = Decimal("0")
    bid_quantity: Decimal = Decimal("0")
    ask_price: Decimal = Decimal("0")
    ask_quantity: Decimal = Decimal("0")

    @property
    def mid(self) -> Decimal:
        return (self.bid_price + self.ask_price) / 2

    @property
    def spread_bps(self) -> Decimal:
        if self.mid <= 0:
            return Decimal("Infinity")
        return (self.ask_price - self.bid_price) / self.mid * Decimal(10_000)


@dataclass(frozen=True, slots=True)
class BookDeltaEvent(MarketEvent):
    previous_sequence: int | None = None
    bids: tuple[tuple[Decimal, Decimal], ...] = ()
    asks: tuple[tuple[Decimal, Decimal], ...] = ()


@dataclass(frozen=True, slots=True)
class MarkPriceEvent(MarketEvent):
    mark_price: Decimal = Decimal("0")
    index_price: Decimal = Decimal("0")
    funding_rate: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PillarTwoSignal:
    symbol: str
    direction: Direction
    strength: float
    regime: str
    generated_at: datetime
    valid_until: datetime
    reasons: tuple[str, ...]
    score: float
    expected_move_bps: float = 0.0
    horizon_seconds: int = 0
    model: str = "legacy"
    probability: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", ensure_utc(self.generated_at))
        object.__setattr__(self, "valid_until", ensure_utc(self.valid_until))
        if not 0 <= self.strength <= 1:
            raise ValueError("strength must be within [0, 1]")
        if not isfinite(self.score):
            raise ValueError("score must be finite")
        if not isfinite(self.expected_move_bps) or self.expected_move_bps < 0:
            raise ValueError("expected_move_bps must be finite and nonnegative")
        if isinstance(self.horizon_seconds, bool) or not isinstance(self.horizon_seconds, int):
            raise ValueError("horizon_seconds must be an integer")
        if self.horizon_seconds < 0:
            raise ValueError("horizon_seconds must be nonnegative")
        if not self.model:
            raise ValueError("model must not be empty")
        if self.probability is not None and not 0 <= self.probability <= 1:
            raise ValueError("probability must be within [0, 1] when calibrated")
        if self.valid_until < self.generated_at:
            raise ValueError("valid_until must not precede generated_at")

    def is_fresh(self, now: datetime) -> bool:
        now = ensure_utc(now)
        return self.generated_at <= now <= self.valid_until
