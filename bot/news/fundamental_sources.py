"""Pure parsers for official gold-relevant releases, calendars, and daily curves.

No direction labels are inferred. Publication/observation time never substitutes
for the time this collector first received a version.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bot.domain.events import ensure_utc


@dataclass(frozen=True)
class ParsedBatch:
    records: list[dict[str, Any]]
    rejected: list[dict[str, Any]]


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain_text(value: str) -> str:
    parser = _Text()
    parser.feed(value)
    return " ".join(html.unescape(" ".join(parser.parts)).split())


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
        raise ValueError("invalid public source URL")
    # Keep meaningful query parameters: different stories may share a path.
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def parse_timestamp(value: str) -> datetime:
    # These explicit European offsets occur in UN Geneva's published RSS.
    # Unknown abbreviations and missing zones still fail; no host-local default.
    value = re.sub(r"\s+CEST$", " +0200", value.strip())
    value = re.sub(r"\s+CET$", " +0100", value)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(value.strip())
    return ensure_utc(parsed)


def _xml(payload: bytes) -> ET.Element:
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", payload, re.IGNORECASE):
        raise ValueError("DTD/entity declarations are not accepted")
    return ET.fromstring(payload)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _fields(element: ET.Element) -> dict[str, str]:
    return {_local(child.tag): "".join(child.itertext()).strip() for child in element}


def _news(element: ET.Element, received_at: datetime) -> dict[str, Any]:
    fields = _fields(element)
    headline = plain_text(fields.get("title", ""))
    body = plain_text(fields.get("description", fields.get("summary", fields.get("content", ""))))
    link = fields.get("link", "")
    if not link:
        link = next(
            (
                child.get("href", "")
                for child in element
                if _local(child.tag) == "link" and child.get("rel", "alternate") == "alternate"
            ),
            "",
        )
    link = canonical_url(link)
    published = parse_timestamp(fields.get("pubDate", fields.get("published", "")))
    if not headline or published > received_at:
        raise ValueError("missing headline or future publication")
    content_hash = digest(" ".join((headline + " " + body).lower().split()))
    return {
        "kind": "news",
        "native_id": fields.get("guid", fields.get("id", link)),
        "canonical_url": link,
        "published_at": published.isoformat(),
        "headline": headline,
        "body": body,
        "language": "en",
        "body_scope": "publisher_feed_summary",
        "content_hash": content_hash,
        "actual": None,
        "consensus": None,
        "prior": None,
        "surprise": None,
    }


def parse_rss(payload: bytes, received_at: datetime) -> ParsedBatch:
    received_at = ensure_utc(received_at)
    root = _xml(payload)
    if _local(root.tag) not in {"rss", "feed", "RDF"}:
        raise ValueError("response is not an RSS/Atom feed")
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, element in enumerate(root.iter()):
        if _local(element.tag) not in {"item", "entry"}:
            continue
        try:
            records.append(_news(element, received_at))
        except (ValueError, TypeError, OverflowError) as exc:
            rejected.append({"item_index": index, "reason": str(exc)})
    return ParsedBatch(records, rejected)


def parse_fed_archive(payload: bytes, received_at: datetime) -> ParsedBatch:
    """Official website's historical index; its naive dates are not release timestamps."""
    received_at = ensure_utc(received_at)
    items = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(items, list):
        raise ValueError("Fed archive must be a list")
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        try:
            if isinstance(item, dict) and set(item) == {"updateDate"}:
                continue  # Publisher's feed metadata, not an article.
            date_only = " " not in item["d"].strip()
            format_string = "%m/%d/%Y" if date_only else "%m/%d/%Y %I:%M:%S %p"
            local = datetime.strptime(item["d"].strip(), format_string)
            if local.date() > received_at.date():
                raise ValueError("future archive publication date")
            link = item.get("l") or item.get("stub")
            if not isinstance(link, str) or not link:
                raise ValueError("missing archive article link")
            # Retain the publisher's actual linked URL, including linked agencies.
            # The collector does not fetch or execute these article links.
            url = canonical_url(urljoin("https://www.federalreserve.gov", link))
            title = plain_text(item["t"])
            if not title:
                raise ValueError("missing archive headline")
            records.append(
                {
                    "kind": "news_archive",
                    "native_id": url,
                    "canonical_url": url,
                    "headline": title,
                    "body": "",
                    "body_scope": "headline_only",
                    "event_category": item["pt"],
                    "publication_date": local.date().isoformat(),
                    "publisher_local_time": item["d"],
                    "published_at": None,
                    "publication_time_quality": (
                        "date_only" if date_only else "timezone_unspecified_in_archive"
                    ),
                    "content_hash": digest(title.lower()),
                    "language": "en",
                }
            )
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            rejected.append({"item_index": index, "reason": str(exc)})
    return ParsedBatch(records, rejected)


