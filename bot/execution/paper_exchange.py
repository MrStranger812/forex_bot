from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from bot.domain.events import BboEvent
from bot.domain.instruments import Instrument
from bot.domain.orders import OrderRequest, OrderState, OrderStatus, OrderType, Side
from bot.domain.positions import Position


class PaperExchange:
    def __init__(
        self,
        instruments: dict[str, Instrument],
        maker_fee_bps: Decimal,
        taker_fee_bps: Decimal,
        starting_equity: Decimal = Decimal("0"),
    ) -> None:
        self.instruments = instruments
        self.maker_fee_bps = maker_fee_bps
        self.taker_fee_bps = taker_fee_bps
        self.orders: dict[str, OrderState] = {}
        self.positions = {symbol: Position(symbol) for symbol in instruments}
        self.last_bbo: dict[str, BboEvent] = {}
        self.fees_paid = Decimal("0")
        self.starting_equity = starting_equity
        self.realized_pnl = Decimal("0")

    @property
    def equity(self) -> Decimal:
        unrealized = Decimal("0")
        for symbol, position in self.positions.items():
            bbo = self.last_bbo.get(symbol)
            if position.is_flat or bbo is None:
                continue
            mark = bbo.mid
            price_pnl = (
                (mark - position.average_entry_price) * position.quantity
                if position.quantity > 0
                else (position.average_entry_price - mark) * abs(position.quantity)
            )
            unrealized += price_pnl * self.instruments[symbol].contract_multiplier
        return self.starting_equity + self.realized_pnl + unrealized - self.fees_paid

    def update_bbo(self, event: BboEvent) -> list[OrderState]:
        self.last_bbo[event.symbol] = event
        filled: list[OrderState] = []
        available_for_buys = event.ask_quantity
        available_for_sells = event.bid_quantity
        for state in list(self.orders.values()):
            if state.request.symbol != event.symbol or state.status not in {
                OrderStatus.ACCEPTED,
                OrderStatus.PARTIALLY_FILLED,
            }:
                continue
            if state.request.order_type is OrderType.STOP and self._stop_triggered(
                state.request, event
            ):
                self._fill(
                    state, self._fill_price(state.request, event), event.received_ts, maker=False
                )
                filled.append(state)
            elif state.request.order_type is OrderType.LIMIT and self._marketable(
                state.request, event
            ):
                available = (
                    available_for_buys if state.request.side is Side.BUY else available_for_sells
                )
                if available > 0:
                    amount = self._fill(
                        state,
                        self._fill_price(state.request, event),
                        event.received_ts,
                        maker=True,
                        fill_quantity=available,
                    )
                    if state.request.side is Side.BUY:
                        available_for_buys -= amount
                    else:
                        available_for_sells -= amount
                    filled.append(state)
        return filled

    def submit(self, request: OrderRequest, now: datetime) -> OrderState:
        if request.client_order_id in self.orders:
            raise ValueError("duplicate client-order ID")
        instrument = self.instruments[request.symbol]
        validation_price = request.price or self._fill_price(request, self.last_bbo[request.symbol])
        valid, reason = instrument.validate_order(validation_price, request.quantity)
        position = self.positions[request.symbol]
        if request.reduce_only and request.quantity > abs(position.quantity):
            valid, reason = False, "reduce_only_exceeds_position"
        status = OrderStatus.ACCEPTED if valid else OrderStatus.REJECTED
        state = OrderState(
            request=request,
            status=status,
            created_at=now,
            updated_at=now,
            rejection_reason=None if valid else reason,
        )
        self.orders[request.client_order_id] = state
        if not valid:
            return state
        bbo = self.last_bbo.get(request.symbol)
        if bbo is not None and request.order_type is OrderType.MARKET:
            self._fill(state, self._fill_price(request, bbo), now, maker=False)
        elif bbo is not None and self._marketable(request, bbo):
            if request.post_only:
                state.status = OrderStatus.REJECTED
                state.rejection_reason = "post_only_would_cross"
            else:
                self._fill(state, self._fill_price(request, bbo), now, maker=False)
        return state

    def cancel(self, client_order_id: str, now: datetime) -> OrderState:
        state = self.orders[client_order_id]
        if state.status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            state.status = OrderStatus.CANCELED
            state.updated_at = now
        return state

    @staticmethod
    def _marketable(request: OrderRequest, bbo: BboEvent) -> bool:
        if request.order_type is OrderType.MARKET:
            return True
        if request.price is None:
            return False
        return (
            request.price >= bbo.ask_price
            if request.side is Side.BUY
            else request.price <= bbo.bid_price
        )

    @staticmethod
    def _fill_price(request: OrderRequest, bbo: BboEvent) -> Decimal:
        return bbo.ask_price if request.side is Side.BUY else bbo.bid_price

    @staticmethod
    def _stop_triggered(request: OrderRequest, bbo: BboEvent) -> bool:
        if request.stop_price is None:
            return False
        if request.side is Side.SELL:
            return bbo.bid_price <= request.stop_price
        return bbo.ask_price >= request.stop_price

    def _fill(
        self,
        state: OrderState,
        price: Decimal,
        now: datetime,
        *,
        maker: bool,
        fill_quantity: Decimal | None = None,
    ) -> Decimal:
        request = state.request
        position = self.positions[request.symbol]
        if request.reduce_only and (position.is_flat or position.side is request.side):
            state.status = OrderStatus.REJECTED
            state.rejection_reason = "reduce_only_would_increase"
            return Decimal("0")
        remaining = request.quantity - state.filled_quantity
        amount = min(remaining, fill_quantity) if fill_quantity is not None else remaining
        old_notional = state.filled_quantity * (state.average_fill_price or Decimal("0"))
        realized = position.apply_fill(request.side, amount, price)
        self.realized_pnl += realized * self.instruments[request.symbol].contract_multiplier
        state.filled_quantity += amount
        state.status = (
            OrderStatus.FILLED
            if state.filled_quantity == request.quantity
            else OrderStatus.PARTIALLY_FILLED
        )
        state.average_fill_price = (old_notional + amount * price) / state.filled_quantity
        state.updated_at = now
        rate = self.maker_fee_bps if maker else self.taker_fee_bps
        self.fees_paid += price * amount * rate / Decimal(10_000)
        return amount
