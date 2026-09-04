from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.domain.events import BboEvent
from bot.domain.instruments import Instrument
from bot.domain.orders import OrderRequest, OrderState, OrderStatus, OrderType, Side
from bot.execution.order_manager import OrderManager
from bot.execution.paper_exchange import PaperExchange
from bot.execution.reconciliation import InvalidOrderTransition, Reconciler
from bot.risk.kill_switch import KillSwitch
from bot.risk.limits import RiskLimits
from bot.risk.sizing import SizingRejected, position_size


def instrument() -> Instrument:
    return Instrument(
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


def bbo(now: datetime, bid: str = "99.9", ask: str = "100.1") -> BboEvent:
    return BboEvent(
        "BTC_USDT",
        now,
        now,
        bid_price=Decimal(bid),
        bid_quantity=Decimal("10"),
        ask_price=Decimal(ask),
        ask_quantity=Decimal("10"),
    )


def test_position_sizing_risk_and_rounding() -> None:
    result = position_size(
        equity=Decimal("10000"),
        risk_fraction=Decimal("0.001"),
        entry_price=Decimal("100"),
        stop_price=Decimal("99"),
        instrument=instrument(),
    )
    assert result == Decimal("10")


@pytest.mark.parametrize("leverage", [Decimal("0"), Decimal("3")])
def test_unsafe_leverage_rejected(leverage: Decimal) -> None:
    with pytest.raises(SizingRejected):
        position_size(
            equity=Decimal("10000"),
            risk_fraction=Decimal("0.001"),
            entry_price=Decimal("100"),
            stop_price=Decimal("99"),
            instrument=instrument(),
            leverage=leverage,
        )


def test_daily_loss_and_drawdown_limits() -> None:
    now = datetime.now(UTC)
    limits = RiskLimits(Decimal("10000"))
    limits.record_realized(Decimal("-100"), now, Decimal("9900"))
    verdict = limits.evaluate(
        now=now,
        equity=Decimal("9900"),
        symbol_has_position=False,
        uncertain_order=False,
        market_data_certain=True,
    )
    assert "daily_loss_limit" in verdict.reasons
    verdict = limits.evaluate(
        now=now,
        equity=Decimal("9600"),
        symbol_has_position=False,
        uncertain_order=False,
        market_data_certain=True,
    )
    assert "drawdown_limit" in verdict.reasons


def test_loss_cooldown() -> None:
    now = datetime.now(UTC)
    limits = RiskLimits(Decimal("10000"), max_consecutive_losses=2, cooldown=timedelta(minutes=1))
    limits.record_realized(Decimal("-1"), now, Decimal("9999"))
    limits.record_realized(Decimal("-1"), now, Decimal("9998"))
    verdict = limits.evaluate(
        now=now,
        equity=Decimal("9998"),
        symbol_has_position=False,
        uncertain_order=False,
        market_data_certain=True,
    )
    assert "loss_cooldown" in verdict.reasons


def test_kill_switch_latches() -> None:
    switch = KillSwitch()
    switch.trigger("data_uncertain")
    with pytest.raises(RuntimeError):
        switch.assert_entry_allowed()
    with pytest.raises(PermissionError):
        switch.reset_for_new_session(operator_acknowledged=False)
    switch.reset_for_new_session(operator_acknowledged=True)
    switch.assert_entry_allowed()


def test_post_only_cross_rejected() -> None:
    now = datetime.now(UTC)
    exchange = PaperExchange({"BTC_USDT": instrument()}, Decimal("1"), Decimal("4"))
    exchange.update_bbo(bbo(now))
    request = OrderRequest(
        "one",
        "BTC_USDT",
        Side.BUY,
        Decimal("1"),
        OrderType.LIMIT,
        price=Decimal("100.1"),
        post_only=True,
    )
    assert exchange.submit(request, now).status is OrderStatus.REJECTED


def test_market_fill_and_reduce_only_protection() -> None:
    now = datetime.now(UTC)
    exchange = PaperExchange({"BTC_USDT": instrument()}, Decimal("1"), Decimal("4"))
    exchange.update_bbo(bbo(now))
    buy = OrderRequest("buy", "BTC_USDT", Side.BUY, Decimal("1"), OrderType.MARKET)
    assert exchange.submit(buy, now).status is OrderStatus.FILLED
    bad_reduce = OrderRequest(
        "bad", "BTC_USDT", Side.BUY, Decimal("1"), OrderType.MARKET, reduce_only=True
    )
    assert exchange.submit(bad_reduce, now).status is OrderStatus.REJECTED
    close = OrderRequest(
        "close", "BTC_USDT", Side.SELL, Decimal("1"), OrderType.MARKET, reduce_only=True
    )
    assert exchange.submit(close, now).status is OrderStatus.FILLED
    assert exchange.positions["BTC_USDT"].is_flat


def test_reconciler_unknown_then_fill() -> None:
    now = datetime.now(UTC)
    request = OrderRequest("id", "BTC_USDT", Side.BUY, Decimal("1"), OrderType.MARKET)
    state = OrderState(request, OrderStatus.SUBMITTING, now, now)
    reconciler = Reconciler()
    reconciler.add(state)
    reconciler.transition("id", OrderStatus.UNKNOWN, now)
    assert reconciler.has_unknown
    reconciler.transition(
        "id",
        OrderStatus.FILLED,
        now,
        filled_quantity=Decimal("1"),
        average_fill_price=Decimal("100"),
    )
    assert not reconciler.has_unknown


def test_cancel_fill_race_is_valid_but_terminal_transition_is_not() -> None:
    now = datetime.now(UTC)
    request = OrderRequest(
        "id", "BTC_USDT", Side.BUY, Decimal("1"), OrderType.LIMIT, price=Decimal("100")
    )
    reconciler = Reconciler()
    reconciler.add(OrderState(request, OrderStatus.ACCEPTED, now, now))
    reconciler.transition("id", OrderStatus.CANCEL_PENDING, now)
    reconciler.transition("id", OrderStatus.FILLED, now, filled_quantity=Decimal("1"))
    with pytest.raises(InvalidOrderTransition):
        reconciler.transition("id", OrderStatus.CANCELED, now)


def test_resting_entry_gets_protective_stop_and_stop_flattens() -> None:
    now = datetime.now(UTC)
    exchange = PaperExchange({"BTC_USDT": instrument()}, Decimal("1"), Decimal("4"))
    exchange.update_bbo(bbo(now, bid="99.9", ask="100.1"))
    manager = OrderManager(exchange)
    entry, protective = manager.submit_entry_with_stop(
        symbol="BTC_USDT",
        side=Side.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("99.9"),
        stop_price=Decimal("98.0"),
        now=now,
    )
    assert entry.status is OrderStatus.ACCEPTED
    assert protective is None
    updates = manager.on_bbo(bbo(now + timedelta(seconds=1), bid="99.8", ask="99.9"))
    assert [state.status for state in updates] == [OrderStatus.FILLED, OrderStatus.ACCEPTED]
    assert exchange.positions["BTC_USDT"].protective_exit_client_id is not None
    manager.on_bbo(bbo(now + timedelta(seconds=2), bid="97.9", ask="98.0"))
    assert exchange.positions["BTC_USDT"].is_flat


def test_uncertainty_recovery_cancels_entries_and_emergency_flattens() -> None:
    now = datetime.now(UTC)
    exchange = PaperExchange({"BTC_USDT": instrument()}, Decimal("1"), Decimal("4"))
    exchange.update_bbo(bbo(now))
    manager = OrderManager(exchange)
    exchange.submit(
        OrderRequest(
            "position",
            "BTC_USDT",
            Side.BUY,
            Decimal("1"),
            OrderType.MARKET,
        ),
        now,
    )
    working = exchange.submit(
        OrderRequest(
            "entry",
            "BTC_USDT",
            Side.BUY,
            Decimal("1"),
            OrderType.LIMIT,
            price=Decimal("99.0"),
        ),
        now,
    )
    assert manager.cancel_working_entries(now) == [working]
    flattened = manager.emergency_flatten(now)
    assert flattened[0].request.reduce_only
    assert exchange.positions["BTC_USDT"].is_flat


def test_partial_fill_is_immediately_protected_and_stop_is_resized() -> None:
    now = datetime.now(UTC)
    exchange = PaperExchange({"BTC_USDT": instrument()}, Decimal("1"), Decimal("4"))
    exchange.update_bbo(bbo(now))
    manager = OrderManager(exchange)
    entry, _ = manager.submit_entry_with_stop(
        symbol="BTC_USDT",
        side=Side.BUY,
        quantity=Decimal("2"),
        entry_price=Decimal("99.9"),
        stop_price=Decimal("98"),
        now=now,
    )
    first_event = BboEvent(
        "BTC_USDT",
        now + timedelta(seconds=1),
        now + timedelta(seconds=1),
        bid_price=Decimal("99.8"),
        bid_quantity=Decimal("1"),
        ask_price=Decimal("99.9"),
        ask_quantity=Decimal("0.5"),
    )
    first_updates = manager.on_bbo(first_event)
    assert entry.status is OrderStatus.PARTIALLY_FILLED
    assert first_updates[-1].request.quantity == Decimal("0.5")
    second_event = BboEvent(
        "BTC_USDT",
        now + timedelta(seconds=2),
        now + timedelta(seconds=2),
        bid_price=Decimal("99.8"),
        bid_quantity=Decimal("2"),
        ask_price=Decimal("99.9"),
        ask_quantity=Decimal("2"),
    )
    second_updates = manager.on_bbo(second_event)
    assert entry.status is OrderStatus.FILLED
    assert second_updates[-1].request.quantity == Decimal("2")
