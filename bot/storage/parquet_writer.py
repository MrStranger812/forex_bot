from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ParquetUnavailable(RuntimeError):
    pass


class PartitionedParquetWriter:
    def __init__(self, root: str | Path, *, batch_size: int = 1000) -> None:
        self.root = Path(root)
        self.batch_size = batch_size
        self._buffers: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    def append(
        self, event_type: str, symbol: str, record: dict[str, Any], received_at: datetime
    ) -> None:
        day = received_at.astimezone(UTC).date().isoformat()
        key = (event_type, symbol, day)
        buffer = self._buffers.setdefault(key, [])
        buffer.append(record)
        if len(buffer) >= self.batch_size:
            self._flush_key(key)

    def flush(self) -> None:
        for key in list(self._buffers):
            self._flush_key(key)

    def _flush_key(self, key: tuple[str, str, str]) -> None:
        records = self._buffers.pop(key, [])
        if not records:
            return
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ParquetUnavailable("install the storage dependency group") from exc
        event_type, symbol, day = key
        target_dir = self.root / f"event_type={event_type}" / f"symbol={symbol}" / f"date={day}"
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%H%M%S%f")
        target = target_dir / f"part-{timestamp}.parquet"
        normalized = [json.loads(json.dumps(record, default=str)) for record in records]
        table = pa.Table.from_pylist(normalized)
        pq.write_table(table, target, compression="zstd")

    def __enter__(self) -> PartitionedParquetWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.flush()
