from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import aiohttp

from .schemas import NewsItem


class JsonFeedCollector:
    """Collector for a configured JSON feed; it has no embedded vendor secret."""

    def __init__(self, url: str, source_id: str, reliability: float, api_token: str = "") -> None:
        self.url = url
        self.source_id = source_id
        self.reliability = reliability
        self.api_token = api_token

    async def collect(self) -> AsyncIterator[NewsItem]:
        headers = {"Authorization": f"Bearer {self.api_token}"} if self.api_token else {}
        timeout = aiohttp.ClientTimeout(total=10)
        async with (
            aiohttp.ClientSession(headers=headers) as session,
            session.get(self.url, timeout=timeout) as response,
        ):
            response.raise_for_status()
            payload = await response.json()
        records = payload if isinstance(payload, list) else payload.get("items", [])
        for record in records:
            headline = str(record["headline"])
            body = str(record.get("body", ""))
            published = datetime.fromisoformat(str(record["published_at"]).replace("Z", "+00:00"))
            yield NewsItem(
                source_id=self.source_id,
                canonical_url=str(record["url"]),
                published_at=published,
                received_at=datetime.now(UTC),
                symbols=tuple(str(value).upper() for value in record.get("symbols", [])),
                headline=headline,
                body=body,
                language=str(record.get("language", "en")),
                source_reliability=self.reliability,
                content_hash=NewsItem.hash_content(headline, body),
            )


def serialize_news(item: NewsItem) -> str:
    return json.dumps(
        {
            "source_id": item.source_id,
            "canonical_url": item.canonical_url,
            "published_at": item.published_at.isoformat(),
            "received_at": item.received_at.isoformat(),
            "symbols": item.symbols,
            "headline": item.headline,
            "body": item.body,
            "language": item.language,
            "source_reliability": item.source_reliability,
            "content_hash": item.content_hash,
        },
        separators=(",", ":"),
    )
