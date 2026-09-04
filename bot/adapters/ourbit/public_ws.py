from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import websockets

from bot.domain.events import BookDeltaEvent, MarketEvent


class SequenceGap(RuntimeError):
    pass


@dataclass(slots=True)
class SequencedOrderBook:
    symbol: str
    bids: dict[Decimal, Decimal]
    asks: dict[Decimal, Decimal]
    sequence: int | None = None
    valid: bool = False

    @classmethod
    def empty(cls, symbol: str) -> SequencedOrderBook:
        return cls(symbol=symbol, bids={}, asks={})

    def load_snapshot(
        self,
        sequence: int,
        bids: Iterable[tuple[Decimal, Decimal]],
        asks: Iterable[tuple[Decimal, Decimal]],
    ) -> None:
        self.bids = {price: quantity for price, quantity in bids if quantity > 0}
        self.asks = {price: quantity for price, quantity in asks if quantity > 0}
        self.sequence = sequence
        self.valid = True

    def apply(self, delta: BookDeltaEvent) -> None:
        if delta.symbol != self.symbol:
            raise ValueError("book symbol mismatch")
        if not self.valid or self.sequence is None:
            raise SequenceGap("book has no valid snapshot")
        contiguous = delta.sequence == self.sequence + 1 and (
            delta.previous_sequence is None or delta.previous_sequence == self.sequence
        )
        if not contiguous or delta.sequence is None or delta.sequence <= self.sequence:
            self.valid = False
            raise SequenceGap(
                f"expected after {self.sequence}, got previous={delta.previous_sequence} "
                f"sequence={delta.sequence}"
            )
        for side, levels in ((self.bids, delta.bids), (self.asks, delta.asks)):
            for price, quantity in levels:
                if quantity == 0:
                    side.pop(price, None)
                else:
                    side[price] = quantity
        self.sequence = delta.sequence

    @property
    def best_bid(self) -> tuple[Decimal, Decimal] | None:
        if not self.valid or not self.bids:
            return None
        price = max(self.bids)
        return price, self.bids[price]

    @property
    def best_ask(self) -> tuple[Decimal, Decimal] | None:
        if not self.valid or not self.asks:
            return None
        price = min(self.asks)
        return price, self.asks[price]


class EventFreshnessFilter:
    def __init__(self, stale_after: timedelta) -> None:
        self.stale_after = stale_after
        self._last_exchange_ts: dict[tuple[str, type[MarketEvent]], datetime] = {}

    def accept(self, event: MarketEvent) -> tuple[bool, str]:
        if event.received_ts - event.exchange_ts > self.stale_after:
            return False, "stale"
        key = (event.symbol, type(event))
        previous = self._last_exchange_ts.get(key)
        if previous is not None and event.exchange_ts <= previous:
            return False, "out_of_order"
        self._last_exchange_ts[key] = event.exchange_ts
        return True, "ok"


class DepthSynchronizer:
    """Buffers deltas around a REST snapshot and invalidates on any gap."""

    def __init__(self, symbol: str, max_buffered_deltas: int = 10_000) -> None:
        self.book = SequencedOrderBook.empty(symbol)
        self.max_buffered_deltas = max_buffered_deltas
        self._buffer: list[BookDeltaEvent] = []

    def on_delta(self, delta: BookDeltaEvent) -> bool:
        if not self.book.valid:
            self._buffer.append(delta)
            if len(self._buffer) > self.max_buffered_deltas:
                self._buffer.clear()
                raise SequenceGap("depth delta buffer overflow")
            return False
        try:
            self.book.apply(delta)
            return True
        except SequenceGap:
            self._buffer = [delta]
            raise

    def apply_snapshot(
        self,
        sequence: int,
        bids: Iterable[tuple[Decimal, Decimal]],
        asks: Iterable[tuple[Decimal, Decimal]],
    ) -> None:
        self.book.load_snapshot(sequence, bids, asks)
        pending = sorted(
            (delta for delta in self._buffer if delta.sequence and delta.sequence > sequence),
            key=lambda delta: delta.sequence or 0,
        )
        self._buffer.clear()
        for delta in pending:
            self.book.apply(delta)


RawHandler = Callable[[dict[str, Any], datetime], Awaitable[None]]


class OurbitPublicWebSocket:
    """Reconnectable raw transport; payload normalization remains in mapper.py."""

    def __init__(
        self,
        url: str,
        topics: list[str],
        handler: RawHandler,
        *,
        heartbeat_seconds: float = 20.0,
        reconnect_before: timedelta = timedelta(hours=23, minutes=30),
    ) -> None:
        if not topics or any(not topic for topic in topics):
            raise ValueError("verified non-empty topics are required")
        if len(topics) > 30:
            raise ValueError("Ourbit documents at most 30 subscriptions per connection")
        self.url = url
        self.topics = topics
        self.handler = handler
        self.heartbeat_seconds = heartbeat_seconds
        self.reconnect_before = reconnect_before
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        backoff = 1.0
        while not self._stopping.is_set():
            try:
                await self._connection_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._stopping.is_set():
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _connection_once(self) -> None:
        opened = datetime.now(UTC)
        async with websockets.connect(
            self.url, ping_interval=None, close_timeout=3, max_queue=10_000
        ) as socket:
            for request_id, topic in enumerate(self.topics):
                await socket.send(
                    json.dumps({"method": "SUBSCRIPTION", "params": [topic], "id": request_id})
                )
            while not self._stopping.is_set():
                if datetime.now(UTC) - opened >= self.reconnect_before:
                    return
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=self.heartbeat_seconds)
                except TimeoutError:
                    await socket.send("PING")
                    continue
                received_at = datetime.now(UTC)
                if raw == "PING":
                    await socket.send("PONG")
                    continue
                if raw == "PONG":
                    continue
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    continue
                await self.handler(payload, received_at)
