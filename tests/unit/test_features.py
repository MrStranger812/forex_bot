from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.domain.events import BboEvent, TradeEvent
from bot.features.bars import MultiIntervalBarBuilder
from bot.features.microstructure import TradeFlow, book_imbalance, liquidity_quality, microprice
from bot.strategy.pillar_two import PillarTwoEngine


def trade(at: datetime, price: str, side: str = "buy") -> TradeEvent:
    return TradeEvent(
        "BTC_USDT",
        at,
        at,
        price=Decimal(price),
        quantity=Decimal("1"),
        aggressor_side=side,
    )


def test_bar_builder_emits_all_intervals_on_boundary() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    builder = MultiIntervalBarBuilder()
    assert builder.update(trade(start, "100")) == []
    bars = builder.update(trade(start + timedelta(seconds=60), "101"))
    assert {bar.interval_seconds for bar in bars} == {1, 5, 15, 60}
    assert all(bar.open == 100 and bar.close == 100 for bar in bars)


def test_late_trade_does_not_corrupt_bar() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    builder = MultiIntervalBarBuilder((1,))
    builder.update(trade(start + timedelta(seconds=1), "101"))
    assert builder.update(trade(start, "1")) == []
    bar = builder.update(trade(start + timedelta(seconds=2), "102"))[0]
    assert bar.open == 101


@pytest.mark.parametrize(
    ("bid", "ask", "expected"), [("3", "1", 0.5), ("1", "3", -0.5), ("0", "0", 0.0)]
)
def test_book_imbalance(bid: str, ask: str, expected: float) -> None:
    assert book_imbalance(Decimal(bid), Decimal(ask)) == expected


def test_microprice_weights_opposite_price() -> None:
    assert microprice(Decimal("100"), Decimal("3"), Decimal("102"), Decimal("1")) == Decimal(
        "101.5"
    )


def test_trade_flow_is_bounded() -> None:
    flow = TradeFlow(3)
    assert flow.update("buy", Decimal("2")) == 1
    assert flow.update("sell", Decimal("1")) == pytest.approx(1 / 3)
    assert flow.update("unknown", Decimal("10")) == pytest.approx(1 / 3)


def test_liquidity_quality_rejects_wide_spread() -> None:
    assert liquidity_quality(6, 5, 100_000, 10_000) == 0


def test_pillar_two_generates_bounded_signal() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    engine = PillarTwoEngine("BTC_USDT")
    for index in range(100):
        now = start + timedelta(milliseconds=index * 100)
        engine.on_trade(trade(now, str(100 + index / 10)))
    now = start + timedelta(seconds=10)
    engine.on_bbo(
        BboEvent(
            "BTC_USDT",
            now,
            now,
            bid_price=Decimal("109.8"),
            bid_quantity=Decimal("100"),
            ask_price=Decimal("109.9"),
            ask_quantity=Decimal("10"),
        )
    )
    signal = engine.signal(now)
    assert 0 <= signal.strength <= 1
    assert signal.symbol == "BTC_USDT"
