from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.run_fundamental_collector import resources, validate_config
from bot.config import load_config
from bot.news.fundamental_enrichment import calendar_value, parse_fed_article, parse_forex_factory
from bot.news.fundamental_sources import parse_timestamp
from bot.news.fundamental_store import FundamentalStore
from research.fundamental_capture_audit import audit, rebuild

NOW = datetime(2026, 9, 9, 19, tzinfo=UTC)
RELEASE = datetime(2026, 9, 10, 12, 30, tzinfo=UTC)
ARTICLE_URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260909a.htm"
ARTICLE = b"""<html><nav>unrelated menu</nav><div id="article">
<div class="heading col"><p class="article__time">September 09, 2026</p>
<h3 class="title">Policy decision</h3><p class="releaseTime">For release at 2:00 p.m. EDT
<ul><li>Share</li></ul></div><div><p>The committee reviewed employment and inflation
conditions and explained its interest rate decision. This statement preserves the
details of the policy discussion.</p><script>bad script</script></div></div>
<footer>unrelated footer</footer></html>"""


def payload(forecast: str = "0.3%", **changes: Any) -> bytes:
    row = {
        "title": "CPI m/m",
        "country": "USD",
        "date": "2026-09-10T08:30:00-04:00",
        "impact": "High",
        "forecast": forecast,
        "previous": "0.2%",
    }
    row.update(changes)
    return json.dumps([row]).encode()


def capture(
    store: FundamentalStore, source: dict[str, Any], raw: bytes, at: datetime = NOW
) -> None:
    store.record_fetch(
        source,
        {
            "source_id": source["id"],
            "url": source["url"],
            "http_status": 200,
            "received_at": at.isoformat(),
            "success": True,
            "raw_sha256": store.archive(raw),
        },
        parse_forex_factory(raw, at),
        resource=source["id"] + ":" + source["url"],
        mode="live_poll",
        poll_seconds=source["poll_seconds"],
    )


def source() -> dict[str, Any]:
    config = load_config(Path("configs/pillar_one_xau_capture.yaml"))
    return next(row for row in config["sources"] if row["kind"] == "forex_factory")


@pytest.mark.parametrize(
    ("raw", "number", "unit"),
    [
        ("0.3%", "0.3", "percent"),
        ("-45.2B", "-45200000000.0", "number_dimension_unspecified"),
        ("220K", "220000", "number_dimension_unspecified"),
        ("", None, None),
        ("NaN", None, None),
        ("3-4%", None, None),
    ],
)
def test_calendar_units_preserved(raw: str, number: str | None, unit: str | None) -> None:
    value = calendar_value(raw)
    assert value["value"] == number and value["unit"] == unit
    assert value["raw"] == raw


def test_forecast_does_not_invent_actual_or_surprise() -> None:
    row = parse_forex_factory(payload(actual="0.4%"), NOW).records[0]
    assert row["scheduled_at"] == RELEASE.isoformat()
    assert row["actual"] is None and row["surprise"] is None
    assert row["consensus"] == "0.3" and row["published_at"] is None
    assert parse_forex_factory(payload(country="All"), NOW).records[0]["currency"] is None
    assert parse_forex_factory(payload(date="2026-09-10T08:30:00"), NOW).rejected


def test_versions_asof_and_late_forecasts(tmp_path: Path) -> None:
    with FundamentalStore(tmp_path) as store:
        capture(store, source(), payload())
        capture(store, source(), payload(), NOW + timedelta(minutes=1))
        capture(store, source(), payload("0.4%"), NOW + timedelta(minutes=2))
        capture(store, source(), payload("0.8%"), RELEASE)
        rows = list(store.records())
        assert len(rows) == 3 and rows[0]["available_at"] == NOW.isoformat()
        assert rows[0]["source_class"] == "provider_published_export"
        prior = store.consensus_before_release(rows[0]["native_id"], RELEASE)
        assert prior is not None and prior["consensus"] == "0.4"
        assert store.context(NOW)["upcoming_24h"][0]["consensus"] == "0.3"
        assert audit(store)["forecast_coverage"]["observed_before_scheduled_release"] == 2


def test_late_only_and_removed_forecasts_never_backfilled(tmp_path: Path) -> None:
    with FundamentalStore(tmp_path / "late") as store:
        capture(store, source(), payload(), RELEASE)
        row = next(store.records())
        assert store.consensus_before_release(row["native_id"], RELEASE) is None
    with FundamentalStore(tmp_path / "removed") as store:
        capture(store, source(), payload())
        capture(store, source(), payload(""), NOW + timedelta(seconds=1))
        row = next(store.records())
        latest = store.consensus_before_release(row["native_id"], RELEASE)
        assert latest is not None and latest["consensus"] is None


