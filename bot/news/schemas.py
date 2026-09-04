from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from bot.domain.events import Direction, ensure_utc


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


@dataclass(frozen=True, slots=True)
class NewsItem:
    source_id: str
    canonical_url: str
    published_at: datetime
    received_at: datetime
    symbols: tuple[str, ...]
    headline: str
    body: str
    language: str
    source_reliability: float
    content_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "published_at", ensure_utc(self.published_at))
        object.__setattr__(self, "received_at", ensure_utc(self.received_at))
        object.__setattr__(self, "canonical_url", canonicalize_url(self.canonical_url))
        if not 0 <= self.source_reliability <= 1:
            raise ValueError("source_reliability must be within [0, 1]")
        expected = self.hash_content(self.headline, self.body)
        if self.content_hash != expected:
            raise ValueError("content_hash does not match content")

    @staticmethod
    def hash_content(headline: str, body: str) -> str:
        normalized = " ".join(f"{headline}\n{body}".lower().split())
        return hashlib.sha256(normalized.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PillarOneOpinion:
    direction: Direction
    confidence: float
    strength: float
    horizon_seconds: int
    affected_symbols: tuple[str, ...]
    event_type: str
    abstain: bool
    generated_at: datetime
    source_content_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", ensure_utc(self.generated_at))
        if not 0 <= self.confidence <= 1 or not 0 <= self.strength <= 1:
            raise ValueError("confidence and strength must be within [0, 1]")
        if self.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if self.abstain and self.direction is not Direction.NEUTRAL:
            raise ValueError("abstaining opinions must be neutral")
        if not self.abstain and self.direction is Direction.NEUTRAL:
            raise ValueError("neutral opinions must abstain")
        if not self.affected_symbols:
            raise ValueError("affected_symbols cannot be empty")

    @property
    def valid_until(self) -> datetime:
        return self.generated_at + timedelta(seconds=self.horizon_seconds)

    def applies_to(self, symbol: str, now: datetime) -> bool:
        now = ensure_utc(now)
        return (
            not self.abstain
            and symbol in self.affected_symbols
            and self.generated_at <= now <= self.valid_until
        )

    @classmethod
    def from_json(
        cls, raw: str, *, generated_at: datetime, source_content_hash: str
    ) -> PillarOneOpinion:
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("LLM response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("LLM response must be a JSON object")
        required = {
            "direction",
            "confidence",
            "strength",
            "horizon_seconds",
            "affected_symbols",
            "event_type",
            "abstain",
        }
        if set(payload) != required:
            raise ValueError(f"LLM schema fields must be exactly {sorted(required)}")
        try:
            direction = Direction(str(payload["direction"]).lower())
            symbols = tuple(str(item).upper() for item in payload["affected_symbols"])
            if not all(symbol in {"BTC_USDT", "ETH_USDT"} for symbol in symbols):
                raise ValueError("unknown affected symbol")
            return cls(
                direction=direction,
                confidence=float(payload["confidence"]),
                strength=float(payload["strength"]),
                horizon_seconds=int(payload["horizon_seconds"]),
                affected_symbols=symbols,
                event_type=str(payload["event_type"]),
                abstain=payload["abstain"] if isinstance(payload["abstain"], bool) else _bad_bool(),
                generated_at=generated_at,
                source_content_hash=source_content_hash,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid LLM opinion: {exc}") from exc


def _bad_bool() -> bool:
    raise ValueError("abstain must be boolean")
