from datetime import UTC, datetime, timedelta
from decimal import Decimal

from training.build_dataset import (
    Quote,
    add_conversation,
    chronological_group_split,
    executable_return,
    label_news,
)


def test_executable_return_uses_ask_entry_bid_exit_and_fees() -> None:
    now = datetime.now(UTC)
    entry = Quote(now, Decimal("99"), Decimal("101"))
    exit_quote = Quote(now + timedelta(seconds=30), Decimal("109"), Decimal("111"))
    result = executable_return(entry, exit_quote, "bullish", Decimal("1"))
    assert result == Decimal("109") / Decimal("101") - 1 - Decimal("0.0002")


def test_labels_all_horizons_and_builds_assistant_json() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    news = {
        "published_at": now.isoformat(),
        "content_hash": "one",
        "symbols": ["BTC_USDT"],
        "headline": "Headline",
        "body": "Body",
        "source_reliability": 0.8,
        "event_type": "macro",
    }
    quotes = [Quote(now, Decimal("99"), Decimal("101"))]
    quotes.extend(
        Quote(now + timedelta(seconds=value), Decimal("109"), Decimal("111"))
        for value in (30, 60, 300, 900, 3600)
    )
    labeled = label_news(news, quotes, Decimal("1"))
    assert labeled["labels"]["300"] == "bullish"
    conversation = add_conversation(labeled, 300)
    assert [message["role"] for message in conversation["messages"]] == [
        "system",
        "user",
        "assistant",
    ]


def test_duplicate_content_hash_never_crosses_splits() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {
            "published_at": (start + timedelta(days=index)).isoformat(),
            "content_hash": str(index // 2),
        }
        for index in range(20)
    ]
    splits = chronological_group_split(rows, timedelta(0))
    split_hashes = [{row["content_hash"] for row in values} for values in splits.values()]
    assert not (split_hashes[0] & split_hashes[1])
    assert not (split_hashes[1] & split_hashes[2])
