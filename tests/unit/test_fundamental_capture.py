from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.run_fundamental_collector import Resource, capture_pass, fetch_resource, validate_config
from bot.config import load_config
from bot.news.fundamental_sources import (
    ParsedBatch,
    parse_calendar,
    parse_fed_archive,
    parse_rss,
    parse_treasury,
)
from bot.news.fundamental_store import FundamentalStore
from research.fundamental_capture_audit import audit, rebuild

NOW = datetime(2026, 9, 8, 15, tzinfo=UTC)
RSS = b"""<rss><channel><item><title>Fed policy update</title>
<link>https://www.federalreserve.gov/releases/a.htm</link>
<description>&lt;p&gt;Policy &amp;amp; inflation&lt;/p&gt;</description>
<pubDate>Tue, 08 Sep 2026 10:30:00 EDT</pubDate></item></channel></rss>"""
SOURCE: dict[str, Any] = {"id": "fed", "topic": "monetary_policy"}


def ingest(
    store: FundamentalStore,
    payload: bytes = RSS,
    *,
    now: datetime = NOW,
    source: dict[str, Any] = SOURCE,
    batch: ParsedBatch | None = None,
    success: bool = True,
) -> dict[str, int]:
    return store.record_fetch(
        source,
        {
            "source_id": source["id"],
            "url": "https://www.federalreserve.gov/feeds/press_monetary.xml",
            "received_at": now.isoformat(),
            "success": success,
            "http_status": 200,
            "raw_sha256": store.archive(payload),
            "etag": "version-one",
        },
        batch or parse_rss(payload, now),
        resource=source["id"],
        mode="live_poll",
        poll_seconds=300,
    )


def test_rss_utc_summary_and_no_invented_surprise() -> None:
    row = parse_rss(RSS, NOW).records[0]
    assert row["published_at"] == "2026-09-08T14:30:00+00:00"
    assert row["body"] == "Policy & inflation"
    assert row["consensus"] is None and row["surprise"] is None
    assert row["body_scope"] == "publisher_feed_summary"


def test_archive_naive_date_is_metadata_and_never_fresh_news(tmp_path: Path) -> None:
    raw = json.dumps(
        [
            {
                "d": "9/8/2026 10:30:00 AM",
                "t": "Policy update",
                "l": "/newsevents/pressreleases/a.htm",
                "pt": "Monetary Policy",
            }
        ]
    ).encode()
    batch = parse_fed_archive(raw, NOW)
    assert batch.records[0]["published_at"] is None
    assert batch.records[0]["publication_date"] == "2026-09-08"
    with FundamentalStore(tmp_path) as store:
        ingest(store, raw, batch=batch)
        assert store.context(NOW)["recent_news"] == []


def test_archive_accepts_date_only_and_linked_agencies_without_inventing_time() -> None:
    raw = json.dumps(
        [
            {
                "d": "1/3/2006",
                "t": "Agency statement",
                "l": "",
                "stub": "https://www.ffiec.gov/news/statement",
                "pt": "Other Announcements",
            },
            {"updateDate": "Last update: September 04, 2026"},
        ]
    ).encode()
    batch = parse_fed_archive(raw, NOW)
    assert not batch.rejected and len(batch.records) == 1
    assert batch.records[0]["publication_time_quality"] == "date_only"
    assert batch.records[0]["published_at"] is None
    assert batch.records[0]["canonical_url"] == "https://www.ffiec.gov/news/statement"


@pytest.mark.parametrize("date_text", [b"", b"2026-09-08T14:00:00", b"2026-09-09T14:00:00Z"])
def test_bad_or_future_publication_is_quarantined(date_text: bytes) -> None:
    raw = RSS.replace(b"Tue, 08 Sep 2026 10:30:00 EDT", date_text)
    batch = parse_rss(raw, NOW)
    assert not batch.records and len(batch.rejected) == 1


def test_atom_does_not_promote_updated_time_to_original_publication() -> None:
    raw = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <title>News</title><link href="https://www.bea.gov/news/1" />
    <updated>2026-09-08T14:00:00Z</updated></entry></feed>"""
    assert parse_rss(raw, NOW).rejected
    raw = raw.replace(b"updated", b"published")
    assert parse_rss(raw, NOW).records[0]["canonical_url"] == "https://www.bea.gov/news/1"


@pytest.mark.parametrize(
    "payload",
    [
        b"<html>Access denied</html>",
        b'<!DOCTYPE rss [<!ENTITY x "expanded">]><rss><channel/></rss>',
    ],
)
def test_non_feed_and_entity_payloads_fail(payload: bytes) -> None:
    with pytest.raises(ValueError):
        parse_rss(payload, NOW)


def test_treasury_preserves_negative_missing_and_vintage_boundary() -> None:
    raw = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:d="urn:d" xmlns:m="urn:m">
    <entry><content><m:properties><d:NEW_DATE>2021-01-04T00:00:00</d:NEW_DATE>
    <d:TC_10YEAR>-1.08</d:TC_10YEAR><d:TC_30YEAR m:null="true"/>
    </m:properties></content></entry></feed>"""
    row = parse_treasury(raw, NOW, 2021).records[0]
    assert row["values"] == {"TC_10YEAR": "-1.08", "TC_30YEAR": None}
    assert row["published_at"] is None and "not_historical" in row["vintage_status"]
    assert parse_treasury(raw, NOW, 2020).rejected
    assert parse_treasury(raw.replace(b"-1.08", b"NaN"), NOW, 2021).rejected


