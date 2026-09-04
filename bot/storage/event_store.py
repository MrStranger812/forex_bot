from __future__ import annotations

import json
import sqlite3
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EventStore(AbstractContextManager["EventStore"]):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                stream TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_time TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_stream_id ON events(stream, event_id)"
        )
        self.connection.commit()

    def append(
        self,
        stream: str,
        event_type: str,
        payload: dict[str, Any],
        event_time: datetime | None = None,
    ) -> int:
        timestamp = event_time or datetime.now(UTC)
        cursor = self.connection.execute(
            "INSERT INTO events(stream, event_type, event_time, payload) VALUES (?, ?, ?, ?)",
            (
                stream,
                event_type,
                timestamp.isoformat(),
                json.dumps(payload, default=str, separators=(",", ":")),
            ),
        )
        self.connection.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("event insert did not return an ID")
        return cursor.lastrowid

    def read(self, stream: str, after_id: int = 0) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT event_id, event_type, event_time, payload FROM events "
            "WHERE stream = ? AND event_id > ? ORDER BY event_id",
            (stream, after_id),
        )
        return [
            {
                "event_id": row[0],
                "event_type": row[1],
                "event_time": row[2],
                "payload": json.loads(row[3]),
            }
            for row in rows
        ]

    def close(self) -> None:
        self.connection.close()

    def __exit__(self, *_: object) -> None:
        self.close()
