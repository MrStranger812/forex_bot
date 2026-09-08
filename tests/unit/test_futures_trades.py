from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from bot.features.bars import Bar
from research.futures_trades import HEADER, compare_candles, iter_trades, summarize_trades

DAY = date(2026, 7, 20)
START = 1784505600000


def payload(rows: list[str], name: str = "BTCUSDT-trades-2026-07-20.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, ",".join(HEADER) + "\n" + "\n".join(rows) + "\n")
    return buffer.getvalue()


def test_measured_flow_and_prefix_stability_and_candle_comparison() -> None:
    rows = [f"1,100,2,200,{START},false", f"2,101,1,101,{START+1000},true"]
    first, quality = summarize_trades(payload(rows), DAY)
    extended, _ = summarize_trades(payload(rows + [f"3,99,1,99,{START+5000},false"]), DAY)
    assert first[0].record() == extended[0].record()
    assert first[0].volume == 3 and first[0].taker_buy_volume == 2
    assert quality["trade_count"] == 2 and quality["skipped_trade_id_values"] == 0
    start = datetime(2026, 7, 20, tzinfo=UTC)
    candle = Bar("BTC_USDT", 60, start, start+timedelta(minutes=1), Decimal(100),
                 Decimal(101), Decimal(99), Decimal(99), Decimal(4), 3)
    assert compare_candles(extended, [candle], {candle.end: Decimal(3)})["passed"]
    assert not compare_candles(extended, [candle], {candle.end: Decimal(2)})["passed"]
    # Price/volume agreement alone cannot excuse a missing or duplicate trade.
    assert not compare_candles(
        extended, [replace(candle, trades=4)], {candle.end: Decimal(3)}
    )["passed"]


@pytest.mark.parametrize("row", [
    f"1,NaN,2,200,{START},false", f"1,100,-2,200,{START},false",
    f"1,100,2,201,{START},false", f"1,100,2,200,{START},unknown",
    f"1,100,2,200,{START*1000},false", f"1,100,2,200,{START-1},false",
])
def test_bad_trade_values_rejected(row: str) -> None:
    with pytest.raises(ValueError):
        list(iter_trades(payload([row]), DAY))


def test_duplicate_and_reversed_trades_rejected_but_gaps_reported() -> None:
    first = f"1,100,2,200,{START+1},false"
    for second in (first, f"2,100,2,200,{START},false"):
        with pytest.raises(ValueError, match="out-of-order"):
            list(iter_trades(payload([first, second]), DAY))
    _, quality = summarize_trades(payload([first, f"3,100,2,200,{START+2},false"]), DAY)
    assert quality["skipped_trade_id_values"] == 1
    assert quality["consecutive_trade_ids"] is False


def test_unexpected_member_and_empty_archive_rejected() -> None:
    with pytest.raises(ValueError, match="bounded CSV"):
        list(iter_trades(payload([], name="../escape.csv"), DAY))
    with pytest.raises(ValueError):
        list(iter_trades(payload([]), DAY))
