from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from bot.domain.events import ensure_utc


class KeyHealth(StrEnum):
    HEALTHY = "healthy"
    EXPIRING = "expiring"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class KeyExpiryStatus:
    health: KeyHealth
    expires_at: datetime
    remaining: timedelta


def key_expiry_status(
    created_at: datetime,
    now: datetime,
    *,
    lifetime: timedelta = timedelta(days=180),
    warn_before: timedelta = timedelta(days=30),
) -> KeyExpiryStatus:
    created_at = ensure_utc(created_at)
    now = ensure_utc(now)
    expires_at = created_at + lifetime
    remaining = expires_at - now
    if remaining <= timedelta(0):
        health = KeyHealth.EXPIRED
    elif remaining <= warn_before:
        health = KeyHealth.EXPIRING
    else:
        health = KeyHealth.HEALTHY
    return KeyExpiryStatus(health, expires_at, remaining)
