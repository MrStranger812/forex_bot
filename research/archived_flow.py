"""Recover measured taker-buy volume from the original checksum-verified spot ZIPs."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from bot.features.bars import Bar
from research.market_data import (
    EPOCH,
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_ROWS,
    MAX_CSV_BYTES,
    provenance_path,
    verify_checksum,
)


def parse_taker_volume(
    payload: bytes, filename: str, timestamp_unit: str, bars: Mapping[datetime, Bar]
) -> dict[datetime, Decimal]:
    if len(payload) > MAX_ARCHIVE_BYTES or timestamp_unit not in {"us", "ms"}:
        raise ValueError("invalid archive size or timestamp unit")
    values: dict[datetime, Decimal] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if (
            len(members) != 1
            or members[0].filename != filename.removesuffix(".zip") + ".csv"
            or members[0].file_size > MAX_CSV_BYTES
        ):
            raise ValueError("archive must contain its expected bounded CSV")
        with archive.open(members[0]) as raw, io.TextIOWrapper(raw, encoding="utf-8") as handle:
            for count, row in enumerate(csv.reader(handle), 1):
                if count > MAX_ARCHIVE_ROWS or len(row) != 12:
                    raise ValueError("invalid archive row count or kline schema")
                start = EPOCH + timedelta(
                    microseconds=int(row[0]) * (1 if timestamp_unit == "us" else 1000)
                )
                bar = bars.get(start)
                if bar is None:
                    continue
                try:
                    recorded = tuple(Decimal(value) for value in row[1:6])
                    buy = Decimal(row[9])
                except InvalidOperation as exc:
                    raise ValueError("invalid kline decimal") from exc
                if recorded != (bar.open, bar.high, bar.low, bar.close, bar.volume):
                    raise ValueError("archive OHLCV differs from normalized candle")
                if int(row[8]) != bar.trades:
                    raise ValueError("archive trade count differs from normalized candle")
                if not buy.is_finite() or not 0 <= buy <= bar.volume:
                    raise ValueError("taker-buy volume must be finite and within total volume")
                if bar.end in values:
                    raise ValueError("duplicate taker-volume candle")
                values[bar.end] = buy
    return values


def read_archived_flow(
    input_path: Path, bars: Sequence[Bar]
) -> tuple[dict[datetime, Decimal], dict[str, Any]]:
    """Read only local archives; exact OHLCV/trade-count matching binds flow to input bars."""
    manifest = json.loads(provenance_path(input_path).read_text(encoding="utf-8"))
    if manifest.get("source") != "binance_spot_klines":
        raise ValueError("flow requires Binance spot provenance")
    requested = {bar.start: bar for bar in bars}
    flow: dict[datetime, Decimal] = {}
    sources: list[dict[str, str]] = []
    for item in manifest["archives"]:
        path = Path(item["raw_path"])
        checksum = Path(str(path) + ".CHECKSUM")
        if path.stat().st_size > MAX_ARCHIVE_BYTES or checksum.stat().st_size > 4096:
            raise ValueError("flow archive exceeds size limit")
        payload = path.read_bytes()
        digest = verify_checksum(payload, checksum.read_bytes(), path.name)
        if digest != item["sha256"]:
            raise ValueError("flow archive differs from original provenance")
        parsed = parse_taker_volume(payload, path.name, item["timestamp_unit"], requested)
        if set(flow) & set(parsed):
            raise ValueError("duplicate flow across archives")
        flow.update(parsed)
        if parsed:
            sources.append({"url": item["url"], "sha256": digest})
    if len(flow) != len(bars):
        raise ValueError("taker-volume archives do not cover every requested candle")
    return flow, {
        "bars": len(flow),
        "sources": sources,
        "field": "Binance spot kline taker buy base asset volume (column 10)",
        "documentation": "https://github.com/binance/binance-public-data",
    }
