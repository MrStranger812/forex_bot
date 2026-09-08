from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bot.features.bars import Bar
from research import market_data
from research.market_data import (
    Archive,
    archive_plan,
    bar_record,
    bars_quality,
    download_bars,
    parse_archive,
    provenance_path,
    read_bars,
    verify_checksum,
)


def candle(start: datetime | None = None) -> Bar:
    start = start or datetime(2026, 5, 1, tzinfo=UTC)
    return Bar(
        "BTC_USDT",
        60,
        start,
        start + timedelta(minutes=1),
        Decimal("100"),
        Decimal("102"),
        Decimal("99"),
        Decimal("101"),
        Decimal("2"),
        4,
    )


def write_records(path: Path, *records: dict[str, object]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def archive_payload(archive: Archive, rows: list[str], *, name: str | None = None) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr(name or archive.filename.replace(".zip", ".csv"), "\n".join(rows) + "\n")
    return stream.getvalue()


def csv_row(start: datetime, units: int) -> str:
    opening = int((start - market_data.EPOCH).total_seconds()) * units
    return f"{opening},100,102,99,101,2,{opening + 60 * units - 1},202,4,1,101,0"


def test_archive_plan_uses_full_months_and_daily_edges() -> None:
    plan = archive_plan(date(2026, 4, 30), date(2026, 6, 2))
    assert len(plan) == 3
    assert "/daily/" in plan[0].url and plan[0].filename.endswith("2026-04-30.zip")
    assert "/monthly/" in plan[1].url and plan[1].filename.endswith("2026-05.zip")
    assert "/daily/" in plan[2].url and plan[2].filename.endswith("2026-06-01.zip")
    assert plan[-1].end == datetime(2026, 6, 2, tzinfo=UTC)


@pytest.mark.parametrize(
    "start,end",
    [
        (date(2026, 5, 1), date(2026, 5, 1)),
        (date(2026, 5, 2), date(2026, 5, 1)),
        (date(2016, 1, 1), date(2026, 5, 1)),
    ],
)
def test_invalid_archive_ranges_fail(start: date, end: date) -> None:
    with pytest.raises(ValueError):
        archive_plan(start, end)


@pytest.mark.parametrize(
    "start,units",
    [
        (date(2024, 12, 31), 1_000),
        (date(2025, 1, 1), 1_000_000),
        (date(2026, 5, 1), 1_000_000),
    ],
)
def test_archive_timestamp_units_and_exclusive_close(start: date, units: int) -> None:
    archive = archive_plan(start, start + timedelta(days=1))[0]
    payload = archive_payload(archive, [csv_row(archive.start, units)])
    bars = parse_archive(payload, archive)
    assert bars == [candle(archive.start)]
    assert bars[0].end == archive.start + timedelta(minutes=1)


def test_wrong_timestamp_unit_is_rejected() -> None:
    archive = archive_plan(date(2025, 1, 1), date(2025, 1, 2))[0]
    with pytest.raises(ValueError, match="close timestamp or units"):
        parse_archive(archive_payload(archive, [csv_row(archive.start, 1_000)]), archive)


@pytest.mark.parametrize(
    "row",
    [
        "1,2,3",
        "not-time,100,102,99,101,2,1,202,4,1,101,0",
        "1735689600000000,NaN,102,99,101,2,1735689659999999,202,4,1,101,0",
        "1735689600000000,100,90,99,101,2,1735689659999999,202,4,1,101,0",
    ],
)
def test_malformed_archive_rows_fail(row: str) -> None:
    archive = archive_plan(date(2025, 1, 1), date(2025, 1, 2))[0]
    with pytest.raises(ValueError):
        parse_archive(archive_payload(archive, [row]), archive)


def test_archive_rejects_duplicates_and_wrong_period() -> None:
    archive = archive_plan(date(2025, 1, 1), date(2025, 1, 2))[0]
    row = csv_row(archive.start, 1_000_000)
    with pytest.raises(ValueError, match="duplicated"):
        parse_archive(archive_payload(archive, [row, row]), archive)
    with pytest.raises(ValueError, match="outside archive period"):
        parse_archive(archive_payload(archive, [csv_row(archive.end, 1_000_000)]), archive)


def test_archive_never_extracts_untrusted_members(tmp_path: Path) -> None:
    archive = archive_plan(date(2025, 1, 1), date(2025, 1, 2))[0]
    payload = archive_payload(archive, [csv_row(archive.start, 1_000_000)], name="../escape.csv")
    with pytest.raises(ValueError, match="expected CSV"):
        parse_archive(payload, archive)
    assert list(tmp_path.iterdir()) == []


def test_archive_size_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    archive = archive_plan(date(2025, 1, 1), date(2025, 1, 2))[0]
    payload = archive_payload(archive, [csv_row(archive.start, 1_000_000)])
    monkeypatch.setattr(market_data, "MAX_CSV_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        parse_archive(payload, archive)


def test_official_checksum_validates_digest_and_filename() -> None:
    payload = b"public archive"
    digest = hashlib.sha256(payload).hexdigest()
    assert verify_checksum(payload, f"{digest}  BTCUSDT.zip".encode(), "BTCUSDT.zip") == digest
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        verify_checksum(payload + b"changed", f"{digest}  BTCUSDT.zip".encode(), "BTCUSDT.zip")
    with pytest.raises(ValueError, match="archive filename"):
        verify_checksum(payload, f"{digest}  ETHUSDT.zip".encode(), "BTCUSDT.zip")
    with pytest.raises(ValueError, match="invalid SHA256"):
        verify_checksum(payload, b"abc BTCUSDT.zip", "BTCUSDT.zip")


def test_read_bars_is_offline_and_retains_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bars.jsonl"
    first = candle()
    second = candle(first.start + timedelta(minutes=3))
    write_records(path, bar_record(first), bar_record(second))

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("replay must not access the network")

    monkeypatch.setattr(market_data.urllib.request, "urlopen", no_network)
    bars = read_bars(path)
    assert bars == [first, second]
    report = bars_quality(
        bars,
        expected_start=first.start - timedelta(minutes=1),
        expected_end=second.end + timedelta(minutes=2),
    )
    assert report["missing_bars"] == 5
    assert report["gap_count"] == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("symbol", "ETH_USDT"),
        ("source_symbol", "ETHUSDT"),
        ("source", "synthetic"),
        ("schema_version", True),
        ("interval_seconds", 1),
        ("trades", True),
        ("trades", -1),
        ("open", "NaN"),
        ("close", "Infinity"),
        ("low", "0"),
        ("high", "90"),
        ("volume", "-1"),
        ("volume", "NaN"),
        ("open", 100.0),
        ("start", "2026-05-01T00:00:00"),
        ("start", "2026-05-01T03:30:00+03:30"),
        ("start", "2026-05-01T00:00:01+00:00"),
        ("end", "2026-05-01T00:02:00+00:00"),
    ],
)
def test_read_rejects_malformed_normalized_values(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path = tmp_path / "bars.jsonl"
    record = bar_record(candle())
    record[field] = value
    write_records(path, record)
    with pytest.raises(ValueError):
        read_bars(path)


def test_read_rejects_duplicate_out_of_order_and_empty_data(tmp_path: Path) -> None:
    path = tmp_path / "bars.jsonl"
    first = candle()
    second = candle(first.start + timedelta(minutes=1))
    for records in [
        [bar_record(first), bar_record(first)],
        [bar_record(second), bar_record(first)],
    ]:
        write_records(path, *records)
        with pytest.raises(ValueError, match="duplicated"):
            read_bars(path)
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="no bars"):
        read_bars(path)


