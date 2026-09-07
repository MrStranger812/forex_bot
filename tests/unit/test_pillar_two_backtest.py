from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.domain.events import Direction, PillarTwoSignal
from bot.features.bars import Bar
from bot.strategy.time_based import TimeBasedPillarTwoConfig
from research.pillar_two_backtest import (
    BacktestResult,
    CandleBacktestConfig,
    CandleCosts,
    chronological_windows,
    run_backtest,
)

D = Decimal
START = datetime(2026, 1, 1, tzinfo=UTC)
FREE = CandleCosts(D(0), D(0), D(0), D(0))
CONFIG = CandleBacktestConfig(
    risk_fraction=D("0.01"),
    stop_bps=D(100),
    target_bps=D(100),
    daily_loss_fraction=D(1),
    max_drawdown_fraction=D(1),
)


def bar(
    index: int, opening: str = "100", high: str = "100", low: str = "100", close: str = "100"
) -> Bar:
    return Bar(
        "BTC_USDT",
        60,
        START + timedelta(minutes=index),
        START + timedelta(minutes=index + 1),
        D(opening),
        D(high),
        D(low),
        D(close),
        D(1),
        1,
    )


class ScheduledSignals:
    def __init__(self, schedule: dict[int, Direction], expected_move_bps: float = 1000) -> None:
        self.schedule = schedule
        self.expected_move_bps = expected_move_bps
        self.last: Bar | None = None

    def on_bar(self, value: Bar) -> None:
        self.last = value

    def signal(self, now: datetime) -> PillarTwoSignal:
        assert self.last is not None
        index = int((self.last.start - START).total_seconds() // 60)
        direction = self.schedule.get(index, Direction.NEUTRAL)
        return PillarTwoSignal(
            "BTC_USDT",
            direction,
            1,
            "TRENDING",
            now,
            now + timedelta(seconds=60),
            (),
            1,
            expected_move_bps=self.expected_move_bps,
            horizon_seconds=120,
            model="trend",
            probability=None,
        )


def run(
    bars: list[Bar],
    schedule: dict[int, Direction] | None = None,
    *,
    costs: CandleCosts = FREE,
    config: CandleBacktestConfig = CONFIG,
    start: datetime | None = None,
    end: datetime | None = None,
    horizon: int = 120,
    expected_move_bps: float = 1000,
) -> BacktestResult:
    return run_backtest(
        bars,
        symbol="BTC_USDT",
        costs=costs,
        config=config,
        start=start,
        end=end,
        engine_config=TimeBasedPillarTwoConfig(horizon_seconds=horizon),
        source_factory=lambda: ScheduledSignals(
            {0: Direction.BULLISH} if schedule is None else schedule,
            expected_move_bps,
        ),
    )


def test_closed_signal_enters_next_open_not_signal_close() -> None:
    result = run([bar(0, high="110", close="110"), bar(1, "120", "120", "120", "120")])
    (trade,) = result.trades
    assert trade.signal_at == START + timedelta(minutes=1)
    assert trade.entry_at == START + timedelta(minutes=1)
    assert trade.entry_reference == D(120)
    assert trade.exit_reason == "end_of_window"
    assert trade.net_pnl == 0


@pytest.mark.parametrize("direction", [Direction.BULLISH, Direction.BEARISH])
def test_ambiguous_stop_target_always_uses_stop(direction: Direction) -> None:
    result = run([bar(0), bar(1, high="102", low="98")], {0: direction})
    (trade,) = result.trades
    assert trade.exit_reason == "stop"
    assert trade.ambiguous_stop_target
    assert trade.net_pnl == D(-100)
    assert result.metrics["ambiguous_stop_target_count"] == 1


@pytest.mark.parametrize(
    "direction,gap,expected",
    [
        (Direction.BULLISH, "95", "-500"),
        (Direction.BEARISH, "105", "-500"),
    ],
)
def test_adverse_opening_gap_can_exceed_planned_risk(
    direction: Direction,
    gap: str,
    expected: str,
) -> None:
    result = run([bar(0), bar(1), bar(2, gap, gap, gap, gap)], {0: direction})
    (trade,) = result.trades
    assert trade.exit_reason == "stop_gap"
    assert trade.exit_reference == D(gap)
    assert trade.net_pnl == D(expected)


def test_favorable_gap_does_not_credit_better_target_fill() -> None:
    result = run([bar(0), bar(1), bar(2, "110", "110", "110", "110")])
    (trade,) = result.trades
    assert trade.exit_reason == "target_gap"
    assert trade.exit_reference == D(101)
    assert trade.net_pnl == D(100)


def test_flat_prices_lose_both_sides_fees_spread_slippage_and_never_exceed_one_x() -> None:
    costs = CandleCosts(D(5), D(2), D(1), D(2))
    result = run([bar(0), bar(1)], costs=costs, config=replace(CONFIG, stop_bps=D(1)))
    (trade,) = result.trades
    assert trade.gross_pnl == 0
    assert trade.entry_fill == D("100.02")
    assert trade.exit_fill == D("99.98")
    assert trade.net_pnl == -trade.execution_cost - trade.fees
    assert trade.entry_fill * trade.quantity <= CONFIG.initial_equity
    assert (
        trade.entry_fill * trade.quantity
        + costs.fee(
            trade.entry_fill,
            trade.quantity,
        )
        <= CONFIG.initial_equity
    )
    assert result.metrics["final_equity"] == pytest.approx(float(D(10000) + trade.net_pnl))
    assert result.metrics["win_rate_pct"] == 0
    assert result.metrics["profit_factor"] == 0


def test_position_risk_budget_includes_all_execution_costs_at_stop() -> None:
    result = run([bar(0), bar(1, low="98")], costs=CandleCosts(D(5), D(2), D(1), D(2)))
    (trade,) = result.trades
    assert float(trade.net_pnl) == pytest.approx(-100)
    assert trade.quantity < 100


def test_expected_edge_must_strictly_exceed_costs_plus_safety_margin() -> None:
    costs = CandleCosts(D(5), D(2), D(1), D(2))
    result = run([bar(0), bar(1)], costs=costs, expected_move_bps=16)
    assert not result.trades
    assert result.metrics["entry_rejection_counts"]["cost_gate"] == 1
    assert result.metrics["win_rate_pct"] is None
    assert result.metrics["profit_factor"] is None


def test_time_exit_occurs_before_later_extremes_can_affect_position() -> None:
    result = run([bar(0), bar(1), bar(2, high="200", low="1")], horizon=60)
    (trade,) = result.trades
    assert trade.exit_at == START + timedelta(minutes=2)
    assert trade.exit_reason == "time"
    assert trade.net_pnl == 0


def test_only_one_position_can_be_open_and_last_position_is_force_closed() -> None:
    result = run([bar(i) for i in range(5)], {i: Direction.BULLISH for i in range(5)}, horizon=600)
    (trade,) = result.trades
    assert trade.entry_at == START + timedelta(minutes=1)
    assert trade.exit_at == START + timedelta(minutes=5)
    assert trade.exit_reason == "end_of_window"


def test_data_gap_cancels_pending_and_flattens_held_position_at_available_price() -> None:
    canceled = run([bar(0), bar(2)])
    assert not canceled.trades
    held = run([bar(0), bar(1), bar(3, "90", "90", "90", "90")], horizon=600)
    (trade,) = held.trades
    assert trade.exit_reason == "data_gap"
    assert trade.exit_reference == D(90)
    assert trade.net_pnl == D(-1000)


def test_mtm_drawdown_detects_unrealized_loss_before_profitable_time_exit() -> None:
    result = run(
        [bar(0), bar(1, low="99.5", close="99.5"), bar(2, "99.5", "100.5", "99.5", "100.5")]
    )
    assert result.trades[0].net_pnl > 0
    assert result.metrics["max_mtm_drawdown_pct"] == pytest.approx(0.5)
    assert result.metrics["max_boundary_mtm_drawdown_pct"] == pytest.approx(0.5)


def test_max_drawdown_kill_switch_prevents_following_entries() -> None:
    result = run(
        [bar(0), bar(1, low="98"), bar(2), bar(3)],
        {i: Direction.BULLISH for i in range(4)},
        config=replace(CONFIG, max_drawdown_fraction=D("0.005")),
    )
    assert len(result.trades) == 1
    assert result.metrics["kill_switch_triggered"]
    assert result.metrics["risk_event_counts"]["max_drawdown_kill_switch"] == 1
    assert result.metrics["entry_rejection_counts"]["max_drawdown_kill_switch"] == 2


def test_loss_cooldown_blocks_then_expires() -> None:
    result = run(
        [bar(0), bar(1, low="98"), bar(2), bar(3), bar(4)],
        {i: Direction.BULLISH for i in range(5)},
        horizon=60,
        config=replace(CONFIG, max_consecutive_losses=1, cooldown_seconds=120),
    )
    assert len(result.trades) == 2
    assert result.trades[-1].entry_at == START + timedelta(minutes=4)
    assert result.metrics["entry_rejection_counts"]["loss_cooldown"] == 2


def test_daily_loss_halt_resets_next_utc_day_but_does_not_erase_total_drawdown() -> None:
    result = run(
        [bar(0), bar(1, low="98"), bar(2), bar(1440), bar(1441)],
        {i: Direction.BULLISH for i in [0, 1, 2, 1440]},
        horizon=60,
        config=replace(CONFIG, daily_loss_fraction=D("0.005")),
    )
    assert len(result.trades) == 2
    assert result.trades[-1].entry_at == START + timedelta(minutes=1441)
    assert result.metrics["entry_rejection_counts"]["daily_loss_limit"] == 1
    assert result.metrics["max_mtm_drawdown_pct"] == pytest.approx(1)


def test_chronological_split_has_full_horizon_embargo_and_no_prewindow_entries() -> None:
    bars = [bar(i) for i in range(20)]
    windows = chronological_windows(
        bars, development_fraction=0.5, embargo_seconds=120, horizon_seconds=120
    )
    assert windows["test"][0] - windows["development"][1] == timedelta(seconds=120)
    start, end = windows["test"]
    result = run(bars, {11: Direction.BULLISH, 12: Direction.BULLISH}, start=start, end=end)
    (trade,) = result.trades
    assert trade.entry_at == START + timedelta(minutes=13)
    assert trade.signal_at >= start
    assert result.metrics["initial_equity"] == 10000
    with pytest.raises(ValueError, match="embargo"):
        chronological_windows(
            bars, development_fraction=0.5, embargo_seconds=119, horizon_seconds=120
        )


def test_end_window_force_close_ignores_later_data() -> None:
    result = run(
        [bar(0), bar(1), bar(2, high="200", low="1")], end=START + timedelta(minutes=2), horizon=600
    )
    (trade,) = result.trades
    assert trade.net_pnl == 0
    assert trade.exit_at == START + timedelta(minutes=2)


def test_partial_final_candle_is_not_used_for_exit_or_drawdown() -> None:
    result = run(
        [bar(0), bar(1), bar(2, high="200", low="1")],
        end=START + timedelta(minutes=2, seconds=30),
        horizon=600,
    )
    trade, = result.trades
    assert trade.net_pnl == 0
    assert trade.exit_at == START + timedelta(minutes=2)
    assert result.metrics["evaluated_bars"] == 2
    assert result.metrics["max_mtm_drawdown_pct"] == 0


@pytest.mark.parametrize(
    "values",
    [
        {"max_notional_fraction": D("1.01")},
        {"risk_fraction": D("NaN")},
        {"initial_equity": D("Infinity")},
        {"min_strength": float("nan")},
        {"risk_fraction": D("0.01001")},
    ],
)
def test_invalid_risk_configuration_fails_closed(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        CandleBacktestConfig(**values)  # type: ignore[arg-type]


def test_invalid_costs_and_nonintegral_holding_period_are_rejected() -> None:
    with pytest.raises(ValueError):
        CandleCosts(D(-1), D(1), D(1), D(1))
    with pytest.raises(ValueError, match="exact number"):
        run([bar(0), bar(1)], horizon=61)


def test_cost_stress_preserves_safety_margin_and_doubles_execution_assumptions() -> None:
    costs = CandleCosts(D(5), D(2), D(1), D(3)).stressed(D(2))
    assert costs.required_edge_bps == D(31)
    assert costs.safety_margin_bps == D(3)
