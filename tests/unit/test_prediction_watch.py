import csv
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from apps.run_prediction_watch import (
    ObservationStore,
    load_settings,
    parse_klines,
    writer_lock,
)
from bot.features.bars import Bar

START = datetime(2026, 9, 11, 23, 6, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def settings(tmp_path: Path) -> dict:
    value = load_settings(Path("configs/arb_prediction_20260912.yaml"))
    value["output_dir"] = str(tmp_path)
    return value


def bar(index: int) -> Bar:
    start = START - 60 * MINUTE + index * MINUTE
    price = Decimal("0.14") + Decimal(index) / 1_000_000
    return Bar("ARB_USDT", 60, start, start + MINUTE, price,
               price + Decimal("0.00001"), price - Decimal("0.00001"),
               price, Decimal(100), 0)


def raw_rows(bars: list[Bar]) -> bytes:
    return json.dumps({"code": "200000", "data": [
        [int(b.start.timestamp() * 1000), *[str(x) for x in (
            b.open, b.high, b.low, b.close, b.volume)], "14"] for b in bars
    ]}).encode()


def test_exact_tehran_window(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path, settings(tmp_path))
    try:
        assert store.start == START
        assert store.end == START + timedelta(days=1)
        assert store.warmup_start == START - timedelta(hours=1)
    finally:
        store.db.close()


def test_closed_bar_buffer_and_sorting() -> None:
    raw = raw_rows([bar(61), bar(60), bar(59)])
    assert parse_klines(raw, START + timedelta(seconds=61)) == [bar(59)]
    assert parse_klines(raw, START + timedelta(seconds=62)) == [bar(59), bar(60)]


@pytest.mark.parametrize("change", ["duplicate", "unaligned", "bad_ohlc", "nan", "error"])
def test_invalid_feed_rejected(change: str) -> None:
    payload = json.loads(raw_rows([bar(59)]))
    row = payload["data"][0]
    if change == "duplicate":
        payload["data"].append(row)
    elif change == "unaligned":
        row[0] += 1
    elif change == "bad_ohlc":
        row[2] = "0.0001"
    elif change == "nan":
        row[4] = "NaN"
    else:
        payload["code"] = "500000"
    with pytest.raises(ValueError):
        parse_klines(json.dumps(payload).encode(), START + MINUTE)


def test_backfill_does_not_claim_forward_forecast_or_use_future(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path, settings(tmp_path))
    try:
        store.ingest([bar(i) for i in range(66)], START + 6 * MINUTE)
        first = deepcopy(store.predictions[0])
        assert first["generated_at"].startswith("2026-09-11 23:06:00")
        assert first["observation_kind"] == "reconstructed"
        assert first["probability"] is None
        assert first["direction"] == "bullish"
        assert first["full_strategy_action"] == "HOLD"
        report = store.report(START + 6 * MINUTE, "observing")
        assert report["evaluation"]["reconstructed"]["scored_directional_signals"] >= 1
        store.ingest([replace(bar(66), close=bar(66).low)], START + 7 * MINUTE)
        assert store.predictions[0] == first
    finally:
        store.db.close()


def test_restart_preserves_receipt_times_and_deduplicates(tmp_path: Path) -> None:
    config = settings(tmp_path)
    store = ObservationStore(tmp_path, config)
    bars = [bar(i) for i in range(60)]
    store.ingest(bars, START + timedelta(seconds=3))
    original = deepcopy(store.predictions)
    store.db.close()
    resumed = ObservationStore(tmp_path, config)
    try:
        assert resumed.ingest(bars, START + 10 * MINUTE) == 0
        assert resumed.predictions == original
        assert resumed.predictions[0]["observation_kind"] == "forward_observation"
        resumed.ingest([bar(60)], START + MINUTE + timedelta(seconds=3))
        assert len(resumed.predictions) == 2
        report = resumed.report(START + 3 * MINUTE, "feed_error", "timeout")
        assert report["current_prediction"] is None
        assert report["latest_prediction_fresh"] is False
        assert report["missing_elapsed_window_bars"] == 2
    finally:
        resumed.db.close()


def test_code_config_pin_blocks_mixed_runs(tmp_path: Path) -> None:
    config = settings(tmp_path)
    ObservationStore(tmp_path, config).db.close()
    config["engine"]["horizon_seconds"] = 600
    with pytest.raises(ValueError, match="configuration/code changed"):
        ObservationStore(tmp_path, config)


def test_revision_and_late_gap_repair_do_not_change_recorded_signal(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path, settings(tmp_path))
    try:
        store.ingest([bar(i) for i in range(60)], START)
        original = deepcopy(store.predictions)
        with pytest.raises(ValueError, match="revised"):
            store.ingest([replace(bar(59), volume=Decimal(9)), bar(60)], START + MINUTE)
        assert store.predictions == original
        assert len(store.bars) == 60
        store.ingest([bar(61)], START + 2 * MINUTE)
        assert "gap_warmup" in store.predictions[-1]["reasons"]
        with pytest.raises(ValueError, match="late gap repair"):
            store.ingest([bar(60)], START + 3 * MINUTE)
    finally:
        store.db.close()


def test_no_future_ingestion_or_endpoint_extrapolation(tmp_path: Path) -> None:
    config = settings(tmp_path)
    config["end"] = (START + 6 * MINUTE).isoformat()
    store = ObservationStore(tmp_path, config)
    try:
        with pytest.raises(ValueError, match="future"):
            store.ingest([bar(59)], START - timedelta(seconds=1))
        store.ingest([bar(i) for i in range(68)], START + 8 * MINUTE)
        assert store.bars[-1].end == store.end
        assert len(store.predictions) == 6
        report = store.report(START + 8 * MINUTE, "finished")
        assert report["missing_elapsed_window_bars"] == 0
        assert report["current_prediction"] is None
        with (tmp_path / "predictions.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["realized_move_pct"]
        assert rows[-1]["realized_move_pct"] == ""
        store.retrospective_backtest()
        replay = json.loads((tmp_path / "retrospective_backtest.json").read_text())
        assert replay["mode"] == "retrospective_pillar_two_only_candle_simulation"
        assert set(replay["results"]) == {"base", "doubled_costs"}
    finally:
        store.db.close()


def test_single_writer_lock_releases(tmp_path: Path) -> None:
    with writer_lock(tmp_path), pytest.raises(OSError), writer_lock(tmp_path):
        pass
    with writer_lock(tmp_path):
        pass
