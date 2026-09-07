from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bot.domain.events import BboEvent, Direction, TradeEvent
from bot.domain.instruments import Instrument
from bot.execution.paper_engine import PaperTradingEngine
from bot.news.schemas import PillarOneOpinion
from bot.strategy.time_based import TimeBasedPillarTwoConfig


def engine(time_based: bool = False) -> PaperTradingEngine:
    instrument = Instrument(
        "BTC_USDT",
        "BTC",
        "USDT",
        "USDT",
        Decimal("0.1"),
        Decimal("0.001"),
        Decimal("0.001"),
        Decimal("5"),
        max_leverage=Decimal("2"),
    )
    return PaperTradingEngine(
        instrument,
        starting_equity=Decimal("10000"),
        risk_fraction=Decimal("0.0005"),
        daily_loss_fraction=Decimal("0.01"),
        max_drawdown_fraction=Decimal("0.03"),
        pillar_one_min_confidence=0.65,
        pillar_two_min_strength=0.3,
        max_spread_bps=20,
        min_depth_notional=10_000,
        maker_fee_bps=Decimal("1"),
        taker_fee_bps=Decimal("4"),
        slippage_bps=Decimal("1"),
        safety_margin_bps=Decimal("3"),
        expected_move_scale_bps=Decimal("25"),
        stop_distance_bps=Decimal("10"),
        time_based_config=TimeBasedPillarTwoConfig() if time_based else None,
    )


def bbo(now: datetime) -> BboEvent:
    return BboEvent(
        "BTC_USDT",
        now,
        now,
        bid_price=Decimal("100.0"),
        bid_quantity=Decimal("100"),
        ask_price=Decimal("100.1"),
        ask_quantity=Decimal("10"),
    )


def make_pillar_two_strong(runtime: PaperTradingEngine) -> None:
    runtime.pillar_two.ema_score = 1
    runtime.pillar_two.dm_score = 1
    runtime.pillar_two.roc_score = 1
    runtime.pillar_two.flow_score = 1
    runtime.pillar_two.volatility_suitability = 1


def test_pillar_one_publication_never_creates_an_order() -> None:
    runtime = engine()
    now = datetime.now(UTC)
    runtime.publish_news(
        PillarOneOpinion(
            Direction.BULLISH,
            0.8,
            0.8,
            60,
            ("BTC_USDT",),
            "macro",
            False,
            now,
            "hash",
        )
    )
    assert runtime.exchange.orders == {}


def test_no_entry_without_pillar_one() -> None:
    runtime = engine()
    make_pillar_two_strong(runtime)
    result = runtime.on_bbo(bbo(datetime.now(UTC)))
    assert result.order is None
    assert "pillar_one_missing_or_stale" in result.gate.reasons


def test_aligned_pillars_create_only_resting_protected_entry_plan() -> None:
    runtime = engine()
    make_pillar_two_strong(runtime)
    now = datetime.now(UTC)
    runtime.publish_news(
        PillarOneOpinion(
            Direction.BULLISH,
            0.9,
            0.9,
            60,
            ("BTC_USDT",),
            "macro",
            False,
            now,
            "hash",
        )
    )
    result = runtime.on_bbo(bbo(now))
    assert result.gate.approved
    assert result.order is not None
    assert result.order.request.post_only
    assert result.order.request.price == Decimal("100.0")


def test_time_based_paper_engine_consumes_completed_trade_bars_and_requires_news() -> None:
    runtime = engine(time_based=True)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for minute in range(62):
        now = start + timedelta(minutes=minute)
        runtime.on_trade(
            TradeEvent(
                "BTC_USDT", now, now,
                price=Decimal("100") + Decimal(minute) / 10,
                quantity=Decimal("1"),
            )
        )
    quote = replace(bbo(now), bid_price=Decimal("106"), ask_price=Decimal("106.01"))
    result = runtime.on_bbo(quote)
    assert result.signal.model != "legacy"
    assert result.signal.probability is None
    assert result.order is None
    assert result.gate.reasons == ("pillar_one_missing_or_stale",)
    runtime.publish_news(
        PillarOneOpinion(
            Direction.BULLISH, 0.9, 0.9, 60, ("BTC_USDT",), "macro", False, now, "timed-news"
        )
    )
    aligned = runtime.on_bbo(quote)
    assert aligned.gate.approved
    assert aligned.order is not None
    assert aligned.order.request.post_only


def test_time_based_signal_cannot_be_refreshed_by_quotes_after_trade_feed_stalls() -> None:
    runtime = engine(time_based=True)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for minute in range(62):
        now = start + timedelta(minutes=minute)
        runtime.on_trade(
            TradeEvent(
                "BTC_USDT", now, now,
                price=Decimal("100") + Decimal(minute) / 10,
                quantity=Decimal("1"),
            )
        )
    stale = runtime.on_bbo(bbo(now + timedelta(hours=1)))
    assert stale.order is None
    assert stale.signal.strength == 0
    # A quote carrying an old exchange timestamp cannot renew expired trade data
    # when it actually arrives two minutes later.
    delayed = runtime.on_bbo(replace(bbo(now), received_ts=now + timedelta(minutes=2)))
    assert "stale_bar" in delayed.signal.reasons
    assert delayed.signal.strength == 0
