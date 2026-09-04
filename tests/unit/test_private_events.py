from datetime import UTC, datetime

import pytest

from bot.adapters.ourbit.private_ws import (
    PrivateEventKind,
    PrivateSequenceGap,
    PrivateSequenceGuard,
    normalize_private_event,
)


@pytest.mark.parametrize("kind", list(PrivateEventKind))
def test_all_private_account_event_kinds(kind: PrivateEventKind) -> None:
    received = datetime.now(UTC)
    event = normalize_private_event(
        {"event_type": kind, "timestamp": 1_700_000_000_000, "sequence": 1}, received
    )
    assert event.kind is kind
    assert event.received_ts is received


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"event_type": "unknown", "timestamp": 1},
        {"event_type": "full_fill", "timestamp": "bad"},
    ],
)
def test_invalid_private_event_fails_closed(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        normalize_private_event(payload, datetime.now(UTC))


def test_private_sequence_gap_requires_rest_reconciliation() -> None:
    received = datetime.now(UTC)
    guard = PrivateSequenceGuard()
    first = normalize_private_event(
        {"event_type": "full_fill", "timestamp": 1_700_000_000_000, "sequence": 4},
        received,
    )
    gap = normalize_private_event(
        {"event_type": "balance_update", "timestamp": 1_700_000_000_001, "sequence": 6},
        received,
    )
    assert guard.accept(first)
    with pytest.raises(PrivateSequenceGap):
        guard.accept(gap)
