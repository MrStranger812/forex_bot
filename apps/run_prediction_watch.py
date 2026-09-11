"""Bounded ARB public-candle observation with durable, causal Pillar Two signals.

This research process has no order client. KuCoin candles are a reference venue,
not Ourbit quotes. First-receipt times distinguish backfill from forward observation.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import yaml

from bot.domain.events import ensure_utc
from bot.features.bars import Bar
from bot.strategy.time_based import TimeBasedPillarTwoConfig, TimeBasedPillarTwoEngine
from research.pillar_two_backtest import CandleBacktestConfig, CandleCosts, run_backtest

BASE = "https://api-futures.kucoin.com"
MINUTE = timedelta(minutes=1)
SYMBOL = "ARB_USDT"
SOURCE_SYMBOL = "ARBUSDTM"


def encode(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True, allow_nan=False)


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, default=str, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def load_settings(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_bytes())
    if not isinstance(raw, dict) or set(raw) != {
        "symbol", "source", "source_symbol", "timezone", "start", "end", "output_dir",
        "poll_seconds", "engine", "costs", "backtest",
    }:
        raise ValueError("invalid prediction-watch configuration keys")
    if (raw["symbol"], raw["source"], raw["source_symbol"]) != (
        SYMBOL, "kucoin_futures", SOURCE_SYMBOL,
    ):
        raise ValueError("this observer supports only the KuCoin ARBUSDTM reference market")
    start, end = (ensure_utc(datetime.fromisoformat(raw[key])) for key in ("start", "end"))
    if not timedelta(0) < end - start <= timedelta(days=2):
        raise ValueError("window must be positive and at most two days")
    if any(t.second or t.microsecond for t in (start, end)):
        raise ValueError("window must align to complete minutes")
    engine = TimeBasedPillarTwoConfig(**raw["engine"])
    if engine.interval_seconds != 60 or engine.horizon_seconds % 60:
        raise ValueError("observer requires minute bars and a whole-minute horizon")
    if type(raw["poll_seconds"]) is not int or not 5 <= raw["poll_seconds"] <= 60:
        raise ValueError("poll_seconds must be an integer from 5 to 60")
    ZoneInfo(raw["timezone"])
    CandleCosts(**raw["costs"])
    CandleBacktestConfig(**raw["backtest"])
    return raw


@contextmanager
def writer_lock(directory: Path) -> Iterator[None]:
    """OS lock releases on crash; an old lock file does not prevent restart."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "writer.lock").open("a+b") as handle:
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def parse_klines(payload: bytes, available_at: datetime) -> list[Bar]:
    """KuCoin futures rows: millisecond open time, O/H/L/C, contracts, turnover.

    Skip the open candle, keeping a two-second publication buffer. Missing minutes
    remain missing; the shared engine resets its warmup across gaps.
    """
    data = json.loads(payload, parse_float=Decimal)
    if not isinstance(data, dict) or data.get("code") != "200000":
        raise ValueError("unsuccessful KuCoin candle response")
    if not isinstance(data.get("data"), list):
        raise ValueError("candle data must be a list")
    result: dict[datetime, Bar] = {}
    validator = TimeBasedPillarTwoEngine(SYMBOL)
    for row in data["data"]:
        if not isinstance(row, list) or len(row) != 7:
            raise ValueError("unexpected KuCoin futures candle schema")
        if type(row[0]) is not int or row[0] % 60_000:
            raise ValueError("unaligned millisecond candle timestamp")
        start = datetime.fromtimestamp(row[0] // 1000, UTC)
        if start + MINUTE > ensure_utc(available_at) - timedelta(seconds=2):
            continue
        prices = [Decimal(str(value)) for value in row[1:6]]
        bar = Bar(SYMBOL, 60, start, start + MINUTE,
                  prices[0], prices[1], prices[2], prices[3], prices[4], 0)
        # Trade count is unavailable (zero placeholder); volume is contracts.
        if start in result:
            raise ValueError("duplicate candle in one response")
        result[start] = bar
    bars = sorted(result.values(), key=lambda bar: bar.start)
    for bar in bars:
        validator.on_bar(bar)
    return bars


def restore_bar(record: dict[str, Any]) -> Bar:
    return Bar(
        symbol=record["symbol"], interval_seconds=record["interval_seconds"],
        start=datetime.fromisoformat(record["start"]), end=datetime.fromisoformat(record["end"]),
        **{key: Decimal(record[key]) for key in ("open", "high", "low", "close", "volume")},
        trades=record["trades"],
    )


class ObservationStore:
    def __init__(self, directory: Path, settings: dict[str, Any]) -> None:
        self.directory, self.settings = directory, settings
        self.start = ensure_utc(datetime.fromisoformat(settings["start"]))
        self.end = ensure_utc(datetime.fromisoformat(settings["end"]))
        self.engine_config = TimeBasedPillarTwoConfig(**settings["engine"])
        self.warmup_start = self.start - MINUTE * self.engine_config.warmup_bars
        self.engine = TimeBasedPillarTwoEngine(SYMBOL, self.engine_config)
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "observations.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY, requested_at TEXT, received_at TEXT, path TEXT,
                parameters TEXT, http_status INTEGER, sha256 TEXT, payload BLOB);
            CREATE TABLE IF NOT EXISTS observations (
                end TEXT PRIMARY KEY, bar TEXT NOT NULL, recorded_at TEXT NOT NULL,
                prediction TEXT);
        """)
        root = Path(__file__).resolve().parents[1]
        files = [Path(__file__), root / "bot/strategy/time_based.py",
                 root / "bot/domain/events.py", root / "research/pillar_two_backtest.py"]
        identity = encode({"settings": settings, "source_sha256": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files
        }})
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('identity',?)", (identity,))
        saved = self.db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()[0]
        if saved != identity:
            self.db.close()
            raise ValueError("configuration/code changed; use a new run directory")
        self.bars: list[Bar] = []
        self.predictions: list[dict[str, Any]] = []
        for raw_bar, prediction in self.db.execute(
            "SELECT bar,prediction FROM observations ORDER BY end"
        ):
            bar = restore_bar(json.loads(raw_bar))
            self.engine.on_bar(bar)
            self.bars.append(bar)
            if prediction:
                self.predictions.append(json.loads(prediction))

    def ingest(self, bars: list[Bar], recorded_at: datetime) -> int:
        recorded_at = ensure_utc(recorded_at)
        existing = {bar.end: bar for bar in self.bars}
        accepted = [bar for bar in bars if self.warmup_start <= bar.start and bar.end <= self.end]
        # Validate the complete batch before persisting or changing in-memory state.
        for bar in accepted:
            if bar.end > recorded_at:
                raise ValueError("cannot ingest a future bar")
            if bar.end in existing and existing[bar.end] != bar:
                raise ValueError("closed candle revised; original observations retained")
            if self.bars and bar.end < self.bars[-1].end and bar.end not in existing:
                raise ValueError("late gap repair requires a separate replay")
        new = [bar for bar in accepted if bar.end not in existing]
        if any(right.start < left.end for left, right in zip(new, new[1:], strict=False)):
            raise ValueError("new bars must be unique and chronological")
        for bar in new:
            self.engine.on_bar(bar)
            prediction = None
            if self.start <= bar.end < self.end:
                signal = self.engine.signal(bar.end)
                prediction = {
                    **asdict(signal), "bar_close": str(bar.close),
                    "recorded_at": recorded_at.isoformat(),
                    "observation_kind": (
                        "forward_observation" if recorded_at <= signal.valid_until
                        and recorded_at < self.end else "reconstructed"
                    ),
                    "target_at": (bar.end + timedelta(
                        seconds=self.engine_config.horizon_seconds)).isoformat(),
                    "source": "kucoin_futures", "source_symbol": SOURCE_SYMBOL,
                    "full_strategy_action": "HOLD", "pillar_one": "unavailable",
                }
                prediction = json.loads(encode(prediction))
            with self.db:
                self.db.execute("INSERT INTO observations VALUES (?,?,?,?)", (
                    bar.end.isoformat(), encode(asdict(bar)), recorded_at.isoformat(),
                    encode(prediction) if prediction else None,
                ))
            self.bars.append(bar)
            if prediction:
                self.predictions.append(prediction)
        return len(new)

    def report(self, now: datetime, state: str, error: str | None = None) -> dict[str, Any]:
        closes = {bar.end.isoformat(): bar.close for bar in self.bars}
        outcomes = []
        for prediction in self.predictions:
            target = closes.get(prediction["target_at"])
            movement = ((target / Decimal(prediction["bar_close"]) - 1) * 100
                        if target is not None else None)
            side = {"bullish": 1, "bearish": -1, "neutral": 0}[prediction["direction"]]
            outcomes.append({
                **prediction, "realized_move_pct": movement,
                "direction_correct": (side * movement > 0
                                      if side and movement is not None else None),
            })
        groups = {}
        for kind in ("forward_observation", "reconstructed"):
            rows = [row for row in outcomes if row["observation_kind"] == kind]
            scored = [row for row in rows if row["direction_correct"] is not None]
            groups[kind] = {
                "signals": len(rows), "directions": dict(Counter(row["direction"] for row in rows)),
                "scored_directional_signals": len(scored),
                "directional_accuracy_pct": (
                    100 * sum(row["direction_correct"] for row in scored) / len(scored)
                    if scored else None
                ),
            }
        elapsed_end = min(self.end, now.replace(second=0, microsecond=0))
        expected = max(0, int((elapsed_end - self.start).total_seconds() // 60))
        observed = sum(self.start < bar.end <= elapsed_end for bar in self.bars)
        latest = self.predictions[-1] if self.predictions else None
        fresh = latest is not None and now <= datetime.fromisoformat(latest["valid_until"])
        report = {
            "state": state, "heartbeat_at": now.isoformat(), "pid": os.getpid(),
            "window_start": self.start.isoformat(), "window_end": self.end.isoformat(),
            "timezone": self.settings["timezone"], "source": "kucoin_futures",
            "source_symbol": SOURCE_SYMBOL, "symbol": SYMBOL, "live_orders_enabled": False,
            "full_strategy_action": "HOLD", "pillar_one": "unavailable", "last_error": error,
            "latest_prediction": latest, "latest_prediction_fresh": fresh,
            "current_prediction": latest if fresh and error is None and now < self.end else None,
            "first_prediction": self.predictions[0] if self.predictions else None,
            "last_closed_bar": self.bars[-1].end.isoformat() if self.bars else None,
            "closed_bars_including_warmup": len(self.bars),
            "expected_elapsed_window_bars": expected, "observed_elapsed_window_bars": observed,
            "missing_elapsed_window_bars": max(0, expected - observed),
            "evaluation": groups,
            "limitations": [
                "KuCoin reference candles are not Ourbit executable quotes.",
                "Five-minute directional hypotheses; no calibrated probability or next-day target.",
                "Reconstructed signals were not emitted at their historical event times.",
                "Directional accuracy uses close-to-close moves, not fills or net trading returns.",
                "Signals overlap; neutral signals are abstentions, not scored wins.",
                "Targets beyond the window or missing target candles remain unscored.",
                "Volume is contracts; trade count is unavailable. No order-book or news input.",
            ],
        }
        write_json(self.directory / "status.json", report)
        if outcomes:
            temporary = self.directory / "predictions.csv.tmp"
            with temporary.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(outcomes[0]))
                writer.writeheader()
                writer.writerows(outcomes)
            (self.directory / "predictions.csv.tmp").replace(self.directory / "predictions.csv")
        return report

    def retrospective_backtest(self) -> None:
        if not self.bars or self.bars[-1].end <= self.start:
            return
        results = {}
        for name, multiplier in (("base", Decimal(1)), ("doubled_costs", Decimal(2))):
            result = run_backtest(
                self.bars, symbol=SYMBOL, engine_config=self.engine_config,
                costs=CandleCosts(**self.settings["costs"]).stressed(multiplier),
                config=CandleBacktestConfig(**self.settings["backtest"]),
                start=self.start, end=min(self.end, self.bars[-1].end),
            )
            results[name] = asdict(result)
        write_json(self.directory / "retrospective_backtest.json", {
            "mode": "retrospective_pillar_two_only_candle_simulation",
            "source": "kucoin_futures", "settings": self.settings, "results": results,
            "limitations": ["Includes reconstructed bars; not forward paper fills.",
                            "Assumed fees/spread/slippage; funding and latency omitted.",
                            "The combined strategy abstains without Pillar One."],
        })


async def fetch(
    session: aiohttp.ClientSession, store: ObservationStore, path: str,
    params: dict[str, str],
) -> tuple[bytes, datetime]:
    if path not in {"/api/v1/kline/query", "/api/v1/contracts/ARBUSDTM"}:
        raise ValueError("only the two public read-only resources are allowed")
    requested = datetime.now(UTC)
    async with session.get(BASE + path, params=params, allow_redirects=False) as response:
        chunks = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            chunks.extend(chunk)
            if len(chunks) > 2 * 1024 * 1024:
                raise ValueError("public response exceeds size bound")
        payload = bytes(chunks)
        received = datetime.now(UTC)
        with store.db:
            store.db.execute("INSERT INTO responses VALUES (NULL,?,?,?,?,?,?,?)", (
                requested.isoformat(), received.isoformat(), path, encode(params), response.status,
                hashlib.sha256(payload).hexdigest(), payload,
            ))
        response.raise_for_status()
        return payload, received


async def collect(session: aiohttp.ClientSession, store: ObservationStore) -> int:
    cursor = store.bars[-1].start if store.bars else store.warmup_start
    count = 0
    # Requests cover at most 199 minutes; the endpoint returns at most 200 rows.
    # Pagination bounds both bootstrap and reconnect catch-up without dropping old bars.
    for _ in range(20):
        now = datetime.now(UTC)
        stop = min(store.end, now.replace(second=0, microsecond=0))
        if cursor >= stop:
            break
        page_end = min(stop, cursor + 199 * MINUTE)
        params = {"symbol": SOURCE_SYMBOL, "granularity": "1",
                  "from": str(int(cursor.timestamp() * 1000)),
                  "to": str(int(page_end.timestamp() * 1000) - 1)}
        payload, received = await fetch(session, store, "/api/v1/kline/query", params)
        bars = parse_klines(payload, received)
        if any(bar.start < cursor or bar.start >= page_end for bar in bars):
            raise ValueError("source returned candles outside the requested range")
        count += store.ingest(bars, received)
        if page_end >= stop:
            break
        cursor = page_end
        await asyncio.sleep(0.2)
    return count


async def run(settings: dict[str, Any], *, once: bool = False, offline: bool = False) -> None:
    directory = Path(settings["output_dir"])
    with writer_lock(directory):
        store = ObservationStore(directory, settings)
        try:
            if offline:
                store.retrospective_backtest()
                print(encode({"report": str(directory / "retrospective_backtest.json")}))
                return
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                store.report(datetime.now(UTC), "starting")
                raw, _ = await fetch(session, store, "/api/v1/contracts/ARBUSDTM", {})
                document = json.loads(raw)
                contract = document.get("data", {})
                if document.get("code") != "200000" or any(contract.get(k) != v for k, v in {
                    "symbol": SOURCE_SYMBOL, "baseCurrency": "ARB", "quoteCurrency": "USDT",
                    "settleCurrency": "USDT", "status": "Open",
                }.items()):
                    raise ValueError("ARB USDT perpetual metadata verification failed")
                failures = 0
                while True:
                    now = datetime.now(UTC)
                    if (directory / "STOP").exists():
                        store.report(now, "stopped")
                        break
                    error = None
                    try:
                        added = await collect(session, store)
                        failures = 0
                    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                        added, failures = 0, failures + 1
                        error = f"{type(exc).__name__}: {exc}"
                        if isinstance(exc, aiohttp.ClientResponseError) and exc.status in {
                            401, 403, 451,
                        }:
                            store.report(datetime.now(UTC), "access_blocked", error)
                            raise
                    now = datetime.now(UTC)
                    complete = now >= store.end + timedelta(seconds=2) and bool(store.bars) and (
                        store.bars[-1].end == store.end
                    )
                    deadline = now >= store.end + timedelta(seconds=90)
                    state = ("finished" if complete else "finished_incomplete" if deadline else
                             "feed_error" if error else "observing" if now >= store.start else
                             "waiting_for_start")
                    if once and not complete and not deadline:
                        state = "snapshot_finished"
                    report = store.report(now, state, error)
                    print(encode({"at": now, "state": state, "new_bars": added,
                                  "latest": report["current_prediction"], "error": error}),
                          flush=True)
                    if complete or deadline or once:
                        store.retrospective_backtest()
                        break
                    delay = min(60, settings["poll_seconds"] * 2 ** min(failures, 3))
                    await asyncio.sleep(delay)
        except BaseException as exc:
            store.report(datetime.now(UTC), "failed", f"{type(exc).__name__}: {exc}")
            raise
        finally:
            store.db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/arb_prediction_20260912.yaml"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="collect one pass and exit")
    mode.add_argument("--offline-report", action="store_true",
                      help="replay stored bars without HTTP")
    args = parser.parse_args()
    asyncio.run(run(load_settings(args.config), once=args.once, offline=args.offline_report))


if __name__ == "__main__":
    main()
