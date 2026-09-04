from dataclasses import dataclass
from decimal import Decimal

from .orders import Side


@dataclass(slots=True)
class Position:
    symbol: str
    quantity: Decimal = Decimal("0")
    average_entry_price: Decimal = Decimal("0")
    protective_exit_client_id: str | None = None

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def side(self) -> Side | None:
        if self.quantity > 0:
            return Side.BUY
        if self.quantity < 0:
            return Side.SELL
        return None

    def apply_fill(self, side: Side, quantity: Decimal, price: Decimal) -> Decimal:
        signed = quantity if side is Side.BUY else -quantity
        if self.quantity == 0 or self.quantity * signed > 0:
            old_notional = abs(self.quantity) * self.average_entry_price
            new_notional = quantity * price
            self.quantity += signed
            self.average_entry_price = (old_notional + new_notional) / abs(self.quantity)
            return Decimal("0")
        closing_quantity = min(abs(self.quantity), quantity)
        realized = (
            (price - self.average_entry_price) * closing_quantity
            if self.quantity > 0
            else (self.average_entry_price - price) * closing_quantity
        )
        if abs(signed) > abs(self.quantity):
            self.quantity += signed
            self.average_entry_price = price
        else:
            self.quantity += signed
            if self.quantity == 0:
                self.average_entry_price = Decimal("0")
                self.protective_exit_client_id = None
        return realized
