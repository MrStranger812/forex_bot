from __future__ import annotations

import argparse
import bisect
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

HORIZONS_SECONDS = (30, 60, 300, 900, 3600)


@dataclass(frozen=True, slots=True)
class Quote:
    timestamp: datetime
    bid: Decimal
    ask: Decimal


def executable_return(entry: Quote, exit_quote: Quote, direction: str, fee_bps: Decimal) -> Decimal:
    fees = fee_bps * 2 / Decimal(10_000)
    if direction == "bullish":
        gross = exit_quote.bid / entry.ask - 1
    elif direction == "bearish":
        gross = entry.bid / exit_quote.ask - 1
    else:
        raise ValueError("direction must be bullish or bearish")
    return gross - fees


def label_news(news: dict[str, Any], quotes: list[Quote], fee_bps: Decimal) -> dict[str, Any]:
    published = datetime.fromisoformat(str(news["published_at"]).replace("Z", "+00:00"))
    timestamps = [quote.timestamp for quote in quotes]
    entry_index = bisect.bisect_left(timestamps, published)
    if entry_index >= len(quotes):
        raise ValueError("no quote after news publication")
    entry = quotes[entry_index]
    returns: dict[str, dict[str, str]] = {}
    labels: dict[str, str] = {}
    for horizon in HORIZONS_SECONDS:
        exit_index = bisect.bisect_left(timestamps, published + timedelta(seconds=horizon))
        if exit_index >= len(quotes):
            continue
        long_return = executable_return(entry, quotes[exit_index], "bullish", fee_bps)
        short_return = executable_return(entry, quotes[exit_index], "bearish", fee_bps)
        returns[str(horizon)] = {"long": str(long_return), "short": str(short_return)}
        if long_return <= 0 and short_return <= 0:
            labels[str(horizon)] = "neutral"
        else:
            labels[str(horizon)] = "bullish" if long_return > short_return else "bearish"
    if not returns:
        raise ValueError("no horizon has a matching quote")
    result = dict(news)
    result["fee_adjusted_returns"] = returns
    result["labels"] = labels
    result["label_provenance"] = {
        "entry_quote_timestamp": entry.timestamp.isoformat(),
        "fee_bps_each_side": str(fee_bps),
        "method": "executable_bid_ask",
    }
    return result


def add_conversation(row: dict[str, Any], horizon_seconds: int) -> dict[str, Any]:
    horizon = str(horizon_seconds)
    if horizon not in row["labels"]:
        raise ValueError(f"missing quote for training horizon {horizon_seconds}")
    direction = row["labels"][horizon]
    horizon_returns = row["fee_adjusted_returns"][horizon]
    selected_return = max(
        abs(Decimal(horizon_returns["long"])), abs(Decimal(horizon_returns["short"]))
    )
    strength = min(Decimal("1"), selected_return * Decimal(10_000) / Decimal("25"))
    abstain = direction == "neutral"
    output = {
        "direction": direction,
        "confidence": 0.5,
        "strength": float(strength),
        "horizon_seconds": horizon_seconds,
        "affected_symbols": row["symbols"],
        "event_type": row.get("event_type", "other"),
        "abstain": abstain,
    }
    result = dict(row)
    result["messages"] = [
        {
            "role": "system",
            "content": (
                "Classify short-lived crypto impact as strict JSON. "
                "Abstain when evidence is neutral."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "published_at": row["published_at"],
                    "symbols": row["symbols"],
                    "headline": row["headline"],
                    "body": row["body"],
                    "source_reliability": row["source_reliability"],
                },
                separators=(",", ":"),
            ),
        },
        {"role": "assistant", "content": json.dumps(output, separators=(",", ":"))},
    ]
    return result


def chronological_group_split(
    rows: list[dict[str, Any]], embargo: timedelta
) -> dict[str, list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: row["published_at"])
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in ordered:
        content_hash = str(row["content_hash"])
        groups.setdefault(content_hash, []).append(row)
    grouped = sorted(groups.values(), key=lambda group: group[0]["published_at"])
    train_cut = int(len(grouped) * 0.70)
    validation_cut = int(len(grouped) * 0.85)
    train = [row for group in grouped[:train_cut] for row in group]
    validation = [row for group in grouped[train_cut:validation_cut] for row in group]
    test = [row for group in grouped[validation_cut:] for row in group]
    if train and validation:
        boundary = datetime.fromisoformat(validation[0]["published_at"].replace("Z", "+00:00"))
        train = [
            row
            for row in train
            if datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
            < boundary - embargo
        ]
    if validation and test:
        boundary = datetime.fromisoformat(test[0]["published_at"].replace("Z", "+00:00"))
        validation = [
            row
            for row in validation
            if datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
            < boundary - embargo
        ]
    return {"train": train, "validation": validation, "test": test}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build reproducible news-impact splits")
    parser.add_argument("--news", required=True)
    parser.add_argument("--quotes", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--fee-bps", type=Decimal, required=True)
    parser.add_argument("--embargo-seconds", type=int, default=3600)
    parser.add_argument(
        "--training-horizon-seconds", type=int, choices=HORIZONS_SECONDS, default=300
    )
    args = parser.parse_args()
    news_path = Path(args.news).resolve()
    quarantined = Path("data/llm_training").resolve()
    if quarantined in news_path.parents:
        raise RuntimeError("legacy generated XAU/USD training data is quarantined")
    quotes = [
        Quote(
            datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")),
            Decimal(str(row["bid"])),
            Decimal(str(row["ask"])),
        )
        for row in _read_jsonl(Path(args.quotes))
    ]
    rows = [
        add_conversation(label_news(item, quotes, args.fee_bps), args.training_horizon_seconds)
        for item in _read_jsonl(news_path)
    ]
    splits = chronological_group_split(rows, timedelta(seconds=args.embargo_seconds))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    for name, records in splits.items():
        with (output / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
