import hashlib
import json

from research.backtest import DeterministicReplay


def replay_digest(records: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()

    def consume(record: dict[str, object]) -> None:
        digest.update(json.dumps(record, sort_keys=True).encode())

    DeterministicReplay(records).run(consume)
    return digest.hexdigest()


def test_same_capture_always_replays_to_same_digest() -> None:
    records: list[dict[str, object]] = [
        {"exchange_ts": "2026-01-01T00:00:00.002Z", "sequence": 2},
        {"exchange_ts": "2026-01-01T00:00:00.001Z", "sequence": 1},
    ]
    assert replay_digest(records) == replay_digest(list(reversed(records)))
