from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bot.domain.events import BboEvent
from bot.domain.instruments import Instrument, round_down
from bot.domain.orders import OrderRequest, OrderType, Side
from bot.domain.positions import Position


def instrument(**overrides: object) -> Instrument:
    values = {
        "symbol": "BTC_USDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "settlement_asset": "USDT",
        "price_increment": Decimal("0.1"),
        "quantity_increment": Decimal("0.001"),
        "min_quantity": Decimal("0.001"),
        "min_notional": Decimal("5"),
        "max_leverage": Decimal("2"),
    }
    values.update(overrides)
    return Instrument(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "increment", "expected"),
    [("1.29", "0.1", "1.2"), ("0.0099", "0.001", "0.009"), ("3", "1", "3")],
)
def test_round_down(value: str, increment: str, expected: str) -> None:
    assert round_down(Decimal(value), Decimal(increment)) == Decimal(expected)


@pytest.mark.parametrize(
    ("price", "quantity", "reason"),
    [
        ("10", "0.000", "below_min_quantity"),
        ("10", "0.001", "below_min_notional"),
        ("10.01", "1", "invalid_price_increment"),
    ],
)
def test_instrument_validation(price: str, quantity: str, reason: str) -> None:
    assert instrument().validate_order(Decimal(price), Decimal(quantity)) == (False, reason)


def test_non_usdt_instrument_rejected() -> None:
    with pytest.raises(ValueError):
        instrument(quote_asset="USD", settlement_asset="USD")


def test_limit_requires_price() -> None:
    with pytest.raises(ValueError):
        OrderRequest("id", "BTC_USDT", Side.BUY, Decimal("1"), OrderType.LIMIT)


def test_position_handles_open_reduce_and_flip() -> None:
    position = Position("BTC_USDT")
    position.apply_fill(Side.BUY, Decimal("2"), Decimal("100"))
    position.apply_fill(Side.BUY, Decimal("1"), Decimal("130"))
    assert position.quantity == 3
    assert position.average_entry_price == 110
    position.apply_fill(Side.SELL, Decimal("4"), Decimal("90"))
    assert position.quantity == -1
    assert position.average_entry_price == 90


def test_bbo_spread() -> None:
    now = datetime.now(UTC)
    event = BboEvent("BTC_USDT", now, now, bid_price=Decimal("99"), ask_price=Decimal("101"))
    assert event.mid == 100
    assert event.spread_bps == 200


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValueError):
        BboEvent("BTC_USDT", datetime.now(), datetime.now())
