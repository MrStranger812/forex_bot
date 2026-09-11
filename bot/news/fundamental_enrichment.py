"""Public calendar snapshots and bounded extraction of official Fed article text."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from bot.domain.events import ensure_utc
from bot.news.fundamental_sources import (
    ParsedBatch,
    canonical_url,
    digest,
    parse_timestamp,
    plain_text,
)


def calendar_value(raw: str) -> dict[str, str | None]:
    """Keep percent units and unspecified numeric dimensions distinct; never guess units."""
    value = raw.strip()
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)([%KMBT]?)", value)
    if not match:
        return {
            "raw": raw,
            "value": None,
            "unit": None,
            "status": "missing" if not value else "unparsed",
        }
    number = Decimal(match[1])
    suffix = match[2]
    multiplier = {"K": 3, "M": 6, "B": 9, "T": 12}.get(suffix, 0)
    return {
        "raw": raw,
        "value": str(number * (Decimal(10) ** multiplier)),
        "unit": "percent" if suffix == "%" else "number_dimension_unspecified",
        "status": "parsed",
    }


def parse_forex_factory(payload: bytes, received_at: datetime) -> ParsedBatch:
    ensure_utc(received_at)
    items = json.loads(payload)
    if not isinstance(items, list):
        raise ValueError("calendar export must be a JSON array")
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        try:
            if not isinstance(item, dict):
                raise ValueError("calendar event must be an object")
            title = plain_text(item["title"])
            currency = item["country"]
            scheduled = parse_timestamp(item["date"])
            if not title or (currency != "All" and not re.fullmatch(r"[A-Z]{3}", currency)):
                raise ValueError("missing event title or invalid currency")
            forecast = calendar_value(item.get("forecast", ""))
            prior = calendar_value(item.get("previous", ""))
            # The public export has no stable provider ID. Same-day rescheduling
            # is versioned; cross-day moves require explicit reconciliation.
            native_id = digest([currency, title, item["date"][:10]])
            records.append(
                {
                    "kind": "macro_calendar",
                    "native_id": native_id,
                    "headline": title,
                    "currency": None if currency == "All" else currency,
                    "scheduled_at": scheduled.isoformat(),
                    "published_at": None,
                    "provider_datetime": item["date"],
                    "impact": item.get("impact"),
                    "consensus": forecast["value"],
                    "consensus_detail": forecast,
                    "prior": prior["value"],
                    "prior_detail": prior,
                    "actual": None,
                    "surprise": None,
                    "consensus_kind": "provider_calendar_forecast",
                    "actual_status": "not_available_in_weekly_export",
                    "identity_quality": "title_currency_provider_date_no_stable_id",
                    "vintage_status": "observed_snapshot_not_historical_vintage",
                }
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            rejected.append({"index": index, "error": str(exc)})
    return ParsedBatch(records, rejected)


class _FedArticle(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.heading_depth = 0
        self.hidden = 0
        self.parts: list[str] = []
        self.closed = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "div":
            if self.depth:
                self.depth += 1
                if "heading" in (values.get("class") or "").split():
                    self.heading_depth = self.depth
            elif values.get("id") == "article" and not self.closed:
                self.depth = 1
        if self.depth and tag in {"script", "style", "noscript"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth and tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "div" and self.depth:
            if self.depth == self.heading_depth:
                self.heading_depth = 0
            self.depth -= 1
            if not self.depth:
                self.closed = True

    def handle_data(self, data: str) -> None:
        if self.depth and not self.heading_depth and not self.hidden:
            self.parts.append(data)


def parse_fed_article(payload: bytes, received_at: datetime, url: str) -> ParsedBatch:
    received_at = ensure_utc(received_at)
    url = canonical_url(url)
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != "www.federalreserve.gov"
        or not re.fullmatch(r"/newsevents/pressreleases/monetary\d{8}[a-z]\d?\.htm", parts.path)
    ):
        raise ValueError("article URL must be an official monetary-policy release")
    text = payload.decode("utf-8-sig")
    date_match = re.search(r"class=[\"\']article__time[\"\'][^>]*>([^<]+)", text)
    title_match = re.search(r"<h3\s+class=[\"\']title[\"\'][^>]*>(.*?)</h3>", text, re.S)
    if not date_match or not title_match:
        raise ValueError("missing official article date/title markers")
    day = datetime.strptime(plain_text(date_match[1]), "%B %d, %Y").date()
    if day > received_at.date():
        raise ValueError("future article date")
    release = re.search(r"For release at (\d{1,2}):(\d{2})\s+([ap])\.m\.\s+(EDT|EST)", text)
    published: datetime | None = None
    if release:
        hour, minute = int(release[1]), int(release[2])
        if not 1 <= hour <= 12 or not 0 <= minute <= 59:
            raise ValueError("invalid article release time")
        hour = hour % 12 + (12 if release[3] == "p" else 0)
        offset = timezone(timedelta(hours=-4 if release[4] == "EDT" else -5))
        published = datetime(day.year, day.month, day.day, hour, minute, tzinfo=offset).astimezone(
            UTC
        )
        if published > received_at:
            raise ValueError("future article release")
    parser = _FedArticle()
    parser.feed(text)
    body = " ".join(" ".join(parser.parts).split())
    headline = plain_text(title_match[1])
    if not parser.closed or len(body) < 100 or not headline:
        raise ValueError("missing or incomplete article body")
    return ParsedBatch(
        [
            {
                "kind": "news_document",
                "native_id": url,
                "canonical_url": url,
                "headline": headline,
                "body": body,
                "body_scope": "publisher_article_text",
                "language": "en",
                "publication_date": day.isoformat(),
                "published_at": published.isoformat() if published else None,
                "publication_time_quality": "explicit_release_timezone"
                if published
                else "date_only",
                "content_hash": digest(" ".join((headline + " " + body).lower().split())),
                "actual": None,
                "consensus": None,
                "prior": None,
                "surprise": None,
            }
        ],
        [],
    )