def calendar(
    start: str = "DTSTART;TZID=America/New_York:20260909T083000", extra: str = ""
) -> bytes:
    return (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:cpi-release\r\n"
        "SUMMARY:Consumer Price\r\n Index\r\n"
        + start
        + "\r\n"
        + extra
        + "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()


def test_calendar_dst_unfolding_and_future_schedule() -> None:
    row = parse_calendar(calendar(), NOW).records[0]
    assert row["scheduled_at"] == "2026-09-09T12:30:00+00:00"
    assert row["headline"] == "Consumer PriceIndex"
    winter = calendar("DTSTART;TZID=America/New_York:20261209T083000")
    assert parse_calendar(winter, NOW).records[0]["scheduled_at"].endswith("13:30:00+00:00")


@pytest.mark.parametrize(
    "start,extra",
    [
        ("DTSTART:20260909T083000", ""),
        ("DTSTART;TZID=America/New_York:20261101T013000", ""),
        ("DTSTART;TZID=America/New_York:20260308T023000", ""),
        ("DTSTART:20260909T123000Z", "RRULE:FREQ=MONTHLY\r\n"),
    ],
)
def test_unresolved_calendar_times_and_recurrence_are_quarantined(start: str, extra: str) -> None:
    assert parse_calendar(calendar(start, extra), NOW).rejected


def test_archive_restart_dedupe_and_revisions_keep_first_receipt(tmp_path: Path) -> None:
    with FundamentalStore(tmp_path) as store:
        assert ingest(store)["inserted"] == 1
        first = next(store.records())
    with FundamentalStore(tmp_path) as store:
        assert ingest(store, now=NOW + timedelta(minutes=1))["duplicates"] == 1
        revised = RSS.replace(b"Policy &amp;amp; inflation", b"Revised statement")
        assert ingest(store, revised, now=NOW + timedelta(minutes=2))["inserted"] == 1
        rows = list(store.records())
        assert rows[0]["received_at"] == first["received_at"]
        assert len({row["story_group"] for row in rows}) == 1
        assert len(list(store.records(NOW + timedelta(minutes=1)))) == 1
        assert store.connection.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == 3


def test_syndication_group_and_old_story_cannot_refresh_bias(tmp_path: Path) -> None:
    with FundamentalStore(tmp_path) as store:
        ingest(store)
        copy = RSS.replace(b"/releases/a.htm", b"/releases/b.htm")
        ingest(store, copy, source={"id": "syndicated", "topic": "monetary_policy"})
        assert len({row["story_group"] for row in store.records()}) == 1
        revised = RSS.replace(b"10:30:00", b"12:30:00")
        ingest(store, revised, now=NOW + timedelta(hours=2))
        assert store.context(NOW + timedelta(hours=2))["recent_news"] == []


def test_repeated_monthly_release_url_is_a_new_event(tmp_path: Path) -> None:
    with FundamentalStore(tmp_path) as store:
        ingest(store)
        next_month = RSS.replace(b"Tue, 08 Sep 2026", b"Thu, 08 Oct 2026")
        later = NOW + timedelta(days=30)
        ingest(store, next_month, now=later)
        assert len({row["story_group"] for row in store.records()}) == 2
        assert len(store.context(later)["recent_news"]) == 1


def test_repeated_archive_headlines_do_not_merge_different_releases(tmp_path: Path) -> None:
    raw = json.dumps(
        [
            {
                "d": "1/3/2006",
                "t": "FOMC statement",
                "l": "/releases/first.htm",
                "pt": "Monetary Policy",
            },
            {
                "d": "3/3/2006",
                "t": "FOMC statement",
                "l": "/releases/second.htm",
                "pt": "Monetary Policy",
            },
        ]
    ).encode()
    with FundamentalStore(tmp_path) as store:
        ingest(store, raw, batch=parse_fed_archive(raw, NOW))
        assert len({row["story_group"] for row in store.records()}) == 2


def test_asof_context_excludes_backfilled_history_and_later_cancellation(tmp_path: Path) -> None:
    with FundamentalStore(tmp_path) as store:
        batch = parse_calendar(calendar(), NOW)
        ingest(store, calendar(), batch=batch)
        assert store.context(NOW - timedelta(seconds=1))["upcoming_24h"] == []
        assert len(store.context(NOW)["upcoming_24h"]) == 1
        cancelled = calendar(extra="STATUS:CANCELLED\r\nSEQUENCE:1\r\n")
        ingest(
            store, cancelled, now=NOW + timedelta(minutes=10), batch=parse_calendar(cancelled, NOW)
        )
        assert len(store.context(NOW)["upcoming_24h"]) == 1
        assert store.context(NOW + timedelta(minutes=10))["upcoming_24h"] == []
        assert store.context(NOW)["abstain"] is True


def test_failed_or_partial_parse_cannot_poison_http_validators(tmp_path: Path) -> None:
    with FundamentalStore(tmp_path) as store:
        ingest(store, success=False)
        state = store.checkpoint("fed")
        assert state and state["etag"] is None and state["failures"] == 1
        assert list(store.records()) == []
        assert datetime.fromisoformat(state["next_due"]) >= NOW + timedelta(hours=1)
        partial = ParsedBatch(parse_rss(RSS, NOW).records, [{"reason": "missing date"}])
        ingest(store, batch=partial)
        state = store.checkpoint("fed")
        assert state and state["etag"] is None


def test_export_is_unlabeled_and_refuses_overwrite_or_cross_symbol(tmp_path: Path) -> None:
    with FundamentalStore(tmp_path) as store:
        ingest(store)
        destination = tmp_path / "export.jsonl"
        assert store.export(destination) == 1
        row = json.loads(destination.read_text())
        assert row["symbols"] == ["XAU_USDT"] and row["label_status"] == "unlabeled"
        with pytest.raises(FileExistsError):
            store.export(destination)
        assert store.report()["training_ready"] is False
    with pytest.raises(ValueError, match="another research symbol"):
        FundamentalStore(tmp_path, "BTC_USDT")


@pytest.mark.asyncio
async def test_capture_persists_before_restart_and_skips_not_due(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config("configs/pillar_one_xau_capture.yaml")
    config["sources"] = [source for source in config["sources"] if source["id"] == "fed_monetary"]
    calls = []

    def fetch(*args: Any) -> tuple[dict[str, Any], bytes]:
        calls.append(args)
        return (
            {
                "source_id": "fed_monetary",
                "url": args[0].url,
                "received_at": datetime.now(UTC).isoformat(),
                "success": True,
                "http_status": 200,
            },
            RSS,
        )

    monkeypatch.setattr("apps.run_fundamental_collector.fetch_resource", fetch)
    with FundamentalStore(tmp_path) as store:
        result = await capture_pass(config, store, backfill=False)
        assert result["inserted"] == 1
    with FundamentalStore(tmp_path) as store:
        assert (await capture_pass(config, store, backfill=False))["skipped"] == 1
    assert len(calls) == 1


def test_config_rejects_credentials_and_execution() -> None:
    config = load_config("configs/pillar_one_xau_capture.yaml")
    validate_config(config)
    config["sources"][0]["url"] = "https://user:password@www.federalreserve.gov/feeds/a.xml"
    with pytest.raises(ValueError, match="credentials"):
        validate_config(config)


def test_offline_rebuild_preserves_availability_and_verifies_raw_hashes(tmp_path: Path) -> None:
    original, rebuilt = tmp_path / "original", tmp_path / "rebuilt"
    with FundamentalStore(original) as store:
        ingest(store)
        initial = list(store.records())
    config = {
        "research_symbol": "XAU_USDT",
        "sources": [dict(SOURCE, kind="rss", poll_seconds=300)],
    }
    result = rebuild(config, original, rebuilt)
    assert result["audit_passed"] and result["records"] == 1
    with FundamentalStore(rebuilt) as store:
        assert list(store.records()) == initial
        assert list(store.records(NOW - timedelta(seconds=1))) == []
        raw = next((rebuilt / "raw").rglob("*.raw"))
        raw.write_bytes(b"corruption")
        with pytest.raises(ValueError, match="hash mismatch"):
            audit(store)
    with pytest.raises(FileExistsError):
        rebuild(config, original, rebuilt)


@pytest.mark.parametrize(
    "checkpoint,accepted",
    [
        (None, False),
        ({"etag": None, "last_modified": None, "failures": 1}, False),
        ({"etag": '"accepted-version"', "last_modified": None}, True),
    ],
)
def test_304_requires_a_previous_accepted_http_validator(
    monkeypatch: pytest.MonkeyPatch,
    checkpoint: dict[str, Any] | None,
    accepted: bool,
) -> None:
    class Opener:
        def open(self, request: Any, timeout: int) -> Any:
            raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", {}, None)

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    resource = Resource(SOURCE, "https://www.federalreserve.gov/feeds/press_monetary.xml", None)
    evidence, body = fetch_resource(resource, 5, 1000, checkpoint)
    assert evidence["success"] is accepted and body == b""
