from decimal import Decimal

from bot.domain.instruments import Instrument


class SizingRejected(ValueError):
    pass


def position_size(
    *,
    equity: Decimal,
    risk_fraction: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    instrument: Instrument,
    leverage: Decimal = Decimal("1"),
) -> Decimal:
    if equity <= 0:
        raise SizingRejected("equity must be positive")
    if not Decimal("0") < risk_fraction <= Decimal("0.01"):
        raise SizingRejected("risk_fraction must be within (0, 0.01]")
    if leverage <= 0 or leverage > min(Decimal("2"), instrument.max_leverage):
        raise SizingRejected("leverage exceeds safety or instrument limit")
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        raise SizingRejected("stop distance must be positive")
    raw = equity * risk_fraction / (stop_distance * instrument.contract_multiplier)
    margin_cap = equity * leverage / (entry_price * instrument.contract_multiplier)
    quantity = instrument.round_quantity(min(raw, margin_cap))
    valid, reason = instrument.validate_order(instrument.round_price(entry_price), quantity)
    if not valid:
        raise SizingRejected(reason)
    return quantity