def test_forecast_reversion_retains_new_receipt(tmp_path: Path) -> None:
    with FundamentalStore(tmp_path) as store:
        capture(store, source(), payload("0.3%"))
        capture(store, source(), payload("0.4%"), NOW + timedelta(seconds=1))
        capture(store, source(), payload("0.3%"), NOW + timedelta(seconds=2))
        capture(store, source(), payload("0.3%"), NOW + timedelta(seconds=3))
        rows = list(store.records())
        assert len(rows) == 3 and rows[0]["record_id"] != rows[2]["record_id"]
        last = store.consensus_before_release(rows[0]["native_id"], RELEASE)
        assert last is not None and last["consensus"] == "0.3"
        assert last["available_at"] == (NOW + timedelta(seconds=2)).isoformat()


def test_same_day_reschedule_versions_require_exact_release_time(tmp_path: Path) -> None:
    with FundamentalStore(tmp_path) as store:
        capture(store, source(), payload())
        capture(
            store,
            source(),
            payload("0.4%", date="2026-09-10T09:00:00-04:00"),
            NOW + timedelta(seconds=1),
        )
        rows = list(store.records())
        assert rows[0]["story_group"] == rows[1]["story_group"]
        assert store.consensus_before_release(rows[0]["native_id"], RELEASE) is None
        assert (
            store.context(NOW + timedelta(seconds=2))["upcoming_24h"][0]["scheduled_at"]
            == "2026-09-10T13:00:00+00:00"
        )


def test_article_body_time_and_no_backdating(tmp_path: Path) -> None:
    batch = parse_fed_article(ARTICLE, NOW, ARTICLE_URL)
    row = batch.records[0]
    assert row["published_at"] == "2026-09-09T18:00:00+00:00"
    assert all(word not in row["body"] for word in ("unrelated", "Share", "bad script"))
    with FundamentalStore(tmp_path) as store:
        store.record_fetch(
            {"id": "fed_articles", "topic": "monetary_policy"},
            {
                "received_at": NOW.isoformat(),
                "success": True,
                "http_status": 200,
                "url": ARTICLE_URL,
                "raw_sha256": store.archive(ARTICLE),
            },
            batch,
            resource=ARTICLE_URL,
            mode="live_poll",
            poll_seconds=86400,
        )
        assert store.context(NOW - timedelta(seconds=1))["recent_news"] == []
        assert store.context(NOW)["recent_news"][0]["body_scope"] == "publisher_article_text"
        assert store.context(NOW + timedelta(seconds=1))["recent_news"] == []


def test_article_without_explicit_zone_retains_date_only() -> None:
    row = parse_fed_article(ARTICLE.replace(b" EDT", b""), NOW, ARTICLE_URL).records[0]
    assert row["published_at"] is None and row["publication_date"] == "2026-09-09"
    with pytest.raises(ValueError):
        parse_fed_article(ARTICLE, NOW, "https://other.example/newsevents/pressreleases/a.htm")
    with pytest.raises(ValueError):
        parse_fed_article(ARTICLE, NOW - timedelta(hours=2), ARTICLE_URL)
    with pytest.raises(ValueError):
        parse_fed_article(b"<html>Access denied</html>", NOW, ARTICLE_URL)


def test_explicit_european_offsets_without_guessing_other_zones() -> None:
    assert parse_timestamp("Thu, 27 Aug 2026 18:37:11 CEST").hour == 16
    assert parse_timestamp("Thu, 27 Aug 2026 18:37:11 CET").hour == 17
    with pytest.raises(ValueError):
        parse_timestamp("Thu, 27 Aug 2026 18:37:11 XYZ")


def test_article_resources_bounded_to_official_discovered_links() -> None:
    config = load_config(Path("configs/pillar_one_xau_capture.yaml"))
    article_source = next(row for row in config["sources"] if row["kind"] == "fed_article")
    article_source["max_articles"] = 1
    config["sources"] = [article_source]
    validate_config(config)
    rows = [
        {
            "source_id": "fed_archive",
            "canonical_url": ARTICLE_URL,
            "publication_date": "2026-09-09",
        },
        {
            "source_id": "fed_archive",
            "canonical_url": "https://evil.example/",
            "publication_date": "2026-09-10",
        },
    ]
    assert [r.url for r in resources(config, NOW, False, rows)] == [ARTICLE_URL]
    assert resources(config, NOW, False) == []


def test_calendar_config_rejects_wrong_attribution_and_excess_polling() -> None:
    config = load_config(Path("configs/pillar_one_xau_capture.yaml"))
    config["sources"] = [source()]
    validate_config(config)
    config["sources"][0]["source_class"] = "official_primary"
    with pytest.raises(ValueError):
        validate_config(config)
    config["sources"] = [source()]
    config["sources"][0]["poll_seconds"] = 60
    with pytest.raises(ValueError):
        validate_config(config)


def test_enrichment_rebuild_preserves_receipt_and_provider(tmp_path: Path) -> None:
    config = load_config(Path("configs/pillar_one_xau_capture.yaml"))
    with FundamentalStore(tmp_path / "original") as store:
        capture(store, source(), payload())
    result = rebuild(config, tmp_path / "original", tmp_path / "rebuilt")
    assert result["forecast_coverage"]["observed_before_scheduled_release"] == 1
    with FundamentalStore(tmp_path / "rebuilt") as store:
        row = next(store.records())
        assert row["available_at"] == NOW.isoformat()
        assert row["source_class"] == "provider_published_export"
