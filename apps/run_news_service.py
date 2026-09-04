from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime

from bot.news.collectors import JsonFeedCollector
from bot.news.deduplicator import NewsDeduplicator
from bot.news.llm_client import LlmOpinionClient
from bot.storage.event_store import EventStore


async def run(feed_url: str, source: str, database: str) -> None:
    collector = JsonFeedCollector(feed_url, source, reliability=0.8)
    deduplicator = NewsDeduplicator()
    client = LlmOpinionClient(
        os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080"),
        os.environ["LLM_MODEL"],
        api_key=os.getenv("LLM_API_KEY", ""),
    )
    with EventStore(database) as store:
        async for item in collector.collect():
            if not deduplicator.accept(item):
                continue
            opinion = await client.analyze(item, datetime.now(UTC))
            stream = (
                f"pillar-one:{item.symbols[0]}" if len(item.symbols) == 1 else "pillar-one:multi"
            )
            store.append(
                stream,
                "PillarOneOpinion",
                {
                    "direction": opinion.direction,
                    "confidence": opinion.confidence,
                    "strength": opinion.strength,
                    "valid_until": opinion.valid_until.isoformat(),
                    "affected_symbols": opinion.affected_symbols,
                    "event_type": opinion.event_type,
                    "source_content_hash": opinion.source_content_hash,
                },
                opinion.generated_at,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent news opinion service")
    parser.add_argument("--feed-url", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--database", default="artifacts/news_events.sqlite3")
    args = parser.parse_args()
    asyncio.run(run(args.feed_url, args.source, args.database))


if __name__ == "__main__":
    main()
