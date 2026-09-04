import json
from datetime import UTC, datetime

import pytest

from bot.domain.events import Direction
from bot.news.deduplicator import NewsDeduplicator
from bot.news.schemas import NewsItem, PillarOneOpinion, canonicalize_url


def news(url: str = "https://EXAMPLE.com/a?utm=x", body: str = "Body") -> NewsItem:
    now = datetime.now(UTC)
    return NewsItem(
        "source",
        url,
        now,
        now,
        ("BTC_USDT",),
        "Headline",
        body,
        "en",
        0.8,
        NewsItem.hash_content("Headline", body),
    )


def valid_payload(**changes: object) -> str:
    payload = {
        "direction": "bearish",
        "confidence": 0.74,
        "strength": 0.66,
        "horizon_seconds": 600,
        "affected_symbols": ["BTC_USDT"],
        "event_type": "regulatory",
        "abstain": False,
    }
    payload.update(changes)
    return json.dumps(payload)


def test_url_canonicalization_drops_query() -> None:
    assert canonicalize_url("HTTPS://Example.COM/a/?utm=x") == "https://example.com/a"


def test_duplicate_hash_or_url_rejected() -> None:
    dedupe = NewsDeduplicator()
    assert dedupe.accept(news())
    assert not dedupe.accept(news(url="https://elsewhere.test/story"))


def test_valid_opinion() -> None:
    opinion = PillarOneOpinion.from_json(
        valid_payload(), generated_at=datetime.now(UTC), source_content_hash="x"
    )
    assert opinion.direction is Direction.BEARISH


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        valid_payload(confidence=1.1),
        valid_payload(direction="sideways"),
        valid_payload(affected_symbols=["DOGE_USDT"]),
        valid_payload(abstain="false"),
        valid_payload(direction="neutral", abstain=False),
        valid_payload(extra=1),
        valid_payload(horizon_seconds=0),
    ],
)
def test_invalid_opinions_fail_closed(payload: str) -> None:
    with pytest.raises(ValueError):
        PillarOneOpinion.from_json(payload, generated_at=datetime.now(UTC), source_content_hash="x")
