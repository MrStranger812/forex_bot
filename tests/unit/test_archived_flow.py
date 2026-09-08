import hashlib
import io
import json
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bot.features.bars import Bar
from bot.strategy.time_based import TimeBasedPillarTwoConfig
from research.archived_flow import parse_taker_volume, read_archived_flow
from research.market_data import provenance_path
from research.pillar_two_experiments import Candidate, build_signal_cache
from research.trade_outcomes import FEATURE_NAMES, opportunity_features

START = datetime(2026, 5, 1, tzinfo=UTC)
NAME = "BTCUSDT-1m-2026-05.zip"
BAR = Bar(
    "BTC_USDT",
    60,
    START,
    START + timedelta(minutes=1),
    Decimal(100),
    Decimal(102),
    Decimal(99),
    Decimal(101),
    Decimal(10),
    20,
)


def payload(buy: str = "7", *, count: int = 1) -> bytes:
    opening = int(START.timestamp()) * 1000000
    row = f"{opening},100,102,99,101,10,{opening + 59999999},1000,20,{buy},700,0\n"
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(NAME.removesuffix(".zip") + ".csv", row * count)
    return data.getvalue()


def test_recorded_flow_is_bound_to_matching_candle_and_close_time() -> None:
    values = parse_taker_volume(payload(), NAME, "us", {BAR.start: BAR})
    assert values == {BAR.end: Decimal(7)}
    with pytest.raises(ValueError, match="OHLCV"):
        parse_taker_volume(payload(), NAME, "us", {BAR.start: replace(BAR, close=Decimal(100))})
    with pytest.raises(ValueError, match="duplicate"):
        parse_taker_volume(payload(count=2), NAME, "us", {BAR.start: BAR})


@pytest.mark.parametrize("buy", ["-1", "11", "NaN", "Infinity", "bad"])
def test_impossible_taker_volumes_are_rejected(buy: str) -> None:
    with pytest.raises(ValueError):
        parse_taker_volume(payload(buy), NAME, "us", {BAR.start: BAR})


def test_original_archive_checksum_is_reverified(tmp_path: Path) -> None:
    archive = tmp_path / NAME
    archive.write_bytes(payload())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = Path(str(archive) + ".CHECKSUM")
    checksum.write_text(f"{digest}  {NAME}\n", encoding="utf-8")
    normalized = tmp_path / "bars.jsonl"
    provenance_path(normalized).write_text(
        json.dumps(
            {
                "source": "binance_spot_klines",
                "archives": [
                    {
                        "raw_path": str(archive),
                        "sha256": digest,
                        "url": "https://data.binance.vision/example",
                        "timestamp_unit": "us",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    flow, provenance = read_archived_flow(normalized, [BAR])
    assert flow[BAR.end] == 7
    assert provenance["bars"] == 1
    archive.write_bytes(payload("8"))
    with pytest.raises(ValueError, match="SHA256"):
        read_archived_flow(normalized, [BAR])


def test_flow_features_use_measured_past_volume_and_mark_availability() -> None:
    data = [
        replace(
            BAR,
            start=START + timedelta(minutes=i),
            end=START + timedelta(minutes=i + 1),
            open=Decimal(10000 + i),
            high=Decimal(10002 + i),
            low=Decimal(9998 + i),
            close=Decimal(10001 + i),
        )
        for i in range(100)
    ]
    cache = build_signal_cache(data, TimeBasedPillarTwoConfig())
    candidate = Candidate("flow_test", 3600, Decimal(80), Decimal(120), 0.7, ("trend",))
    measured = {bar.end: Decimal(7) for bar in data}
    full = opportunity_features(data, cache, candidate, 300, measured)
    without = opportunity_features(data, cache, candidate, 300)
    assert full
    for row in full:
        for name in ("aligned_taker_flow_1", "aligned_taker_flow_5", "aligned_taker_flow_15"):
            assert row.features[FEATURE_NAMES.index(name)] == pytest.approx(0.4)
        assert row.features[FEATURE_NAMES.index("flow_available")] == 1
    assert without[0].features[FEATURE_NAMES.index("flow_available")] == 0
    mutated_future = {
        at: Decimal(1) if at > data[84].end else amount for at, amount in measured.items()
    }
    changed = opportunity_features(data, cache, candidate, 300, mutated_future)
    assert [row for row in full if row.decision_at <= data[84].end] == [
        row for row in changed if row.decision_at <= data[84].end
    ]