def parse_treasury(payload: bytes, received_at: datetime, year: int) -> ParsedBatch:
    received_at = ensure_utc(received_at)
    root = _xml(payload)
    if _local(root.tag) != "feed":
        raise ValueError("response is not a Treasury Atom feed")
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, entry in enumerate(root):
        if _local(entry.tag) != "entry":
            continue
        try:
            props = next(node for node in entry.iter() if _local(node.tag) == "properties")
            fields = _fields(props)
            observed = date.fromisoformat(fields["NEW_DATE"][:10])
            if observed.year != year or observed > received_at.date():
                raise ValueError("Treasury observation outside requested year or in future")
            values: dict[str, str | None] = {}
            for key, value in fields.items():
                if key.startswith(("BC_", "TC_")):
                    parsed = Decimal(value) if value else None
                    if parsed is not None and not parsed.is_finite():
                        raise ValueError("non-finite Treasury yield")
                    values[key] = str(parsed) if parsed is not None else None
            if not values or not any(value is not None for value in values.values()):
                raise ValueError("no finite Treasury observations")
            records.append(
                {
                    "kind": "yield_curve",
                    "native_id": observed.isoformat(),
                    "observation_date": observed.isoformat(),
                    "published_at": None,
                    "publication_time_quality": "not_provided",
                    "values": values,
                    "units": "percent_per_annum",
                    "vintage_status": "latest_snapshot_not_historical_vintage",
                }
            )
        except (KeyError, ValueError, StopIteration, ArithmeticError) as exc:
            rejected.append({"item_index": index, "reason": str(exc)})
    return ParsedBatch(records, rejected)


def _ical_time(key: str, value: str) -> datetime:
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    match = re.search(r'(?:^|;)TZID="?([^;"]+)', key)
    if not match:
        raise ValueError("calendar time needs UTC or a named timezone")
    local = datetime.strptime(value, "%Y%m%dT%H%M%S")
    zone = ZoneInfo(match.group(1))
    first = local.replace(tzinfo=zone, fold=0)
    second = local.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError("ambiguous or nonexistent calendar local time")
    return first.astimezone(UTC)


def parse_calendar(payload: bytes, received_at: datetime) -> ParsedBatch:
    ensure_utc(received_at)
    text = payload.decode("utf-8-sig")
    if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
        raise ValueError("response is not a complete iCalendar")
    text = re.sub(r"\r?\n[ \t]", "", text)
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, block in enumerate(text.split("BEGIN:VEVENT")[1:]):
        try:
            if "END:VEVENT" not in block:
                raise ValueError("incomplete calendar event")
            fields: dict[str, tuple[str, str]] = {}
            for line in block.split("END:VEVENT", 1)[0].splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    fields[key.split(";", 1)[0]] = (key, value.strip())
            if {"RRULE", "RDATE", "RECURRENCE-ID"} & fields.keys():
                raise ValueError("recurring calendars require expansion before ingestion")
            scheduled = _ical_time(*fields["DTSTART"])
            uid = fields["UID"][1]
            summary = fields["SUMMARY"][1].replace(r"\,", ",").replace(r"\n", " ")
            if not uid or not summary:
                raise ValueError("calendar UID and summary required")
            records.append(
                {
                    "kind": "calendar",
                    "native_id": uid,
                    "headline": summary,
                    "scheduled_at": scheduled.isoformat(),
                    "published_at": None,
                    "status": fields.get("STATUS", ("", "CONFIRMED"))[1],
                    "sequence": int(fields.get("SEQUENCE", ("", "0"))[1]),
                    "actual": None,
                    "consensus": None,
                    "prior": None,
                    "surprise": None,
                }
            )
        except (ValueError, KeyError) as exc:
            rejected.append({"item_index": index, "reason": str(exc)})
    return ParsedBatch(records, rejected)
