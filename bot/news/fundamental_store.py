"""Append-only observations and durable version deduplication for Pillar One."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from bot.domain.events import ensure_utc
from bot.news.fundamental_sources import ParsedBatch, digest


class FundamentalStore(AbstractContextManager["FundamentalStore"]):
    def __init__(self, directory: Path, symbol: str = "XAU_USDT") -> None:
        self.directory = directory
        self.symbol = symbol
        directory.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(directory / "capture.sqlite3", timeout=30)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fetches (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL, received_at TEXT NOT NULL,
                success INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL,
                native_id TEXT NOT NULL, story_group TEXT NOT NULL, content_hash TEXT,
                canonical_url TEXT, available_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS records_asof ON records(available_at);
            CREATE INDEX IF NOT EXISTS records_content ON records(content_hash);
            CREATE INDEX IF NOT EXISTS records_url ON records(canonical_url);
            CREATE INDEX IF NOT EXISTS records_native ON records(source,native_id);
            CREATE TABLE IF NOT EXISTS sightings (
                fetch_id INTEGER NOT NULL REFERENCES fetches(id),
                record_id TEXT NOT NULL REFERENCES records(id),
                PRIMARY KEY(fetch_id,record_id));
            CREATE TABLE IF NOT EXISTS checkpoints (
                resource TEXT PRIMARY KEY, next_due TEXT NOT NULL, failures INTEGER NOT NULL,
                etag TEXT, last_modified TEXT, backfill_done INTEGER NOT NULL);
        """)
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO metadata VALUES ('research_symbol',?)", (symbol,)
            )
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key='research_symbol'"
        ).fetchone()
        if row[0] != symbol:
            self.connection.close()
            raise ValueError("capture directory belongs to another research symbol")

    def archive(self, payload: bytes) -> str:
        sha = hashlib.sha256(payload).hexdigest()
        path = self.directory / "raw" / sha[:2] / (sha + ".raw")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(payload)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise ValueError("raw archive integrity mismatch") from None
        return sha

    def checkpoint(self, resource: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT next_due,failures,etag,last_modified,backfill_done FROM checkpoints "
            "WHERE resource=?",
            (resource,),
        ).fetchone()
        if not row:
            return None
        return dict(
            zip(
                ("next_due", "failures", "etag", "last_modified", "backfill_done"), row, strict=True
            )
        )

    def record_fetch(
        self,
        source: dict[str, Any],
        evidence: dict[str, Any],
        batch: ParsedBatch,
        *,
        resource: str,
        mode: str,
        poll_seconds: int,
    ) -> dict[str, int]:
        received = ensure_utc(datetime.fromisoformat(evidence["received_at"]))
        successful = evidence["success"] is True
        previous = self.checkpoint(resource)
        failures = 0 if successful else min(12, (previous or {}).get("failures", 0) + 1)
        delay = poll_seconds if successful else max(3600, poll_seconds * min(64, 2**failures))
        next_due = received + timedelta(seconds=delay)
        evidence = dict(evidence, rejected=batch.rejected, parsed_records=len(batch.records))
        inserted = duplicates = 0
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO fetches(source,received_at,success,payload) VALUES (?,?,?,?)",
                (source["id"], received.isoformat(), int(successful), json.dumps(evidence)),
            )
            fetch_id = cursor.lastrowid
            if successful:
                for record in batch.records:
                    identity = digest({"source": source["id"], "record": record})
                    if record["kind"] == "macro_calendar":
                        latest = self.connection.execute(
                            "SELECT id,payload FROM records WHERE source=? AND kind=? "
                            "AND native_id=? ORDER BY available_at DESC,rowid DESC LIMIT 1",
                            (source["id"], record["kind"], record["native_id"]),
                        ).fetchone()
                        latest_payload = json.loads(latest[1]) if latest else {}
                        if latest and all(latest_payload.get(k) == v for k, v in record.items()):
                            identity = latest[0]
                        else:
                            # A -> B -> A is a new observation of A. Global content
                            # deduplication would silently leave B as the latest forecast.
                            identity = digest(
                                {
                                    "source": source["id"],
                                    "record": record,
                                    "transition_received_at": received.isoformat(),
                                }
                            )
                    existing = self.connection.execute(
                        "SELECT id FROM records WHERE id=?", (identity,)
                    ).fetchone()
                    if existing:
                        duplicates += 1
                    else:
                        group = self._story_group(source["id"], record)
                        stored = dict(
                            record,
                            record_id=identity,
                            source_id=source["id"],
                            topic=source["topic"],
                            symbols=[self.symbol],
                            source_url=evidence["url"],
                            story_group=group,
                            received_at=received.isoformat(),
                            available_at=received.isoformat(),
                            capture_mode=mode,
                            raw_sha256=evidence["raw_sha256"],
                            source_class=source.get("source_class", "official_primary"),
                            label_status="unlabeled",
                        )
                        self.connection.execute(
                            "INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                identity,
                                source["id"],
                                record["kind"],
                                record["native_id"],
                                group,
                                record.get("content_hash"),
                                record.get("canonical_url"),
                                received.isoformat(),
                                json.dumps(stored, sort_keys=True),
                            ),
                        )
                        inserted += 1
                    self.connection.execute(
                        "INSERT OR IGNORE INTO sightings VALUES (?,?)", (fetch_id, identity)
                    )
            # Do not cache validators from parse failures or poison a later retry with a 304.
            etag = evidence.get("etag") if successful and not batch.rejected else None
            modified = evidence.get("last_modified") if successful and not batch.rejected else None
            if successful and evidence.get("http_status") == 304:
                etag = etag or (previous or {}).get("etag")
                modified = modified or (previous or {}).get("last_modified")
            complete = successful and not batch.rejected and mode == "historical_backfill"
            self.connection.execute(
                "INSERT INTO checkpoints VALUES (?,?,?,?,?,?) ON CONFLICT(resource) DO UPDATE "
                "SET next_due=excluded.next_due, failures=excluded.failures, etag=excluded.etag, "
                "last_modified=excluded.last_modified, backfill_done=excluded.backfill_done",
                (resource, next_due.isoformat(), failures, etag, modified, int(complete)),
            )
        return {"inserted": inserted, "duplicates": duplicates, "rejected": len(batch.rejected)}

    def _story_group(self, source: str, record: dict[str, Any]) -> str:
        if record["kind"] not in {"news", "news_archive", "news_document"}:
            return digest([source, record["kind"], record["native_id"]])
        # Archive headlines alone recur across unrelated policy decisions. Only full
        # feed content participates in syndication matching on nearby publication dates.
        content_hash = record.get("content_hash") if record["kind"] == "news" else None
        published = record.get("published_at") or record["publication_date"]
        reference_date = datetime.fromisoformat(published).date()
        rows = self.connection.execute(
            "SELECT story_group,payload FROM records WHERE (source=? AND native_id=?) "
            "OR (canonical_url IS NOT NULL AND canonical_url=?) "
            "OR (content_hash IS NOT NULL AND content_hash=?) ORDER BY available_at,rowid",
            (source, record["native_id"], record.get("canonical_url"), content_hash),
        )
        for group, payload in rows:
            earlier = json.loads(payload)
            earlier_time = earlier.get("published_at") or earlier.get("publication_date")
            if (
                earlier_time
                and abs((reference_date - datetime.fromisoformat(earlier_time).date()).days) <= 1
            ):
                return str(group)
        return digest([source, record["native_id"], reference_date.isoformat()])

    def records(self, at: datetime | None = None) -> Iterator[dict[str, Any]]:
        query = "SELECT payload FROM records"
        params: tuple[str, ...] = ()
        if at is not None:
            query += " WHERE available_at<=?"
            params = (ensure_utc(at).isoformat(),)
        query += " ORDER BY available_at,rowid"
        for row in self.connection.execute(query, params):
            yield json.loads(row[0])

    def context(self, at: datetime, news_max_age_seconds: int = 3600) -> dict[str, Any]:
        at = ensure_utc(at)
        groups: dict[str, dict[str, Any]] = {}
        first_published: dict[str, datetime] = {}
        curves: dict[str, dict[str, Any]] = {}
        for row in self.records(at):
            group = row["story_group"]
            if row["kind"] == "yield_curve":
                previous = curves.get(row["source_id"])
                if row["observation_date"] <= at.date().isoformat() and (
                    previous is None or row["observation_date"] >= previous["observation_date"]
                ):
                    curves[row["source_id"]] = row
            elif row["kind"] in {"news", "news_document", "calendar", "macro_calendar"}:
                if row["kind"] == "news_document" and not row.get("published_at"):
                    continue
                if (
                    row["kind"] == "calendar"
                    and group in groups
                    and row["sequence"] < groups[group]["sequence"]
                ):
                    continue
                groups[group] = row
                if row.get("published_at"):
                    published = datetime.fromisoformat(row["published_at"])
                    first_published[group] = min(first_published.get(group, published), published)
        news = [
            row
            for group, row in groups.items()
            if row["kind"] in {"news", "news_document"}
            and 0 <= (at - first_published[group]).total_seconds() <= news_max_age_seconds
        ]
        calendar = [
            row
            for row in groups.values()
            if row["kind"] in {"calendar", "macro_calendar"}
            and row.get("status") != "CANCELLED"
            and at <= datetime.fromisoformat(row["scheduled_at"]) <= at + timedelta(days=1)
        ]
        # Daily releases can be stale; retain date/age explicitly for downstream abstention.
        for row in curves.values():
            row["observation_age_days"] = (
                at.date() - datetime.fromisoformat(row["observation_date"]).date()
            ).days
        return {
            "research_symbol": self.symbol,
            "as_of": at.isoformat(),
            "recent_news": news,
            "upcoming_24h": calendar,
            "yield_curves": list(curves.values()),
            "direction": None,
            "abstain": True,
            "reason": "Capture context is not a validated fundamental trading opinion.",
        }

    def consensus_before_release(
        self, native_id: str, release_at: datetime
    ) -> dict[str, Any] | None:
        """Last observed forecast version strictly before this exact scheduled release.

        Caller must reconcile event identity and actual release timing first. A
        later removal of a forecast overrides an older numeric forecast too.
        """
        release_at = ensure_utc(release_at)
        result = None
        for row in self.records(release_at - timedelta(microseconds=1)):
            if row["kind"] == "macro_calendar" and row["native_id"] == native_id:
                result = row
        if result is not None and datetime.fromisoformat(result["scheduled_at"]) == release_at:
            return result
        return None

    def report(self) -> dict[str, Any]:
        counts = self.connection.execute(
            "SELECT source,kind,COUNT(*),MIN(available_at),MAX(available_at) "
            "FROM records GROUP BY source,kind ORDER BY source"
        ).fetchall()
        recent = self.connection.execute(
            "SELECT payload FROM fetches WHERE id IN (SELECT MAX(id) FROM fetches GROUP BY source)"
        ).fetchall()
        return {
            "research_symbol": self.symbol,
            "records": sum(row[2] for row in counts),
            "by_source": [
                dict(
                    zip(
                        ("source", "kind", "versions", "first_received", "last_received"),
                        row,
                        strict=True,
                    )
                )
                for row in counts
            ],
            "fetch_count": self.connection.execute("SELECT COUNT(*) FROM fetches").fetchone()[0],
            "failed_fetch_count": self.connection.execute(
                "SELECT COUNT(*) FROM fetches WHERE success=0"
            ).fetchone()[0],
            "latest_source_fetches": [json.loads(row[0]) for row in recent],
            "training_ready": False,
            "live_ready": False,
            "missing": [
                "Verified Ourbit XAU contract and executable quotes",
                "Pre-release forecast history paired with verified first-release actuals",
                "Broad geopolitical news and dollar/positioning/reference-gold data",
                "Leakage-checked quote labels and chronological model validation",
            ],
            "limitations": [
                "RSS polling latency is not a low-latency newswire",
                "Latest historical Treasury snapshots are not historical vintages",
                "Every record is unlabeled; data volume is not independent event count",
            ],
        }

    def export(self, path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("x", encoding="utf-8") as handle:
            for row in self.records():
                handle.write(json.dumps(row, sort_keys=True) + "\n")
                count += 1
        return count

    def close(self) -> None:
        self.connection.close()

    def __exit__(self, *_: object) -> None:
        self.close()
