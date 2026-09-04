from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from bot.domain.orders import TERMINAL_ORDER_STATUSES, OrderState, OrderStatus


class InvalidOrderTransition(RuntimeError):
    pass


ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {OrderStatus.SUBMITTING},
    OrderStatus.SUBMITTING: {OrderStatus.ACCEPTED, OrderStatus.REJECTED, OrderStatus.UNKNOWN},
    OrderStatus.UNKNOWN: {
        OrderStatus.ACCEPTED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELED,
        OrderStatus.FILLED,
    },
    OrderStatus.ACCEPTED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.CANCELED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_PENDING,
        OrderStatus.CANCELED,
    },
    OrderStatus.CANCEL_PENDING: {
        OrderStatus.CANCELED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
    },
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELED: set(),
    OrderStatus.REJECTED: set(),
}


class Reconciler:
    def __init__(self) -> None:
        self.orders: dict[str, OrderState] = {}

    def add(self, state: OrderState) -> None:
        client_id = state.request.client_order_id
        if client_id in self.orders:
            raise ValueError("duplicate client-order ID")
        self.orders[client_id] = state

    def transition(
        self,
        client_order_id: str,
        status: OrderStatus,
        now: datetime,
        *,
        filled_quantity: Decimal | None = None,
        average_fill_price: Decimal | None = None,
        exchange_order_id: str | None = None,
        rejection_reason: str | None = None,
    ) -> OrderState:
        state = self.orders[client_order_id]
        if status not in ALLOWED_TRANSITIONS[state.status]:
            raise InvalidOrderTransition(f"{state.status} -> {status}")
        if filled_quantity is not None:
            if filled_quantity < state.filled_quantity or filled_quantity > state.request.quantity:
                raise InvalidOrderTransition("invalid cumulative fill quantity")
            state.filled_quantity = filled_quantity
        state.status = status
        state.updated_at = now
        state.average_fill_price = average_fill_price or state.average_fill_price
        state.exchange_order_id = exchange_order_id or state.exchange_order_id
        state.rejection_reason = rejection_reason
        return state

    @property
    def has_unknown(self) -> bool:
        return any(state.status is OrderStatus.UNKNOWN for state in self.orders.values())

    def working(self, symbol: str | None = None) -> list[OrderState]:
        return [
            state
            for state in self.orders.values()
            if state.status not in TERMINAL_ORDER_STATUSES
            and (symbol is None or state.request.symbol == symbol)
        ]
