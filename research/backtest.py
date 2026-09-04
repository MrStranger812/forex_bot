from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CostModel:
    fee_bps: Decimal
    slippage_bps: Decimal

    def round_trip_cost(self, notional: Decimal) -> Decimal:
        return notional * (self.fee_bps + self.slippage_bps) * 2 / Decimal(10_000)


class DeterministicReplay:
    def __init__(self, records: Iterable[dict[str, Any]]) -> None:
        indexed = list(enumerate(records))
        self.records = [
            record
            for _, record in sorted(
                indexed,
                key=lambda item: (
                    item[1].get("exchange_ts", item[1].get("timestamp", "")),
                    item[0],
                ),
            )
        ]

    def run(self, handler: Callable[[dict[str, Any]], None]) -> int:
        for record in self.records:
            handler(record)
        return len(self.records)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministically replay normalized JSONL events")
    parser.add_argument("input")
    args = parser.parse_args()
    replay = DeterministicReplay(read_jsonl(args.input))
    print(f"Replayed {replay.run(lambda _: None)} records")


if __name__ == "__main__":
    main()
