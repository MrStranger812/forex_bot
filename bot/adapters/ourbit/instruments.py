from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.domain.instruments import Instrument


class InstrumentMappingError(ValueError):
    pass


def _decimal(payload: dict[str, Any], *keys: str) -> Decimal:
    for key in keys:
        if key in payload and payload[key] is not None:
            return Decimal(str(payload[key]))
    raise InstrumentMappingError(f"missing any of fields: {', '.join(keys)}")


def map_instrument(payload: dict[str, Any]) -> Instrument:
    """Map only an explicitly understood normalized or exchange instrument payload."""
    symbol = str(payload.get("symbol", "")).replace("/", "_").upper()
    if not symbol:
        raise InstrumentMappingError("missing symbol")
    base, separator, quote = symbol.partition("_")
    if not separator:
        quote = str(payload.get("quoteAsset", payload.get("quote_asset", "USDT"))).upper()
        if symbol.endswith(quote):
            base = symbol[: -len(quote)]
            symbol = f"{base}_{quote}"
    settlement = str(payload.get("settleAsset", payload.get("settlement_asset", quote))).upper()
    return Instrument(
        symbol=symbol,
        base_asset=str(payload.get("baseAsset", payload.get("base_asset", base))).upper(),
        quote_asset=quote,
        settlement_asset=settlement,
        price_increment=_decimal(payload, "tickSize", "price_increment"),
        quantity_increment=_decimal(payload, "stepSize", "quantity_increment"),
        min_quantity=_decimal(payload, "minQty", "min_quantity"),
        min_notional=_decimal(payload, "minNotional", "min_notional"),
        contract_multiplier=_decimal(payload, "contractSize", "contract_multiplier"),
        max_leverage=_decimal(payload, "maxLeverage", "max_leverage"),
        status=str(payload.get("status", "UNKNOWN")).upper(),
    )
