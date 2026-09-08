from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.domain.events import Direction, PillarTwoSignal
from bot.features.bars import Bar
from bot.strategy.time_based import TimeBasedPillarTwoConfig
from research.pillar_two_backtest import CandleBacktestConfig, CandleCosts, run_backtest
from research.pillar_two_experiments import Candidate, build_signal_cache
from research.trade_outcomes import (
    FEATURE_NAMES,
    Opportunity,
    label_opportunity,
    mature_outcomes,
    opportunity_features,
)

START = datetime(2026, 5, 1, tzinfo=UTC)
D = Decimal
COSTS = CandleCosts(D(5), D(1), D(1), D(2))
CANDIDATE = Candidate("test", 180, D(100), D(100), 0.5, ("trend",))


def bar(
    index: int, opening: str = "100", high: str = "100.1", low: str = "99.9", close: str = "100"
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
        D(5),
        2,
    )


def opportunity(direction: Direction = Direction.BULLISH) -> Opportunity:
    at = START + timedelta(minutes=1)
    signal = PillarTwoSignal(
        "BTC_USDT",
        direction,
        1,
        "TRENDING",
        at,
        at + timedelta(seconds=60),
        (),
        1,
        expected_move_bps=1000,
        horizon_seconds=180,
        model="trend",
    )
    return Opportunity(at, 0, signal, (0.0,) * len(FEATURE_NAMES))


class OneSignal:
    def __init__(self, signal: PillarTwoSignal) -> None:
        self.value = signal

    def on_bar(self, bar: Bar) -> None:
        pass

    def signal(self, now: datetime) -> PillarTwoSignal:
        return (
            self.value
            if now == self.value.generated_at
            else replace(self.value, direction=Direction.NEUTRAL, strength=0)
        )


@pytest.mark.parametrize("direction", [Direction.BULLISH, Direction.BEARISH])
@pytest.mark.parametrize(
    "path",
    [
        [bar(0), bar(1), bar(2), bar(3)],
        [bar(0), bar(1, high="102", low="98"), bar(2), bar(3)],
        [bar(0), bar(1), bar(2, "90", "90.1", "89.9", "90"), bar(3)],
        [bar(0), bar(1), bar(2, "110", "110.1", "109.9", "110"), bar(3)],
    ],
)
def test_labels_match_executor_net_return_exits_and_costs(
    direction: Direction, path: list[Bar]
) -> None:
    opp = opportunity(direction)
    result = label_opportunity(path, opp, CANDIDATE, COSTS, COSTS.stressed(D(2)))
    assert result is not None
    for costs, expected in [(COSTS, result.net_bps), (COSTS.stressed(D(2)), result.stress_net_bps)]:
        execution = run_backtest(
            path,
            symbol="BTC_USDT",
            engine_config=TimeBasedPillarTwoConfig(horizon_seconds=180),
            config=CandleBacktestConfig(
                stop_bps=D(100),
                target_bps=D(100),
                daily_loss_fraction=D(1),
                max_drawdown_fraction=D(1),
            ),
            costs=costs,
            source_factory=lambda: OneSignal(opp.signal),
        )
        assert len(execution.trades) == 1
        trade = execution.trades[0]
        assert float(trade.net_return_on_entry_notional_pct * 100) == pytest.approx(expected)
        assert trade.exit_at == result.known_at
        assert trade.exit_reason == result.exit_reason


def test_incomplete_and_missing_future_do_not_fabricate_labels() -> None:
    assert (
        label_opportunity([bar(0), bar(1)], opportunity(), CANDIDATE, COSTS, COSTS.stressed(D(2)))
        is None
    )
    assert (
        label_opportunity(
            [bar(0), bar(1), bar(3)], opportunity(), CANDIDATE, COSTS, COSTS.stressed(D(2))
        )
        is None
    )


def test_purge_requires_full_horizon_even_when_a_stop_is_known_early() -> None:
    label = label_opportunity(
        [bar(0), bar(1, high="102", low="98"), bar(2), bar(3)],
        opportunity(),
        CANDIDATE,
        COSTS,
        COSTS.stressed(D(2)),
    )
    assert label is not None
    assert label.known_at == START + timedelta(minutes=2)
    assert mature_outcomes([label], START, START + timedelta(minutes=3)) == []
    assert mature_outcomes([label], START, START + timedelta(minutes=4)) == [label]


def test_net_forecast_gate_does_not_charge_costs_twice_or_accept_negative_expectancy() -> None:
    path = [bar(index) for index in range(4)]
    for net, expected_trades in [(3, 1), (2, 0), (-1, 0)]:
        signal = replace(opportunity().signal, expected_move_bps=0, expected_net_return_bps=net)
        result = run_backtest(
            path,
            symbol="BTC_USDT",
            engine_config=TimeBasedPillarTwoConfig(horizon_seconds=180),
            config=CandleBacktestConfig(),
            costs=COSTS,
            source_factory=lambda signal=signal: OneSignal(signal),
        )
        assert len(result.trades) == expected_trades


def test_feature_values_and_decision_schedule_are_prefix_invariant() -> None:
    data = [
        bar(index, str(10000 + index), str(10002 + index), str(9998 + index), str(10001 + index))
        for index in range(120)
    ]
    config = TimeBasedPillarTwoConfig()
    candidate = replace(CANDIDATE, horizon_seconds=3600)
    prefix = data[:85]
    early = opportunity_features(prefix, build_signal_cache(prefix, config), candidate, 300)
    full = opportunity_features(data, build_signal_cache(data, config), candidate, 300)
    assert early
    assert early == [row for row in full if row.decision_at <= prefix[-1].end]
    assert all(int(row.decision_at.timestamp()) % 300 == 0 for row in full)


def test_signal_rejects_nonfinite_net_expectancy() -> None:
    with pytest.raises(ValueError, match="expected_net_return"):
        replace(opportunity().signal, expected_net_return_bps=float("nan"))
