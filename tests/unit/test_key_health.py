from datetime import UTC, datetime, timedelta

import pytest

from bot.adapters.ourbit.key_health import KeyHealth, key_expiry_status


@pytest.mark.parametrize(
    ("age_days", "expected"),
    [(1, KeyHealth.HEALTHY), (160, KeyHealth.EXPIRING), (180, KeyHealth.EXPIRED)],
)
def test_key_expiry_health(age_days: int, expected: KeyHealth) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert key_expiry_status(now - timedelta(days=age_days), now).health is expected
