"""Verified public BTC spot/USDT-M candle acquisition, separate from offline replay.

The Binance spot archive is a candle proxy. It is not Ourbit perpetual data and
contains no executable spread, order-book queue, or reconstructed trade flow.
See https://github.com/binance/binance-public-data for the source contract.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import urllib.request
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from bot.features.bars import Bar

SOURCE = "binance_spot_klines"
SOURCE_SYMBOL = "BTCUSDT"
SYMBOL = "BTC_USDT"
INTERVAL_SECONDS = 60
BASE_URL = "https://data.binance.vision/data/spot"
FUTURES_SOURCE = "binance_um_klines"
FUTURES_BASE_URL = "https://data.binance.vision/data/futures/um"
KLINE_HEADER = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_CSV_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_ROWS = 50_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MICROSECOND_START = datetime(2025, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Archive:
    url: str
    start: datetime
    end: datetime
    market: str = "spot"

    def __post_init__(self) -> None:
        if self.market not in {"spot", "um"}:
            raise ValueError("unsupported archive market")

    @property
    def timestamp_units(self) -> int:
        return 1_000_000 if self.market == "spot" and self.start >= MICROSECOND_START else 1_000

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[1]


def archive_plan(start: date, end: date, *, market: str = "spot") -> list[Archive]:
    """Choose monthly archives for full months and daily ones for partial months.

    ``start`` is inclusive and ``end`` exclusive. Both refer to UTC dates.
    """
    if market not in {"spot", "um"}:
        raise ValueError("market must be spot or um")
    if market == "um" and start < date(2019, 9, 8):
        raise ValueError("BTCUSDT USDT-M data starts on 2019-09-08")
    if start >= end:
        raise ValueError("start must be before exclusive end")
    if start < date(2017, 8, 17):
        raise ValueError("BTCUSDT spot data starts on 2017-08-17")
    result: list[Archive] = []
    cursor = start
    while cursor < end:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        if cursor.day == 1 and next_month <= end:
            frequency, suffix, next_cursor = "monthly", cursor.strftime("%Y-%m"), next_month
        else:
            frequency, suffix = "daily", cursor.isoformat()
            next_cursor = cursor + timedelta(days=1)
        result.append(
            Archive(
                url=f"{BASE_URL if market == 'spot' else FUTURES_BASE_URL}"
                f"/{frequency}/klines/{SOURCE_SYMBOL}/1m/"
                f"{SOURCE_SYMBOL}-1m-{suffix}.zip",
                start=datetime.combine(cursor, datetime.min.time(), tzinfo=UTC),
                end=datetime.combine(next_cursor, datetime.min.time(), tzinfo=UTC),
                market=market,
            )
        )
        cursor = next_cursor
    return result


def _download(url: str, maximum_bytes: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "pillar-two-research/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > maximum_bytes:
            raise ValueError(f"download exceeds size limit: {url}")
        body: bytes = response.read(maximum_bytes + 1)
    if len(body) > maximum_bytes:
        raise ValueError(f"download exceeds size limit: {url}")
    return body


def verify_checksum(payload: bytes, checksum: bytes, filename: str) -> str:
    """Verify a ZIP against Binance's official SHA256 sidecar, including its name."""
    try:
        fields = checksum.decode("ascii").strip().split()
    except UnicodeDecodeError as exc:
        raise ValueError("checksum must be ASCII") from exc
    if (
        len(fields) != 2
        or re.fullmatch(r"[a-fA-F0-9]{64}", fields[0]) is None
        or fields[1].removeprefix("*") != filename
    ):
        raise ValueError("invalid SHA256 checksum manifest or archive filename")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != fields[0].lower():
        raise ValueError(f"SHA256 mismatch for {filename}")
    return digest


def _positive_decimal(value: object, field: str, *, allow_zero: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal in {field}") from exc
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero):
        raise ValueError(
            f"{field} must be finite and {'nonnegative' if allow_zero else 'positive'}"
        )
    return number


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a UTC ISO timestamp")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp in {field}") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")
    return timestamp.astimezone(UTC)


