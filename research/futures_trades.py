"""Acquire verified individual USDT-M trades and audit them against minute candles.

Raw exchange CSVs remain in their original ZIPs. Five-second summaries preserve
observed trade order and measured aggressor volume; they contain no quotes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from bot.features.bars import Bar
from research.archived_flow import read_archived_flow
from research.market_data import EPOCH, _download, provenance_path, read_bars, verify_checksum

HEADER = ("id", "price", "qty", "quote_qty", "time", "is_buyer_maker")
MAX_ZIP_BYTES = 128 * 1024 * 1024
MAX_CSV_BYTES = 1024 * 1024 * 1024
MAX_ROWS = 15_000_000


@dataclass(frozen=True, slots=True)
class PublicTrade:
    trade_id: int
    timestamp_ms: int
    price: Decimal
    quantity: Decimal
    buyer_maker: bool


def iter_trades(payload: bytes, day: date) -> Iterator[PublicTrade]:
    """Reject ambiguous schema, nonfinite money, duplicates and reversed event order."""
    if len(payload) > MAX_ZIP_BYTES:
        raise ValueError("trade archive exceeds size limit")
    filename = f"BTCUSDT-trades-{day.isoformat()}.csv"
    start_ms = (day - EPOCH.date()).days * 86_400_000
    previous_id, previous_time = -1, -1
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if (
            len(members) != 1 or members[0].filename != filename
            or members[0].file_size > MAX_CSV_BYTES
        ):
            raise ValueError("trade archive must contain its expected bounded CSV")
        with archive.open(members[0]) as raw, io.TextIOWrapper(raw, encoding="utf-8") as handle:
            reader = csv.reader(handle)
            if tuple(next(reader, ())) != HEADER:
                raise ValueError("unsupported individual trade header")
            count = 0
            for count, row in enumerate(reader, 1):
                if count > MAX_ROWS or len(row) != len(HEADER):
                    raise ValueError("invalid trade row size or count")
                trade_id, at = int(row[0]), int(row[4])
                try:
                    price, quantity, quote = (Decimal(value) for value in row[1:4])
                except InvalidOperation as exc:
                    raise ValueError("invalid trade decimal") from exc
                if any(not value.is_finite() or value <= 0 for value in (price, quantity, quote)):
                    raise ValueError("trade money must be positive and finite")
                if abs(price * quantity - quote) > Decimal("0.00000001"):
                    raise ValueError("trade quote quantity does not match price times quantity")
                if row[5] not in {"true", "false"}:
                    raise ValueError("buyer-maker flag must be an explicit boolean")
                if not start_ms <= at < start_ms + 86_400_000:
                    raise ValueError("trade timestamp outside requested UTC day or wrong units")
                if trade_id <= previous_id or at < previous_time:
                    raise ValueError("duplicate or out-of-order individual trades")
                previous_id, previous_time = trade_id, at
                yield PublicTrade(trade_id, at, price, quantity, row[5] == "true")
            if count == 0:
                raise ValueError("empty individual trade archive")


@dataclass(slots=True)
class TradeBucket:
    start_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    taker_buy_volume: Decimal
    trades: int
    first_trade_id: int
    last_trade_id: int

    def record(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source": "binance_um_individual_trades",
            "symbol": "BTC_USDT",
            "interval_seconds": 5,
            "start": (EPOCH + timedelta(milliseconds=self.start_ms)).isoformat(),
            "end": (EPOCH + timedelta(milliseconds=self.start_ms + 5000)).isoformat(),
            "open": str(self.open), "high": str(self.high), "low": str(self.low),
            "close": str(self.close), "volume": str(self.volume),
            "taker_buy_volume": str(self.taker_buy_volume),
            "taker_sell_volume": str(self.volume - self.taker_buy_volume),
            "trades": self.trades,
            "first_trade_id": self.first_trade_id, "last_trade_id": self.last_trade_id,
        }


def summarize_trades(payload: bytes, day: date) -> tuple[list[TradeBucket], dict[str, Any]]:
    buckets: list[TradeBucket] = []
    count, skipped_ids, id_gap_events, max_gap_ms = 0, 0, 0, 0
    first: PublicTrade | None = None
    previous: PublicTrade | None = None
    for trade in iter_trades(payload, day):
        if previous is not None:
            skipped = trade.trade_id - previous.trade_id - 1
            skipped_ids += skipped
            id_gap_events += skipped > 0
            max_gap_ms = max(max_gap_ms, trade.timestamp_ms - previous.timestamp_ms)
        first = first or trade
        previous = trade
        count += 1
        bucket_start = trade.timestamp_ms - trade.timestamp_ms % 5000
        if not buckets or buckets[-1].start_ms != bucket_start:
            buckets.append(TradeBucket(
                bucket_start, trade.price, trade.price, trade.price, trade.price,
                Decimal(0), Decimal(0), 0, trade.trade_id, trade.trade_id,
            ))
        bucket = buckets[-1]
        bucket.high, bucket.low = max(bucket.high, trade.price), min(bucket.low, trade.price)
        bucket.close, bucket.last_trade_id = trade.price, trade.trade_id
        bucket.volume += trade.quantity
        if not trade.buyer_maker:  # Buyer taking liquidity is the aggressive buyer.
            bucket.taker_buy_volume += trade.quantity
        bucket.trades += 1
    assert first is not None and previous is not None  # iter_trades rejects empty data.
    return buckets, {
        "trade_count": count,
        "skipped_trade_id_values": skipped_ids,
        "trade_id_gap_events": id_gap_events,
        "consecutive_trade_ids": skipped_ids == 0,
        "first_trade_at": (EPOCH + timedelta(milliseconds=first.timestamp_ms)).isoformat(),
        "last_trade_at": (EPOCH + timedelta(milliseconds=previous.timestamp_ms)).isoformat(),
        "max_intertrade_gap_ms": max_gap_ms,
        "five_second_buckets": len(buckets),
        "empty_five_second_intervals": 17280 - len(buckets),
    }


def compare_candles(
    buckets: Sequence[TradeBucket], candles: Sequence[Bar], taker_buy: dict[datetime, Decimal],
) -> dict[str, Any]:
    """Independently bind trade coverage, OHLCV and aggressor volume to source candles."""
    grouped: dict[int, list[TradeBucket]] = {}
    for bucket in buckets:
        grouped.setdefault(bucket.start_ms // 60000, []).append(bucket)
    mismatches: list[str] = []
    for candle in candles:
        minute = int((candle.start - EPOCH).total_seconds()) // 60
        parts = grouped.pop(minute, [])
        if not parts:
            mismatches.append(candle.start.isoformat())
            continue
        observed = (
            parts[0].open, max(p.high for p in parts), min(p.low for p in parts),
            parts[-1].close, sum((p.volume for p in parts), Decimal(0)),
            sum(p.trades for p in parts), sum((p.taker_buy_volume for p in parts), Decimal(0)),
        )
        expected = (
            candle.open, candle.high, candle.low, candle.close, candle.volume,
            candle.trades, taker_buy[candle.end],
        )
        if observed != expected:
            mismatches.append(candle.start.isoformat())
    return {
        "candles_checked": len(candles), "mismatched_minutes": mismatches,
        "unmatched_trade_minutes": len(grouped),
        "passed": bool(candles) and not mismatches and not grouped,
    }


def acquire(day: date, candles_path: Path, output_dir: Path) -> dict[str, Any]:
    if any((output_dir / name).exists() for name in ("trades_5s.jsonl", "report.json")):
        raise FileExistsError("choose a new output directory to preserve the prior trade audit")
    if day >= datetime.now(UTC).date() or day < date(2019, 9, 8):
        raise ValueError("choose a completed UTC day in the USDT-M archive period")
    manifest = json.loads(provenance_path(candles_path).read_text(encoding="utf-8"))
    if manifest.get("source") != "binance_um_klines":
        raise ValueError("trade audit requires USDT-M candles, not spot")
    candles = [bar for bar in read_bars(candles_path) if bar.start.date() == day]
    if len(candles) != 1440:
        raise ValueError("trade audit requires complete minute coverage for the UTC day")
    taker_buy, _ = read_archived_flow(candles_path, candles)
    filename = f"BTCUSDT-trades-{day.isoformat()}.zip"
    url = f"https://data.binance.vision/data/futures/um/daily/trades/BTCUSDT/{filename}"
    raw_dir = candles_path.parent / "raw" / "binance_um_trades"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path, checksum_path = raw_dir / filename, raw_dir / (filename + ".CHECKSUM")
    cached = raw_path.exists() and checksum_path.exists()
    if cached:
        if raw_path.stat().st_size > MAX_ZIP_BYTES or checksum_path.stat().st_size > 4096:
            raise ValueError("cached trade archive exceeds size limit")
        payload, checksum = raw_path.read_bytes(), checksum_path.read_bytes()
    else:
        checksum = _download(url + ".CHECKSUM", 4096)
        payload = _download(url, MAX_ZIP_BYTES)
    digest = verify_checksum(payload, checksum, filename)
    if not cached:
        raw_path.write_bytes(payload)
        checksum_path.write_bytes(checksum)
    print(f"Auditing {len(payload)} verified compressed bytes of individual trades", flush=True)
    buckets, quality = summarize_trades(payload, day)
    comparison = compare_candles(buckets, candles, taker_buy)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "trades_5s.jsonl"
    # A failed completeness audit produces a report but no usable normalized output.
    # The public schema identifies trades by ID but does not promise consecutive
    # integers. Require exact candle counts/OHLCV/flow, and disclose numeric gaps.
    passed = comparison["passed"]
    normalized_hash: str | None = None
    if passed:
        with output.open("w", encoding="utf-8", newline="\n") as handle:
            for bucket in buckets:
                handle.write(json.dumps(bucket.record(), separators=(",", ":")) + "\n")
        normalized_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    report = {
        "source": "binance_um_individual_trades", "day": day.isoformat(),
        "url": url, "checksum_url": url + ".CHECKSUM", "sha256": digest,
        "raw_path": str(raw_path.resolve()),
        "documentation": "https://github.com/binance/binance-public-data",
        "verified_at": datetime.now(UTC).isoformat(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "candles_sha256": hashlib.sha256(candles_path.read_bytes()).hexdigest(),
        "quality": quality, "candle_comparison": comparison, "passed": passed,
        "normalized_output": str(output) if passed else None,
        "normalized_sha256": normalized_hash,
        "limitations": [
            "Binance USDT-M trades are a proxy, not Ourbit trades or executable quotes.",
            "Five-second buckets contain observed trades only; inactive periods are not filled.",
            "This sample audits data plumbing; it is not enough for model validation.",
            "Numeric trade-ID gaps are reported; candle agreement is not proof of every event.",
        ],
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise ValueError(f"trade completeness audit failed: {output_dir / 'report.json'}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--candles", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(acquire(args.day, args.candles, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
