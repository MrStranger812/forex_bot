from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bot.domain.events import TradeEvent


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    interval_seconds: int
    start: datetime
    end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trades: int


@dataclass(slots=True)
class _WorkingBar:
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trades: int


class MultiIntervalBarBuilder:
    def __init__(self, intervals: tuple[int, ...] = (1, 5, 15, 60)) -> None:
        if not intervals or any(value <= 0 for value in intervals):
            raise ValueError("intervals must be positive")
        self.intervals = intervals
        self._working: dict[tuple[str, int], _WorkingBar] = {}

    @staticmethod
    def _bucket(timestamp: datetime, seconds: int) -> datetime:
        epoch = int(timestamp.timestamp())
        return datetime.fromtimestamp(epoch - epoch % seconds, tz=UTC)

    def update(self, trade: TradeEvent) -> list[Bar]:
        completed: list[Bar] = []
        for interval in self.intervals:
            key = (trade.symbol, interval)
            start = self._bucket(trade.exchange_ts, interval)
            working = self._working.get(key)
            if working is not None and start < working.start:
                continue
            if working is not None and start > working.start:
                completed.append(self._finish(trade.symbol, interval, working))
                working = None
            if working is None:
                working = _WorkingBar(
                    start=start,
                    open=trade.price,
                    high=trade.price,
                    low=trade.price,
                    close=trade.price,
                    volume=Decimal("0"),
                    trades=0,
                )
                self._working[key] = working
            working.high = max(working.high, trade.price)
            working.low = min(working.low, trade.price)
            working.close = trade.price
            working.volume += trade.quantity
            working.trades += 1
        return completed

    @staticmethod
    def _finish(symbol: str, interval: int, working: _WorkingBar) -> Bar:
        return Bar(
            symbol=symbol,
            interval_seconds=interval,
            start=working.start,
            end=working.start + timedelta(seconds=interval),
            open=working.open,
            high=working.high,
            low=working.low,
            close=working.close,
            volume=working.volume,
            trades=working.trades,
        )
