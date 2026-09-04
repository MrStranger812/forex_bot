from datetime import UTC, datetime

import pytest

from bot.adapters.ourbit.rest import EndpointManifest
from bot.config import load_config, require_live_interlock


def complete_manifest() -> EndpointManifest:
    return EndpointManifest(
        server_time="/time",
        instruments="/instruments",
        book_snapshot="/book",
        balance="/balance",
        positions="/positions",
        open_orders="/orders",
        order="/order",
        fills="/fills",
        funding="/funding",
        listen_key="/listen-key",
        verified=True,
    )


def test_live_config_disabled_by_default() -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        require_live_interlock(load_config("configs/live.yaml"), complete_manifest())


def test_live_requires_eligibility_after_ack_and_credentials(monkeypatch) -> None:
    config = load_config("configs/live.yaml")
    config["execution"]["enabled"] = True
    monkeypatch.setenv("OURBIT_LIVE_ACK", "I_UNDERSTAND_REAL_ORDERS_WILL_BE_SENT")
    monkeypatch.setenv("OURBIT_API_KEY", "key")
    monkeypatch.setenv("OURBIT_API_SECRET", "secret")
    with pytest.raises(RuntimeError, match="eligibility"):
        require_live_interlock(config, complete_manifest())


def test_complete_interlocks_can_pass(monkeypatch) -> None:
    config = load_config("configs/live.yaml")
    config["execution"]["enabled"] = True
    monkeypatch.setenv("OURBIT_LIVE_ACK", "I_UNDERSTAND_REAL_ORDERS_WILL_BE_SENT")
    monkeypatch.setenv("OURBIT_ACCOUNT_ELIGIBILITY_CONFIRMED", "YES_CURRENT_TERMS_REVIEWED")
    monkeypatch.setenv("OURBIT_API_KEY", "key")
    monkeypatch.setenv("OURBIT_API_SECRET", "secret")
    monkeypatch.setenv("OURBIT_KEY_CREATED_AT", datetime.now(UTC).isoformat())
    require_live_interlock(config, complete_manifest())
