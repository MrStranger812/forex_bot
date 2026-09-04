from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bot.adapters.ourbit.public_ws import SequencedOrderBook, SequenceGap
from bot.domain.events import BookDeltaEvent


@pytest.mark.parametrize("jump", range(2, 22))
def test_any_dropped_depth_message_invalidates_book(jump: int) -> None:
    now = datetime.now(UTC)
    book = SequencedOrderBook.empty("BTC_USDT")
    book.load_snapshot(100, [(Decimal("1"), Decimal("1"))], [])
    with pytest.raises(SequenceGap):
        book.apply(
            BookDeltaEvent(
                "BTC_USDT",
                now,
                now,
                100 + jump,
                previous_sequence=100,
                bids=((Decimal("1"), Decimal("2")),),
            )
        )
    assert not book.valid