def test_zero_volume_and_zero_trades_are_valid(tmp_path: Path) -> None:
    path = tmp_path / "bars.jsonl"
    bar = replace(candle(), volume=Decimal(0), trades=0)
    write_records(path, bar_record(bar))
    assert read_bars(path) == [bar]
    assert bars_quality([bar])["zero_volume_bars"] == 1


def test_read_checks_neighboring_provenance_hash(tmp_path: Path) -> None:
    path = tmp_path / "bars.jsonl"
    write_records(path, bar_record(candle()))
    provenance_path(path).write_text(json.dumps({"normalized_sha256": "wrong"}), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA256"):
        read_bars(path)


def test_download_preserves_verifiable_provenance_and_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start, end = date(2025, 1, 1), date(2025, 1, 2)
    archive = archive_plan(start, end)[0]
    payload = archive_payload(archive, [csv_row(archive.start, 1_000_000)])
    digest = hashlib.sha256(payload).hexdigest()
    calls: list[str] = []

    def fake_download(url: str, maximum_bytes: int) -> bytes:
        calls.append(url)
        if url.endswith(".CHECKSUM"):
            return f"{digest}  {archive.filename}".encode()
        return payload

    monkeypatch.setattr(market_data, "_download", fake_download)
    output = tmp_path / "bars.jsonl"
    manifest = download_bars(start=start, end=end, output=output)
    assert len(calls) == 2
    assert manifest["normalized_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert manifest["quality"] == bars_quality(
        [candle(archive.start)],
        expected_start=archive.start,
        expected_end=archive.end,
    )
    assert read_bars(output) == [candle(archive.start)]
    download_bars(start=start, end=end, output=output)
    assert len(calls) == 2  # Verified local cache, no acquisition calls during replay.


def test_bad_download_checksum_never_writes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start, end = date(2025, 1, 1), date(2025, 1, 2)
    archive = archive_plan(start, end)[0]

    def bad_download(url: str, maximum_bytes: int) -> bytes:
        if url.endswith(".CHECKSUM"):
            return f"{'0' * 64}  {archive.filename}".encode()
        return b"corrupted"

    monkeypatch.setattr(market_data, "_download", bad_download)
    output = tmp_path / "bars.jsonl"
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        download_bars(start=start, end=end, output=output)
    assert not output.exists()
    assert not provenance_path(output).exists()


def test_futures_archive_header_units_and_source(tmp_path: Path) -> None:
    archive = archive_plan(date(2026, 5, 1), date(2026, 5, 2), market="um")[0]
    assert "/futures/um/" in archive.url
    assert archive.timestamp_units == 1000
    payload = archive_payload(
        archive, [",".join(market_data.KLINE_HEADER), csv_row(archive.start, 1000)]
    )
    bars = parse_archive(payload, archive)
    assert bars == [candle(archive.start)]
    path = tmp_path / "futures.jsonl"
    write_records(path, bar_record(bars[0], source=market_data.FUTURES_SOURCE))
    assert read_bars(path) == bars
    with pytest.raises(ValueError, match="close timestamp or units"):
        parse_archive(archive_payload(archive, [csv_row(archive.start, 1000000)]), archive)


def test_futures_flow_uses_actual_archive_values() -> None:
    from research.archived_flow import parse_taker_volume

    archive = archive_plan(date(2026, 5, 1), date(2026, 5, 2), market="um")[0]
    payload = archive_payload(
        archive, [",".join(market_data.KLINE_HEADER), csv_row(archive.start, 1000)]
    )
    bar = candle(archive.start)
    values = parse_taker_volume(payload, archive.filename, "ms", {bar.start: bar}, futures=True)
    assert values == {bar.end: Decimal(1)}


def test_mixed_spot_futures_data_and_wrong_manifest_source_fail(tmp_path: Path) -> None:
    path = tmp_path / "mixed.jsonl"
    first = candle()
    second = candle(first.end)
    write_records(
        path, bar_record(first), bar_record(second, source=market_data.FUTURES_SOURCE)
    )
    with pytest.raises(ValueError, match="mixed candle source"):
        read_bars(path)
    write_records(path, bar_record(first))
    provenance_path(path).write_text(json.dumps({
        "normalized_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source": market_data.FUTURES_SOURCE,
    }))
    with pytest.raises(ValueError, match="source differs"):
        read_bars(path)


def test_futures_download_has_distinct_provenance_and_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    start, end = date(2026, 5, 1), date(2026, 5, 2)
    archive = archive_plan(start, end, market="um")[0]
    payload = archive_payload(
        archive, [",".join(market_data.KLINE_HEADER), csv_row(archive.start, 1000)]
    )
    digest = hashlib.sha256(payload).hexdigest()

    def fetch(url: str, maximum_bytes: int) -> bytes:
        assert "/futures/um/" in url
        return f"{digest}  {archive.filename}".encode() if url.endswith(".CHECKSUM") else payload

    monkeypatch.setattr(market_data, "_download", fetch)
    path = tmp_path / "futures.jsonl"
    report = download_bars(start=start, end=end, output=path, market="um")
    assert report["source"] == market_data.FUTURES_SOURCE
    assert read_bars(path) == [candle(archive.start)]
    assert (tmp_path / "raw" / market_data.FUTURES_SOURCE / archive.filename).exists()