def _validate_bar(bar: Bar) -> None:
    if bar.symbol != SYMBOL or bar.interval_seconds != INTERVAL_SECONDS:
        raise ValueError("only BTC_USDT one-minute bars are accepted")
    for timestamp in (bar.start, bar.end):
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise ValueError("bar timestamps must be timezone-aware UTC")
        if timestamp.microsecond or int((timestamp - EPOCH).total_seconds()) % INTERVAL_SECONDS:
            raise ValueError("bar timestamps must be aligned to a one-minute UTC boundary")
    if bar.end - bar.start != timedelta(seconds=INTERVAL_SECONDS):
        raise ValueError("bar end must be exactly one minute after start (exclusive)")
    if any(not p.is_finite() or p <= 0 for p in (bar.open, bar.high, bar.low, bar.close)):
        raise ValueError("OHLC must be finite and positive")
    if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
        raise ValueError("OHLC range does not contain open and close")
    if not bar.volume.is_finite() or bar.volume < 0:
        raise ValueError("volume must be finite and nonnegative")
    if type(bar.trades) is not int or bar.trades < 0:
        raise ValueError("trades must be a nonnegative integer")


def _check_order(previous: Bar | None, bar: Bar) -> None:
    if previous is not None and bar.start < previous.end:
        raise ValueError("bars are duplicated, overlapping, or out of chronological order")


