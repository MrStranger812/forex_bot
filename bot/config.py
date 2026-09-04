from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from bot.adapters.ourbit.key_health import KeyHealth, key_expiry_status
from bot.adapters.ourbit.rest import EndpointManifest


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a mapping")
    return payload


def endpoint_manifest_from_env(verified: bool = False) -> EndpointManifest:
    prefix = "OURBIT_FUTURES_"
    return EndpointManifest(
        server_time=os.getenv(f"{prefix}SERVER_TIME_PATH", ""),
        instruments=os.getenv(f"{prefix}INSTRUMENTS_PATH", ""),
        book_snapshot=os.getenv(f"{prefix}BOOK_SNAPSHOT_PATH", ""),
        balance=os.getenv(f"{prefix}BALANCE_PATH", ""),
        positions=os.getenv(f"{prefix}POSITIONS_PATH", ""),
        open_orders=os.getenv(f"{prefix}OPEN_ORDERS_PATH", ""),
        order=os.getenv(f"{prefix}ORDER_PATH", ""),
        fills=os.getenv(f"{prefix}FILLS_PATH", ""),
        funding=os.getenv(f"{prefix}FUNDING_PATH", ""),
        listen_key=os.getenv(f"{prefix}LISTEN_KEY_PATH", ""),
        verified=verified,
    )


def futures_topics(symbol: str) -> list[str]:
    values = []
    for name in ("TRADES", "BBO", "DEPTH", "MARK", "FUNDING", "STATUS"):
        template = os.getenv(f"OURBIT_FUTURES_{name}_TOPIC", "")
        if not template:
            raise ValueError(f"OURBIT_FUTURES_{name}_TOPIC is not configured")
        values.append(template.format(symbol=symbol.replace("_", "")))
    return values


def require_live_interlock(config: dict[str, Any], manifest: EndpointManifest) -> None:
    if config.get("mode") != "live":
        raise RuntimeError("live runner requires a live configuration")
    if config.get("execution", {}).get("enabled") is not True:
        raise RuntimeError("live execution is disabled in configuration")
    if os.getenv("OURBIT_LIVE_ACK") != "I_UNDERSTAND_REAL_ORDERS_WILL_BE_SENT":
        raise RuntimeError("live acknowledgement is absent")
    if os.getenv("OURBIT_ACCOUNT_ELIGIBILITY_CONFIRMED") != "YES_CURRENT_TERMS_REVIEWED":
        raise RuntimeError("account and jurisdiction eligibility are not confirmed")
    if not os.getenv("OURBIT_API_KEY") or not os.getenv("OURBIT_API_SECRET"):
        raise RuntimeError("live API credentials are absent")
    created_at_raw = os.getenv("OURBIT_KEY_CREATED_AT", "")
    try:
        created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        key_health = key_expiry_status(created_at, datetime.now(UTC)).health
    except ValueError as exc:
        raise RuntimeError("OURBIT_KEY_CREATED_AT is absent or invalid") from exc
    if key_health is not KeyHealth.HEALTHY:
        raise RuntimeError(f"Ourbit API key health requires rotation: {key_health}")
    manifest.validate_live()
