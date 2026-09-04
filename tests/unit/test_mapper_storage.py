from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bot.adapters.ourbit.instruments import InstrumentMappingError, map_instrument
from bot.adapters.ourbit.mapper import PayloadMappingError, map_bbo, map_depth, map_mark, map_trade
from bot.storage.event_store import EventStore


def test_market_payload_mappers() -> None:
    now = datetime.now(UTC)
    base = {"symbol": "BTC/USDT", "timestamp": 1_700_000_000_000, "sequence": 1}
    assert (
        map_trade({**base, "price": "100", "quantity": "2", "aggressor_side": "BUY"}, now).price
        == 100
    )
    assert (
        map_bbo(
            {
                **base,
                "bid_price": "99",
                "bid_quantity": "1",
                "ask_price": "101",
                "ask_quantity": "2",
            },
            now,
        ).mid
        == 100
    )
    assert map_depth(
        {**base, "previous_sequence": 0, "bids": [["99", "1"]], "asks": []}, now
    ).bids == ((Decimal("99"), Decimal("1")),)
    assert map_mark(
        {**base, "mark_price": "100", "index_price": "99", "funding_rate": "0.0001"}, now
    ).funding_rate == Decimal("0.0001")


@pytest.mark.parametrize(
    "payload", [{}, {"timestamp": "bad", "symbol": "BTC_USDT", "price": 1, "quantity": 1}]
)
def test_bad_market_payload(payload: dict[str, object]) -> None:
    with pytest.raises(PayloadMappingError):
        map_trade(payload, datetime.now(UTC))


def test_instrument_mapping_and_required_metadata() -> None:
    mapped = map_instrument(
        {
            "symbol": "BTCUSDT",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "settleAsset": "USDT",
            "tickSize": "0.1",
            "stepSize": "0.001",
            "minQty": "0.001",
            "minNotional": "5",
            "contractSize": "1",
            "maxLeverage": "100",
            "status": "TRADING",
        }
    )
    assert mapped.symbol == "BTC_USDT"
    with pytest.raises(InstrumentMappingError):
        map_instrument({"symbol": "BTCUSDT"})


def test_event_store_round_trip(tmp_path) -> None:
    path = tmp_path / "events.sqlite3"
    with EventStore(path) as store:
        first = store.append("orders", "Accepted", {"price": Decimal("1.2")})
        second = store.append("orders", "Filled", {"quantity": "1"})
        assert second > first
        records = store.read("orders", after_id=first)
        assert [record["event_type"] for record in records] == ["Filled"]
