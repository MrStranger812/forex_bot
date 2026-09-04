from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from bot.domain.events import BboEvent
from bot.domain.orders import OrderRequest, OrderState, OrderStatus, OrderType, Side
from bot.execution.paper_exchange import PaperExchange


class OrderManager:
    """Paper-safe bracket manager; live transports implement the same boundary later."""

    def __init__(self, exchange: PaperExchange) -> None:
        self.exchange = exchange
        self._pending_stops: dict[str, Decimal] = {}
        self._entry_stop_ids: dict[str, str] = {}

    @staticmethod
    def client_id(prefix: str = "ob") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:24]}"

    def submit_entry_with_stop(
        self,
        *,
        symbol: str,
        side: Side,
        quantity: Decimal,
        entry_price: Decimal,
        stop_price: Decimal,
        now: datetime,
    ) -> tuple[OrderState, OrderState | None]:
        if not self.exchange.positions[symbol].is_flat:
            raise RuntimeError("one-position-per-symbol rule")
        entry = OrderRequest(
            client_order_id=self.client_id("entry"),
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=OrderType.LIMIT,
            price=entry_price,
            post_only=True,
        )
        entry_state = self.exchange.submit(entry, now)
        stop_state: OrderState | None = None
        if entry_state.status is OrderStatus.FILLED:
            stop_state = self._submit_stop(entry, stop_price, now)
        elif entry_state.status is OrderStatus.ACCEPTED:
            self._pending_stops[entry.client_order_id] = stop_price
        return entry_state, stop_state

    def on_bbo(self, event: BboEvent) -> list[OrderState]:
        updates = self.exchange.update_bbo(event)
        created: list[OrderState] = []
        for state in updates:
            entry_id = state.request.client_order_id
            stop_price = self._pending_stops.get(entry_id)
            if state.status in {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED} and stop_price:
                old_stop_id = self._entry_stop_ids.get(entry_id)
                if old_stop_id:
                    self.exchange.cancel(old_stop_id, event.received_ts)
                protective = self._submit_stop(
                    state.request,
                    stop_price,
                    event.received_ts,
                    quantity=state.filled_quantity,
                )
                self._entry_stop_ids[entry_id] = protective.request.client_order_id
                created.append(protective)
                if state.status is OrderStatus.FILLED:
                    self._pending_stops.pop(entry_id, None)
        return updates + created

    def ensure_protective_exit(
        self, entry: OrderRequest, stop_price: Decimal, now: datetime
    ) -> OrderState:
        return self._submit_stop(entry, stop_price, now)

    def cancel_working_entries(self, now: datetime) -> list[OrderState]:
        canceled: list[OrderState] = []
        for state in self.exchange.orders.values():
            if state.status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED} and not (
                state.request.reduce_only
            ):
                canceled.append(self.exchange.cancel(state.request.client_order_id, now))
                self._pending_stops.pop(state.request.client_order_id, None)
        return canceled

    def emergency_flatten(self, now: datetime) -> list[OrderState]:
        """Use reduce-only market-style exits only for an emergency recovery path."""
        results: list[OrderState] = []
        for state in self.exchange.orders.values():
            if state.status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
                self.exchange.cancel(state.request.client_order_id, now)
        for symbol, position in self.exchange.positions.items():
            if position.is_flat:
                continue
            side = Side.SELL if position.quantity > 0 else Side.BUY
            request = OrderRequest(
                client_order_id=self.client_id("flatten"),
                symbol=symbol,
                side=side,
                quantity=abs(position.quantity),
                order_type=OrderType.MARKET,
                reduce_only=True,
            )
            results.append(self.exchange.submit(request, now))
        return results

    def _submit_stop(
        self,
        entry: OrderRequest,
        stop_price: Decimal,
        now: datetime,
        *,
        quantity: Decimal | None = None,
    ) -> OrderState:
        exit_side = Side.SELL if entry.side is Side.BUY else Side.BUY
        stop = OrderRequest(
            client_order_id=self.client_id("stop"),
            symbol=entry.symbol,
            side=exit_side,
            quantity=quantity or entry.quantity,
            order_type=OrderType.STOP,
            stop_price=stop_price,
            reduce_only=True,
        )
        # Store the trigger as accepted; never cross it as a plain market order.
        state = OrderState(
            request=stop, status=OrderStatus.ACCEPTED, created_at=now, updated_at=now
        )
        self.exchange.orders[stop.client_order_id] = state
        self.exchange.positions[entry.symbol].protective_exit_client_id = stop.client_order_id
        return state
