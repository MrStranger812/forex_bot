from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import websockets

PrivateHandler = Callable[[dict[str, Any], datetime], Awaitable[None]]
ListenKeyProvider = Callable[[], Awaitable[str]]
ListenKeyKeeper = Callable[[str], Awaitable[None]]


class PrivateEventKind(StrEnum):
    ORDER_ACCEPTED = "order_accepted"
    ORDER_REJECTED = "order_rejected"
    PARTIAL_FILL = "partial_fill"
    FULL_FILL = "full_fill"
    CANCELLATION = "cancellation"
    POSITION_UPDATE = "position_update"
    BALANCE_UPDATE = "balance_update"
    MARGIN_UPDATE = "margin_update"
    LIQUIDATION_WARNING = "liquidation_warning"


@dataclass(frozen=True, slots=True)
class PrivateAccountEvent:
    kind: PrivateEventKind
    exchange_ts: datetime
    received_ts: datetime
    sequence: int | None
    payload: dict[str, Any]


class PrivateSequenceGap(RuntimeError):
    pass


class PrivateSequenceGuard:
    def __init__(self) -> None:
        self.last_sequence: int | None = None

    def accept(self, event: PrivateAccountEvent) -> bool:
        if event.sequence is None:
            return True
        if self.last_sequence is not None and event.sequence != self.last_sequence + 1:
            raise PrivateSequenceGap(
                f"expected private sequence {self.last_sequence + 1}, got {event.sequence}"
            )
        self.last_sequence = event.sequence
        return True


def normalize_private_event(payload: dict[str, Any], received_at: datetime) -> PrivateAccountEvent:
    """Validate an adapter-normalized payload after exchange-specific mapping."""
    try:
        kind = PrivateEventKind(str(payload["event_type"]).lower())
        exchange_ts = datetime.fromtimestamp(int(payload["timestamp"]) / 1000, tz=UTC)
        sequence = int(payload["sequence"]) if payload.get("sequence") is not None else None
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ValueError("invalid normalized private event") from exc
    return PrivateAccountEvent(kind, exchange_ts, received_at, sequence, payload)


class OurbitPrivateWebSocket:
    """Private event transport with listen-key renewal and planned reconnect."""

    def __init__(
        self,
        ws_base_url: str,
        get_listen_key: ListenKeyProvider,
        keepalive: ListenKeyKeeper,
        handler: PrivateHandler,
        *,
        keepalive_every: timedelta = timedelta(minutes=30),
        reconnect_before: timedelta = timedelta(hours=23, minutes=30),
    ) -> None:
        self.ws_base_url = ws_base_url
        self.get_listen_key = get_listen_key
        self.keepalive = keepalive
        self.handler = handler
        self.keepalive_every = keepalive_every
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
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _connection_once(self) -> None:
        listen_key = await self.get_listen_key()
        opened = datetime.now(UTC)
        renewed = opened
        url = f"{self.ws_base_url}?listenKey={listen_key}"
        async with websockets.connect(url, ping_interval=None, max_queue=10_000) as socket:
            while not self._stopping.is_set():
                now = datetime.now(UTC)
                if now - opened >= self.reconnect_before:
                    return
                if now - renewed >= self.keepalive_every:
                    await self.keepalive(listen_key)
                    renewed = now
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=20)
                except TimeoutError:
                    await socket.send("PING")
                    continue
                received_at = datetime.now(UTC)
                if raw == "PING":
                    await socket.send("PONG")
                elif raw != "PONG":
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        await self.handler(payload, received_at)
