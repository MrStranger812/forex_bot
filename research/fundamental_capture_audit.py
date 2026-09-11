"""Verify a capture and optionally rebuild normalized records from preserved responses.

This command never uses the network. Original request and receipt times survive
reprocessing; parser changes cannot invent a historical release timestamp.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bot.config import load_config
from bot.news.fundamental_enrichment import parse_fed_article, parse_forex_factory
from bot.news.fundamental_sources import (
    ParsedBatch,
    parse_calendar,
    parse_fed_archive,
    parse_rss,
    parse_timestamp,
    parse_treasury,
)
from bot.news.fundamental_store import FundamentalStore


def audit(store: FundamentalStore) -> dict[str, Any]:
    checked: set[str] = set()
    records = list(store.records())
    for row in store.connection.execute("SELECT payload FROM fetches"):
        evidence = json.loads(row[0])
        sha = evidence.get("raw_sha256")
        if sha and sha not in checked:
            path = store.directory / "raw" / sha[:2] / (sha + ".raw")
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise ValueError("captured response hash mismatch")
            checked.add(sha)
    for row in records:
        if row["raw_sha256"] not in checked:
            raise ValueError("record lacks an audited response")
        available = parse_timestamp(row["available_at"])
        if available != parse_timestamp(row["received_at"]):
            raise ValueError("capture availability differs from first receipt")
        if row.get("published_at") and parse_timestamp(row["published_at"]) > available:
            raise ValueError("future news publication entered the corpus")
        if row["symbols"] != [store.symbol] or row["label_status"] != "unlabeled":
            raise ValueError("unexpected symbol or labels in capture")
    if store.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    if store.connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("broken raw-record sightings")
    summary = store.report()
    summary.update(
        audited_at=datetime.now(UTC).isoformat(),
        audited_raw_responses=len(checked),
        counts_by_kind=dict(Counter(row["kind"] for row in records)),
        archive_categories=dict(
            Counter(row["event_category"] for row in records if row["kind"] == "news_archive")
        ),
        yield_observation_count=sum(
            sum(value is not None for value in row["values"].values())
            for row in records
            if row["kind"] == "yield_curve"
        ),
        curve_coverage={
            source: {
                "first": min(
                    row["observation_date"] for row in records if row["source_id"] == source
                ),
                "last": max(
                    row["observation_date"] for row in records if row["source_id"] == source
                ),
            }
            for source in {row["source_id"] for row in records if row["kind"] == "yield_curve"}
        },
        audit_passed=True,
        forecast_coverage={
            "versions": sum(row["kind"] == "macro_calendar" for row in records),
            "with_numeric_forecast": sum(
                row["kind"] == "macro_calendar" and row.get("consensus") is not None
                for row in records
            ),
            "observed_before_scheduled_release": sum(
                row["kind"] == "macro_calendar"
                and row.get("consensus") is not None
                and parse_timestamp(row["available_at"]) < parse_timestamp(row["scheduled_at"])
                for row in records
            ),
            "actuals": sum(
                row["kind"] == "macro_calendar" and row.get("actual") is not None for row in records
            ),
        },
    )
    return summary


def rebuild(config: dict[str, Any], original: Path, destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise FileExistsError("rebuild needs a new directory; original captures are retained")
    if not (original / "capture.sqlite3").is_file():
        raise FileNotFoundError("original capture database is absent")
    sources = {source["id"]: source for source in config["sources"]}
    with FundamentalStore(original, config["research_symbol"]) as old:
        audit(old)
        rows = [
            json.loads(row[0])
            for row in old.connection.execute("SELECT payload FROM fetches ORDER BY received_at,id")
        ]
    with FundamentalStore(destination, config["research_symbol"]) as new:
        for evidence in rows:
            source = sources[evidence["source_id"]]
            evidence = dict(
                evidence,
                reprocessed_at=datetime.now(UTC).isoformat(),
                reprocessed_from=str(original),
            )
            sha = evidence.get("raw_sha256")
            body = b""
            if sha:
                body = (original / "raw" / sha[:2] / (sha + ".raw")).read_bytes()
                if new.archive(body) != sha:
                    raise ValueError("rebuild changed response bytes")
            batch = ParsedBatch([], [])
            params = parse_qs(urlsplit(evidence["url"]).query)
            year = int(params["field_tdr_date_value"][0]) if source["kind"] == "treasury" else None
            if evidence["success"] and evidence.get("http_status") == 200:
                received = parse_timestamp(evidence["received_at"])
                kind = source["kind"]
                if kind == "rss":
                    batch = parse_rss(body, received)
                elif kind == "fed_archive":
                    batch = parse_fed_archive(body, received)
                elif kind == "ical":
                    batch = parse_calendar(body, received)
                elif kind == "forex_factory":
                    batch = parse_forex_factory(body, received)
                elif kind == "fed_article":
                    batch = parse_fed_article(body, received, evidence["url"])
                elif year is not None:
                    batch = parse_treasury(body, received, year)
            historical = year is not None and year < parse_timestamp(evidence["received_at"]).year
            new.record_fetch(
                source,
                evidence,
                batch,
                resource=source["id"] + ":" + evidence["url"],
                mode="historical_backfill" if historical else "live_poll",
                poll_seconds=source["poll_seconds"],
            )
        result = audit(new)
        result["reprocessed_from"] = str(original)
        result["original_receipt_times_preserved"] = True
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/pillar_one_xau_capture.yaml"))
    parser.add_argument("--rebuild-to", type=Path)
    parser.add_argument("--export", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    directory = Path(config["storage_dir"])
    if args.rebuild_to:
        result = rebuild(config, directory, args.rebuild_to)
        directory = args.rebuild_to
    else:
        if not (directory / "capture.sqlite3").is_file():
            raise FileNotFoundError("capture database is absent")
        with FundamentalStore(directory, config["research_symbol"]) as store:
            result = audit(store)
    if args.export:
        with FundamentalStore(directory, config["research_symbol"]) as store:
            result["exported_records"] = store.export(args.export)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("records", "counts_by_kind", "yield_observation_count", "audit_passed")
            }
        )
    )


if __name__ == "__main__":
    main()
