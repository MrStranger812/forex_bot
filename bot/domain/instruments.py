from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


def round_down(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        raise ValueError("increment must be positive")
    return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    base_asset: str
    quote_asset: str
    settlement_asset: str
    price_increment: Decimal
    quantity_increment: Decimal
    min_quantity: Decimal
    min_notional: Decimal
    contract_multiplier: Decimal = Decimal("1")
    max_leverage: Decimal = Decimal("1")
    status: str = "TRADING"

    def __post_init__(self) -> None:
        if self.quote_asset != "USDT" or self.settlement_asset != "USDT":
            raise ValueError("only USDT-margined instruments are supported")
        if any(
            value <= 0
            for value in (self.price_increment, self.quantity_increment, self.contract_multiplier)
        ):
            raise ValueError("increments and multiplier must be positive")

    def round_price(self, value: Decimal) -> Decimal:
        return round_down(value, self.price_increment)

    def round_quantity(self, value: Decimal) -> Decimal:
        return round_down(value, self.quantity_increment)

    def validate_order(self, price: Decimal, quantity: Decimal) -> tuple[bool, str]:
        if self.status != "TRADING":
            return False, "instrument_not_trading"
        if quantity < self.min_quantity:
            return False, "below_min_quantity"
        if price * quantity * self.contract_multiplier < self.min_notional:
            return False, "below_min_notional"
        if self.round_price(price) != price:
            return False, "invalid_price_increment"
        if self.round_quantity(quantity) != quantity:
            return False, "invalid_quantity_increment"
        return True, "ok"