def parse_archive(payload: bytes, archive: Archive) -> list[Bar]:
    """Parse a verified ZIP in memory, never extracting archive paths to disk."""
    bars: list[Bar] = []
    expected_name = archive.filename.removesuffix(".zip") + ".csv"
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
            members = zipped.infolist()
            if len(members) != 1 or members[0].filename != expected_name:
                raise ValueError("archive must contain exactly its expected CSV filename")
            if members[0].file_size > MAX_CSV_BYTES:
                raise ValueError("uncompressed archive exceeds size limit")
            with zipped.open(members[0]) as raw, io.TextIOWrapper(raw, encoding="utf-8") as stream:
                for index, row in enumerate(csv.reader(stream), 1):
                    if index == 1 and archive.market == "um" and tuple(row) == KLINE_HEADER:
                        continue
                    if index > MAX_ARCHIVE_ROWS + (archive.market == "um"):
                        raise ValueError("archive exceeds row limit")
                    if len(row) != 12:
                        raise ValueError(f"CSV row {index}: expected 12 kline fields")
                    try:
                        opening, closing, trades = int(row[0]), int(row[6]), int(row[8])
                    except ValueError as exc:
                        raise ValueError(
                            f"CSV row {index}: invalid timestamp or trade count"
                        ) from exc
                    # Spot archives switched units on 2025-01-01. Avoid float epoch conversion.
                    units = archive.timestamp_units
                    if closing != opening + INTERVAL_SECONDS * units - 1:
                        raise ValueError(f"CSV row {index}: invalid close timestamp or units")
                    start = EPOCH + timedelta(microseconds=opening * (1_000_000 // units))
                    if start < archive.start or start >= archive.end:
                        raise ValueError(f"CSV row {index}: timestamp outside archive period")
                    bar = Bar(
                        symbol=SYMBOL,
                        interval_seconds=INTERVAL_SECONDS,
                        start=start,
                        end=start + timedelta(seconds=INTERVAL_SECONDS),
                        open=_positive_decimal(row[1], "open"),
                        high=_positive_decimal(row[2], "high"),
                        low=_positive_decimal(row[3], "low"),
                        close=_positive_decimal(row[4], "close"),
                        volume=_positive_decimal(row[5], "volume", allow_zero=True),
                        trades=trades,
                    )
                    _validate_bar(bar)
                    _check_order(bars[-1] if bars else None, bar)
                    bars.append(bar)
    except (zipfile.BadZipFile, UnicodeDecodeError, OverflowError) as exc:
        raise ValueError("invalid candle archive") from exc
    if not bars:
        raise ValueError("archive contains no bars")
    return bars


def bar_record(bar: Bar, *, source: str = SOURCE) -> dict[str, object]:
    """Encode prices as decimal strings and timestamps as UTC ISO strings."""
    _validate_bar(bar)
    if source not in {SOURCE, FUTURES_SOURCE}:
        raise ValueError("unsupported candle source")
    return {
        "schema_version": 1,
        "source": source,
        "source_symbol": SOURCE_SYMBOL,
        "symbol": bar.symbol,
        "interval_seconds": bar.interval_seconds,
        "start": bar.start.isoformat(),
        "end": bar.end.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
        "trades": bar.trades,
    }


def _record_bar(record: Mapping[str, object]) -> Bar:
    if (
        type(record.get("schema_version")) is not int
        or record.get("schema_version") != 1
        or record.get("source") not in (SOURCE, FUTURES_SOURCE)
        or record.get("source_symbol") != SOURCE_SYMBOL
    ):
        raise ValueError("unsupported candle schema or source")
    if (
        record.get("symbol") != SYMBOL
        or type(record.get("interval_seconds")) is not int
        or record.get("interval_seconds") != INTERVAL_SECONDS
    ):
        raise ValueError("only BTC_USDT one-minute bars are accepted")
    trades = record.get("trades")
    if type(trades) is not int or trades < 0:
        raise ValueError("trades must be a nonnegative integer")
    bar = Bar(
        symbol=SYMBOL,
        interval_seconds=INTERVAL_SECONDS,
        start=_utc(record.get("start"), "start"),
        end=_utc(record.get("end"), "end"),
        open=_positive_decimal(record.get("open"), "open"),
        high=_positive_decimal(record.get("high"), "high"),
        low=_positive_decimal(record.get("low"), "low"),
        close=_positive_decimal(record.get("close"), "close"),
        volume=_positive_decimal(record.get("volume"), "volume", allow_zero=True),
        trades=trades,
    )
    _validate_bar(bar)
    return bar


def provenance_path(path: Path | str) -> Path:
    path = Path(path)
    return path.with_suffix(path.suffix + ".provenance.json")


def read_bars(path: Path | str) -> list[Bar]:
    """Read strict, chronologically ordered bars offline; preserve and report gaps.

    A neighboring provenance manifest, when present, must match the file SHA256.
    Gaps are never silently filled or sorted. Call ``bars_quality`` to inspect them.
    """
    path = Path(path)
    bars: list[Bar] = []
    digest = hashlib.sha256()
    source: object = None
    with path.open("rb") as stream:
        for index, line in enumerate(stream, 1):
            digest.update(line)
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("expected a JSON object")
                bar = _record_bar(record)
                if source is not None and source != record["source"]:
                    raise ValueError("mixed candle source venues/markets")
                source = record["source"]
                _check_order(bars[-1] if bars else None, bar)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError(f"{path}:{index}: {exc}") from exc
            bars.append(bar)
    if not bars:
        raise ValueError("input contains no bars")
    sidecar = provenance_path(path)
    if sidecar.exists():
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("normalized_sha256") != digest.hexdigest()
        ):
            raise ValueError("normalized data SHA256 does not match provenance manifest")
        if "source" in manifest and manifest["source"] != source:
            raise ValueError("candle source differs from provenance manifest")
    return bars


def bars_quality(
    bars: Sequence[Bar],
    *,
    expected_start: datetime | None = None,
    expected_end: datetime | None = None,
) -> dict[str, object]:
    """Report real gaps, including requested coverage edges when given."""
    if not bars:
        raise ValueError("input contains no bars")
    gaps: list[dict[str, object]] = []
    cursor = expected_start or bars[0].start
    missing = 0
    for bar in bars:
        _validate_bar(bar)
        if bar.start < cursor:
            raise ValueError("bars are duplicated, unordered, or outside requested coverage")
        if bar.start > cursor:
            count = int((bar.start - cursor).total_seconds()) // INTERVAL_SECONDS
            gaps.append({"start": cursor.isoformat(), "end": bar.start.isoformat(), "bars": count})
            missing += count
        cursor = bar.end
    if expected_end is not None:
        if cursor > expected_end:
            raise ValueError("bars extend beyond requested coverage")
        if cursor < expected_end:
            count = int((expected_end - cursor).total_seconds()) // INTERVAL_SECONDS
            gaps.append(
                {"start": cursor.isoformat(), "end": expected_end.isoformat(), "bars": count}
            )
            missing += count
    return {
        "bars": len(bars),
        "first_start": bars[0].start.isoformat(),
        "last_end": bars[-1].end.isoformat(),
        "missing_bars": missing,
        "gap_count": len(gaps),
        "gaps": gaps,
        "zero_volume_bars": sum(bar.volume == 0 for bar in bars),
    }


def _write_bars(path: Path, bars: Iterable[Bar], *, source: str = SOURCE) -> str:
    digest = hashlib.sha256()
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        for bar in bars:
            line = (
                json.dumps(bar_record(bar, source=source), separators=(",", ":")) + "\n"
            ).encode("utf-8")
            stream.write(line)
            digest.update(line)
    temporary.replace(path)
    return digest.hexdigest()


def download_bars(
    *,
    start: date,
    end: date,
    output: Path | str,
    raw_directory: Path | str | None = None,
    market: str = "spot",
) -> dict[str, object]:
    """Acquire, verify, normalize and retain the requested public archive history.

    Network access exists only here, never in ``read_bars`` or a replay loop.
    Existing raw archives are reused only after verifying their saved checksums.
    """
    plan = archive_plan(start, end, market=market)
    source = SOURCE if market == "spot" else FUTURES_SOURCE
    if end > datetime.now(UTC).date():
        raise ValueError("exclusive end must not include the current or a future UTC day")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_directory = Path(raw_directory) if raw_directory else output.parent / "raw" / source
    raw_directory.mkdir(parents=True, exist_ok=True)
    bars: list[Bar] = []
    archives: list[dict[str, Any]] = []
    for archive in plan:
        raw_path = raw_directory / archive.filename
        checksum_path = raw_directory / (archive.filename + ".CHECKSUM")
        cached = raw_path.exists() and checksum_path.exists()
        if cached:
            if raw_path.stat().st_size > MAX_ARCHIVE_BYTES or checksum_path.stat().st_size > 4096:
                raise ValueError("cached archive or checksum exceeds size limit")
            payload, checksum = raw_path.read_bytes(), checksum_path.read_bytes()
        else:
            checksum = _download(archive.url + ".CHECKSUM", 4096)
            payload = _download(archive.url, MAX_ARCHIVE_BYTES)
        digest = verify_checksum(payload, checksum, archive.filename)
        parsed = parse_archive(payload, archive)
        if bars:
            _check_order(bars[-1], parsed[0])
        bars.extend(parsed)
        if not cached:
            raw_path.write_bytes(payload)
            checksum_path.write_bytes(checksum)
        archives.append(
            {
                "url": archive.url,
                "checksum_url": archive.url + ".CHECKSUM",
                "sha256": digest,
                "raw_path": str(raw_path.resolve()),
                "bytes": len(payload),
                "bars": len(parsed),
                "timestamp_unit": "us" if archive.timestamp_units == 1_000_000 else "ms",
                "cached": cached,
                "retrieved_at": datetime.fromtimestamp(raw_path.stat().st_mtime, UTC).isoformat(),
                "verified_at": datetime.now(UTC).isoformat(),
            }
        )
    quality = bars_quality(bars, expected_start=plan[0].start, expected_end=plan[-1].end)
    normalized_sha256 = _write_bars(output, bars, source=source)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "source": source,
        "source_symbol": SOURCE_SYMBOL,
        "symbol": SYMBOL,
        "interval_seconds": INTERVAL_SECONDS,
        "requested_start": plan[0].start.isoformat(),
        "requested_end": plan[-1].end.isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
        "source_documentation": "https://github.com/binance/binance-public-data",
        "normalized_sha256": normalized_sha256,
        "archives": archives,
        "quality": quality,
        "limitations": [
            f"Binance {market} candle proxy, not Ourbit executable market data.",
            "No bid/ask spread, queue position, funding, or sub-minute price ordering.",
            "No synthetic order-book imbalance or aggressive trade flow is inferred.",
        ],
    }
    provenance_path(output).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start", required=True, type=date.fromisoformat, help="UTC date, inclusive"
    )
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="UTC date, exclusive")
    parser.add_argument("--symbol", choices=[SOURCE_SYMBOL], default=SOURCE_SYMBOL)
    parser.add_argument("--interval", choices=["1m"], default="1m")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-directory", type=Path)
    parser.add_argument("--market", choices=["spot", "um"], default="spot")
    args = parser.parse_args()
    manifest = download_bars(
        start=args.start,
        end=args.end,
        output=args.output,
        raw_directory=args.raw_directory,
        market=args.market,
    )
    print(json.dumps({"output": str(args.output), "quality": manifest["quality"]}, indent=2))


if __name__ == "__main__":
    main()
