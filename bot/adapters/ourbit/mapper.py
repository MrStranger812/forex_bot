from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from bot.domain.events import BboEvent, BookDeltaEvent, MarkPriceEvent, TradeEvent


class PayloadMappingError(ValueError):
    pass


def timestamp_from_millis(value: object) -> datetime:
    try:
        return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError) as exc:
        raise PayloadMappingError("invalid millisecond timestamp") from exc


def _required(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise PayloadMappingError(f"missing {key}")
    return payload[key]


def map_trade(payload: dict[str, Any], received_at: datetime) -> TradeEvent:
    return TradeEvent(
        symbol=str(_required(payload, "symbol")).replace("/", "_").upper(),
        exchange_ts=timestamp_from_millis(_required(payload, "timestamp")),
        received_ts=received_at,
        sequence=int(payload["sequence"]) if payload.get("sequence") is not None else None,
        price=Decimal(str(_required(payload, "price"))),
        quantity=Decimal(str(_required(payload, "quantity"))),
        aggressor_side=str(payload.get("aggressor_side", "unknown")).lower(),
        raw=payload,
    )


def map_bbo(payload: dict[str, Any], received_at: datetime) -> BboEvent:
    return BboEvent(
        symbol=str(_required(payload, "symbol")).replace("/", "_").upper(),
        exchange_ts=timestamp_from_millis(_required(payload, "timestamp")),
        received_ts=received_at,
        sequence=int(payload["sequence"]) if payload.get("sequence") is not None else None,
        bid_price=Decimal(str(_required(payload, "bid_price"))),
        bid_quantity=Decimal(str(_required(payload, "bid_quantity"))),
        ask_price=Decimal(str(_required(payload, "ask_price"))),
        ask_quantity=Decimal(str(_required(payload, "ask_quantity"))),
        raw=payload,
    )


def _levels(value: object) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(value, list):
        raise PayloadMappingError("book levels must be a list")
    try:
        return tuple((Decimal(str(level[0])), Decimal(str(level[1]))) for level in value)
    except (IndexError, TypeError, ValueError) as exc:
        raise PayloadMappingError("invalid book level") from exc


def map_depth(payload: dict[str, Any], received_at: datetime) -> BookDeltaEvent:
    return BookDeltaEvent(
        symbol=str(_required(payload, "symbol")).replace("/", "_").upper(),
        exchange_ts=timestamp_from_millis(_required(payload, "timestamp")),
        received_ts=received_at,
        sequence=int(_required(payload, "sequence")),
        previous_sequence=(
            int(payload["previous_sequence"])
            if payload.get("previous_sequence") is not None
            else None
        ),
        bids=_levels(payload.get("bids", [])),
        asks=_levels(payload.get("asks", [])),
        raw=payload,
    )


def map_mark(payload: dict[str, Any], received_at: datetime) -> MarkPriceEvent:
    funding = payload.get("funding_rate")
    return MarkPriceEvent(
        symbol=str(_required(payload, "symbol")).replace("/", "_").upper(),
        exchange_ts=timestamp_from_millis(_required(payload, "timestamp")),
        received_ts=received_at,
        sequence=int(payload["sequence"]) if payload.get("sequence") is not None else None,
        mark_price=Decimal(str(_required(payload, "mark_price"))),
        index_price=Decimal(str(_required(payload, "index_price"))),
        funding_rate=Decimal(str(funding)) if funding is not None else None,
        raw=payload,
    )
