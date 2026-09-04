from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.adapters.ourbit.public_ws import (
    DepthSynchronizer,
    EventFreshnessFilter,
    OurbitPublicWebSocket,
    SequencedOrderBook,
    SequenceGap,
)
from bot.domain.events import BookDeltaEvent, TradeEvent


def delta(sequence: int, previous: int | None) -> BookDeltaEvent:
    now = datetime.now(UTC)
    return BookDeltaEvent(
        "BTC_USDT",
        now,
        now,
        sequence,
        previous_sequence=previous,
        bids=((Decimal("100"), Decimal("2")),),
        asks=((Decimal("101"), Decimal("3")),),
    )


def test_snapshot_and_contiguous_delta() -> None:
    book = SequencedOrderBook.empty("BTC_USDT")
    book.load_snapshot(10, [(Decimal("99"), Decimal("1"))], [(Decimal("102"), Decimal("1"))])
    book.apply(delta(11, 10))
    assert book.best_bid == (Decimal("100"), Decimal("2"))
    assert book.best_ask == (Decimal("101"), Decimal("3"))


@pytest.mark.parametrize(("sequence", "previous"), [(12, 10), (11, 9), (10, 10)])
def test_gap_invalidates_book(sequence: int, previous: int | None) -> None:
    book = SequencedOrderBook.empty("BTC_USDT")
    book.load_snapshot(10, [], [])
    with pytest.raises(SequenceGap):
        book.apply(delta(sequence, previous))
    assert not book.valid


def test_zero_quantity_removes_level() -> None:
    now = datetime.now(UTC)
    book = SequencedOrderBook.empty("BTC_USDT")
    book.load_snapshot(1, [(Decimal("100"), Decimal("1"))], [])
    book.apply(
        BookDeltaEvent(
            "BTC_USDT", now, now, 2, previous_sequence=1, bids=((Decimal("100"), Decimal("0")),)
        )
    )
    assert book.best_bid is None


def test_freshness_rejects_stale_and_duplicate() -> None:
    now = datetime.now(UTC)
    filter_ = EventFreshnessFilter(timedelta(seconds=1))
    fresh = TradeEvent("BTC_USDT", now, now, price=Decimal("1"), quantity=Decimal("1"))
    assert filter_.accept(fresh) == (True, "ok")
    assert filter_.accept(fresh) == (False, "out_of_order")
    stale = TradeEvent(
        "ETH_USDT", now - timedelta(seconds=2), now, price=Decimal("1"), quantity=Decimal("1")
    )
    assert filter_.accept(stale) == (False, "stale")


def test_depth_synchronizer_replays_buffer_after_snapshot() -> None:
    sync = DepthSynchronizer("BTC_USDT")
    assert not sync.on_delta(delta(11, 10))
    sync.apply_snapshot(
        10,
        [(Decimal("99"), Decimal("1"))],
        [(Decimal("102"), Decimal("1"))],
    )
    assert sync.book.sequence == 11
    assert sync.book.best_bid == (Decimal("100"), Decimal("2"))


def test_depth_buffer_overflow_fails_closed() -> None:
    sync = DepthSynchronizer("BTC_USDT", max_buffered_deltas=1)
    sync.on_delta(delta(1, 0))
    with pytest.raises(SequenceGap):
        sync.on_delta(delta(2, 1))


async def _ignore_payload(*_: object) -> None:
    return None


@pytest.mark.parametrize("topics", [[], [""], [str(index) for index in range(31)]])
def test_websocket_subscription_limits(topics: list[str]) -> None:
    with pytest.raises(ValueError):
        OurbitPublicWebSocket("wss://example.test/ws", topics, _ignore_payload)
