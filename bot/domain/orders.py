from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .events import ensure_utc


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"
    STOP = "stop"


class OrderStatus(StrEnum):
    CREATED = "created"
    SUBMITTING = "submitting"
    UNKNOWN = "unknown"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELED = "canceled"
    REJECTED = "rejected"


TERMINAL_ORDER_STATUSES = {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED}


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    order_type: OrderType
    price: Decimal | None = None
    stop_price: Decimal | None = None
    post_only: bool = False
    reduce_only: bool = False

    def __post_init__(self) -> None:
        if not self.client_order_id or len(self.client_order_id) > 36:
            raise ValueError("client_order_id must contain 1-36 characters")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.order_type is OrderType.LIMIT and self.price is None:
            raise ValueError("limit orders require a price")
        if self.post_only and self.order_type is not OrderType.LIMIT:
            raise ValueError("post_only requires a limit order")


@dataclass(slots=True)
class OrderState:
    request: OrderRequest
    status: OrderStatus
    created_at: datetime
    updated_at: datetime
    exchange_order_id: str | None = None
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal | None = None
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)

    @property
    def unresolved(self) -> bool:
        return self.status is OrderStatus.UNKNOWN
