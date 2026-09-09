"""Resumable, read-only XAU fundamental capture; no LLM or exchange credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.config import load_config
from bot.news.fundamental_sources import (
    ParsedBatch,
    digest,
    parse_calendar,
    parse_fed_archive,
    parse_rss,
    parse_timestamp,
    parse_treasury,
)
from bot.news.fundamental_store import FundamentalStore

ALLOWED_HOSTS = {
    "www.federalreserve.gov",
    "www.bls.gov",
    "apps.bea.gov",
    "home.treasury.gov",
}
TREASURY_DATASETS = {"daily_treasury_yield_curve", "daily_treasury_real_yield_curve"}


def validate_config(config: dict[str, Any]) -> None:
    if config.get("mode") != "fundamental_capture" or config.get("research_symbol") != "XAU_USDT":
        raise ValueError("this collector requires the isolated XAU fundamental configuration")
    if config.get("execution", {}).get("enabled"):
        raise ValueError("fundamental collection cannot enable execution")
    for key, low, high in (
        ("concurrency", 1, 8),
        ("request_timeout_seconds", 1, 60),
        ("max_response_bytes", 1024, 16 * 1024 * 1024),
    ):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"invalid {key}")
    if not config.get("sources"):
        raise ValueError("at least one explicit source is required")
    ids: set[str] = set()
    for source in config["sources"]:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", source["id"]) or source["id"] in ids:
            raise ValueError("source IDs must be unique lowercase identifiers")
        ids.add(source["id"])
        parts = urllib.parse.urlsplit(source["url"])
        if (
            parts.scheme != "https"
            or parts.hostname not in ALLOWED_HOSTS
            or parts.username
            or parts.password
            or parts.port not in {None, 443}
            or parts.query
            or parts.fragment
        ):
            raise ValueError("source must be a reviewed public HTTPS URL without credentials/query")
        if source["kind"] not in {"rss", "treasury", "ical", "fed_archive"}:
            raise ValueError("unknown source parser")
        poll = source["poll_seconds"]
        if isinstance(poll, bool) or not isinstance(poll, int) or not 60 <= poll <= 86400:
            raise ValueError("poll_seconds must be an integer in [60,86400]")
        if source["kind"] == "treasury":
            if source.get("dataset") not in TREASURY_DATASETS:
                raise ValueError("unverified Treasury dataset")
            if not 2003 <= source.get("history_start_year", 0) <= datetime.now(UTC).year:
                raise ValueError("invalid historical start year")
        if not source.get("topic"):
            raise ValueError("a source topic is required")


@dataclass(frozen=True)
class Resource:
    source: dict[str, Any]
    url: str
    year: int | None

    @property
    def key(self) -> str:
        return str(self.source["id"]) + ":" + self.url


def resources(config: dict[str, Any], now: datetime, backfill: bool) -> list[Resource]:
    result: list[Resource] = []
    for source in config["sources"]:
        if source["kind"] == "treasury":
            start = source["history_start_year"] if backfill else now.year
            for year in range(start, now.year + 1):
                query = urllib.parse.urlencode(
                    {
                        "data": source["dataset"],
                        "field_tdr_date_value": year,
                    }
                )
                result.append(Resource(source, source["url"] + "?" + query, year))
        else:
            result.append(Resource(source, source["url"], None))
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def fetch_resource(
    resource: Resource,
    timeout: int,
    max_bytes: int,
    checkpoint: dict[str, Any] | None,
) -> tuple[dict[str, Any], bytes]:
    evidence: dict[str, Any] = {
        "source_id": resource.source["id"],
        "url": resource.url,
        "method": "GET",
        "requested_at": datetime.now(UTC).isoformat(),
        "source_config_sha256": digest(resource.source),
        "success": False,
    }
    headers = {"User-Agent": "PillarOneResearch/0.1 (public feed collector)"}
    if checkpoint:
        if checkpoint.get("etag"):
            headers["If-None-Match"] = checkpoint["etag"]
        if checkpoint.get("last_modified"):
            headers["If-Modified-Since"] = checkpoint["last_modified"]
    request = urllib.request.Request(resource.url, headers=headers)
    started = time.monotonic()
    body = b""
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            evidence.update(
                http_status=response.code,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                content_type=response.headers.get("Content-Type"),
            )
            body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            evidence["body_truncated"] = True
            body = body[:max_bytes]
            raise ValueError("response exceeds configured size bound")
        evidence["success"] = evidence["http_status"] in {200, 304}
        if not evidence["success"]:
            evidence["error"] = f"HTTP {evidence['http_status']}"
        if evidence["http_status"] == 304 and not checkpoint:
            evidence.update(success=False, error="304 without a prior accepted representation")
    except (OSError, ValueError) as exc:
        evidence.update(success=False, error_type=type(exc).__name__, error=str(exc))
    evidence.update(
        received_at=datetime.now(UTC).isoformat(),
        bytes=len(body),
        request_elapsed_ms=(time.monotonic() - started) * 1000,
    )
    return evidence, body


async def capture_pass(
    config: dict[str, Any],
    store: FundamentalStore,
    *,
    backfill: bool,
) -> dict[str, int]:
    semaphore = asyncio.Semaphore(config["concurrency"])
    totals = {"fetched": 0, "skipped": 0, "inserted": 0, "duplicates": 0, "rejected": 0}

    async def capture(resource: Resource) -> None:
        async with semaphore:
            previous = store.checkpoint(resource.key)
            now = datetime.now(UTC)
            historical = backfill and resource.year is not None
            if previous and (
                (historical and previous["backfill_done"])
                or parse_timestamp(previous["next_due"]) > now
            ):
                totals["skipped"] += 1
                return
            evidence, body = await asyncio.to_thread(
                fetch_resource,
                resource,
                config["request_timeout_seconds"],
                config["max_response_bytes"],
                previous,
            )
            if body:
                evidence["raw_sha256"] = store.archive(body)
            batch = ParsedBatch([], [])
            if evidence["success"] and evidence["http_status"] == 200:
                try:
                    received = parse_timestamp(evidence["received_at"])
                    kind = resource.source["kind"]
                    if kind == "rss":
                        batch = parse_rss(body, received)
                    elif kind == "fed_archive":
                        batch = parse_fed_archive(body, received)
                    elif kind == "ical":
                        batch = parse_calendar(body, received)
                    else:
                        if resource.year is None:
                            raise ValueError("Treasury resource needs an explicit year")
                        batch = parse_treasury(body, received, resource.year)
                    if not batch.records:
                        raise ValueError("no valid records; response retained for diagnosis")
                except (ValueError, TypeError, LookupError, SyntaxError) as exc:
                    evidence.update(success=False, error_type=type(exc).__name__, error=str(exc))
            mode = "historical_backfill" if historical else "live_poll"
            counts = store.record_fetch(
                resource.source,
                evidence,
                batch,
                resource=resource.key,
                mode=mode,
                poll_seconds=resource.source["poll_seconds"],
            )
            totals["fetched"] += 1
            for key, value in counts.items():
                totals[key] += value
            print(
                json.dumps(
                    {
                        "source": resource.source["id"],
                        "year": resource.year,
                        "success": evidence["success"],
                        **counts,
                        "error": evidence.get("error"),
                    }
                ),
                flush=True,
            )

    await asyncio.gather(
        *(capture(item) for item in resources(config, datetime.now(UTC), backfill))
    )
    return totals


def write_status(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


async def run(
    config_path: Path,
    *,
    backfill: bool = False,
    duration_seconds: float = 0,
    report_path: Path | None = None,
    export_path: Path | None = None,
) -> dict[str, Any]:
    if not math.isfinite(duration_seconds) or duration_seconds < 0:
        raise ValueError("duration_seconds must be finite and nonnegative")
    config = load_config(config_path)
    validate_config(config)
    directory = Path(config["storage_dir"])
    report_path = report_path or directory / "status.json"
    deadline = time.monotonic() + duration_seconds
    with FundamentalStore(directory, config["research_symbol"]) as store:
        while True:
            totals = await capture_pass(config, store, backfill=backfill)
            report = store.report()
            report.update(
                generated_at=datetime.now(UTC).isoformat(),
                last_pass=totals,
                config_sha256=digest(config),
                config=config,
                context=store.context(datetime.now(UTC), config["news_max_age_seconds"]),
            )
            write_status(report_path, report)
            if duration_seconds == 0 or time.monotonic() >= deadline:
                break
            await asyncio.sleep(min(30, max(0, deadline - time.monotonic())))
        if export_path:
            report["exported_records"] = store.export(export_path)
            write_status(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/pillar_one_xau_capture.yaml"))
    parser.add_argument("--backfill", action="store_true", help="Resume Treasury years from config")
    parser.add_argument("--duration-seconds", type=float, default=0, help="0 = one bounded pass")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--export", type=Path, help="New JSONL destination; never overwrites")
    args = parser.parse_args()
    report = asyncio.run(
        run(
            args.config,
            backfill=args.backfill,
            duration_seconds=args.duration_seconds,
            report_path=args.report,
            export_path=args.export,
        )
    )
    print(
        json.dumps(
            {
                "records": report["records"],
                "training_ready": report["training_ready"],
                "failed_fetch_count": report["failed_fetch_count"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
