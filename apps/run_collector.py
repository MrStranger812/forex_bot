from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime
from typing import Any

from bot.adapters.ourbit.public_ws import OurbitPublicWebSocket
from bot.config import futures_topics, load_config
from bot.storage.parquet_writer import PartitionedParquetWriter


async def run(config_path: str) -> None:
    config = load_config(config_path)
    symbols = list(config["symbols"])
    ws_url = os.getenv("OURBIT_WS_URL", "wss://wbs.ourbit.com/ws")
    writer = PartitionedParquetWriter(config["market_data"]["record_dir"])

    async def handle(payload: dict[str, Any], received_at: datetime) -> None:
        symbol = str(payload.get("symbol", payload.get("s", "UNKNOWN"))).replace("/", "_")
        channel = str(payload.get("channel", payload.get("c", "raw")))
        writer.append(
            channel, symbol, {"received_at": received_at.isoformat(), "raw": payload}, received_at
        )

    clients = [OurbitPublicWebSocket(ws_url, futures_topics(symbol), handle) for symbol in symbols]
    try:
        async with asyncio.TaskGroup() as group:
            for client in clients:
                group.create_task(client.run())
    finally:
        writer.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Ourbit futures raw-event collector")
    parser.add_argument("--config", default="configs/development.yaml")
    args = parser.parse_args()
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
