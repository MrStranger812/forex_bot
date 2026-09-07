from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot.domain.events import Direction, PillarTwoSignal
from bot.features.bars import Bar
from bot.strategy.time_based import TimeBasedPillarTwoConfig, TimeBasedPillarTwoEngine

START = datetime(2025, 1, 1, tzinfo=UTC)


def bar(index: int, close: int = 10_000, *, width: str = "1", volume: int = 10) -> Bar:
    price, half_width = Decimal(close), Decimal(width)
    start = START + timedelta(minutes=index)
    return Bar(
        "BTC_USDT", 60, start, start + timedelta(minutes=1),
        price, price + half_width, price - half_width, price, Decimal(volume), 1,
    )


def warmed_engine(direction: int = 1) -> TimeBasedPillarTwoEngine:
    engine = TimeBasedPillarTwoEngine("BTC_USDT")
    for index in range(60):
        engine.on_bar(bar(index, 10_000 + direction * index * 2))
    return engine


def test_warmup_requires_complete_history() -> None:
    engine = TimeBasedPillarTwoEngine("BTC_USDT")
    assert engine.signal(START).direction == Direction.NEUTRAL
    for index in range(59):
        engine.on_bar(bar(index, 10_000 + index * 2))
    signal = engine.signal(START + timedelta(minutes=59))
    assert signal.strength == 0
    assert "warmup" in signal.reasons
    engine.on_bar(bar(59, 10_118))
    assert engine.signal(START + timedelta(minutes=60)).direction == Direction.BULLISH


@pytest.mark.parametrize(("sign", "direction"), [(1, Direction.BULLISH), (-1, Direction.BEARISH)])
def test_trend_is_directional_and_does_not_invent_probability(
    sign: int, direction: Direction,
) -> None:
    engine = warmed_engine(sign)
    signal = engine.signal(START + timedelta(minutes=60))
    assert signal.direction == direction
    assert signal.regime == "TRENDING"
    assert signal.model == "trend"
    assert signal.strength >= 0.6
    assert signal.expected_move_bps > 0
    assert signal.horizon_seconds == 300
    assert signal.probability is None
    assert "microstructure_unavailable" in signal.reasons
    assert "uncalibrated_strength" in signal.reasons


def test_repeated_queries_do_not_extend_expiration() -> None:
    engine = warmed_engine()
    close = START + timedelta(minutes=60)
    signal = engine.signal(close)
    assert engine.signal(close + timedelta(seconds=30)) == signal
    stale_time = close + timedelta(seconds=61)
    stale = engine.signal(stale_time)
    assert stale.direction == Direction.NEUTRAL
    assert "stale_bar" in stale.reasons
    assert stale.generated_at == signal.generated_at
    assert stale.valid_until == signal.valid_until
    assert not stale.is_fresh(stale_time)


def test_future_bar_is_never_available_before_close() -> None:
    engine = warmed_engine()
    evaluation = START + timedelta(minutes=60, seconds=-1)
    signal = engine.signal(evaluation)
    assert signal.strength == 0
    assert "future_bar" in signal.reasons
    assert not signal.is_fresh(evaluation)


def test_replay_is_deterministic_and_future_append_does_not_modify_past_signal() -> None:
    first, second = warmed_engine(), warmed_engine()
    now = START + timedelta(minutes=60)
    past = first.signal(now)
    assert second.signal(now) == past
    first.on_bar(bar(60, 9_000))
    assert second.signal(now) == past
    assert first.signal(now).direction == Direction.NEUTRAL


def test_gap_resets_ema_and_full_warmup() -> None:
    engine = warmed_engine()
    independent = TimeBasedPillarTwoEngine("BTC_USDT")
    for offset in range(60):
        observation = bar(120 + offset, 12_000 - offset * 2)
        engine.on_bar(observation)
        independent.on_bar(observation)
        signal = engine.signal(observation.end)
        if offset < 59:
            assert signal.direction == Direction.NEUTRAL
            assert "gap_warmup" in signal.reasons
        else:
            assert signal == independent.signal(observation.end)
            assert signal.direction == Direction.BEARISH


@pytest.mark.parametrize("index", [58, 59])
def test_out_of_order_and_duplicate_bars_are_rejected_without_mutation(index: int) -> None:
    engine = warmed_engine()
    now = START + timedelta(minutes=60)
    expected = engine.signal(now)
    with pytest.raises(ValueError, match="chronological"):
        engine.on_bar(bar(index))
    assert engine.signal(now) == expected


@pytest.mark.parametrize(
    "observation",
    [
        replace(bar(0), start=START.replace(tzinfo=None)),
        replace(bar(0), end=START + timedelta(seconds=59)),
        replace(bar(0), symbol="ETH_USDT"),
        replace(bar(0), interval_seconds=5),
        replace(bar(0), close=Decimal("NaN")),
        replace(bar(0), high=Decimal("Infinity")),
        replace(bar(0), high=Decimal("1e999")),
        replace(bar(0), low=Decimal("1e-999")),
        replace(bar(0), low=Decimal("0")),
        replace(bar(0), close=Decimal("12000")),
        replace(bar(0), low=Decimal("10001")),
        replace(bar(0), volume=Decimal("-1")),
        replace(bar(0), volume=Decimal("NaN")),
        replace(bar(0), trades=-1),
    ],
)
def test_invalid_market_data_is_rejected(observation: Bar) -> None:
    engine = TimeBasedPillarTwoEngine("BTC_USDT")
    with pytest.raises(ValueError):
        engine.on_bar(observation)
    assert engine.signal(START).regime == "UNTRADEABLE"


def test_timezone_offsets_normalize_to_utc_and_naive_query_is_rejected() -> None:
    engine = TimeBasedPillarTwoEngine("BTC_USDT")
    observation = bar(0)
    offset = timezone(timedelta(hours=3, minutes=30))
    engine.on_bar(replace(
        observation, start=observation.start.astimezone(offset),
        end=observation.end.astimezone(offset),
    ))
    assert engine.signal(observation.end).generated_at == observation.end
    assert engine.signal(observation.end).generated_at.tzinfo == UTC
    with pytest.raises(ValueError, match="timezone-aware"):
        engine.signal(observation.end.replace(tzinfo=None))


@pytest.mark.parametrize(("sign", "direction"), [(1, Direction.BEARISH), (-1, Direction.BULLISH)])
def test_mean_reversion_requires_a_range_and_reversal(sign: int, direction: Direction) -> None:
    engine = TimeBasedPillarTwoEngine("BTC_USDT")
    for index in range(58):
        engine.on_bar(bar(index, 10_000 + (2 if index % 2 else -2)))
    engine.on_bar(bar(58, 10_000 + sign * 4))
    engine.on_bar(bar(59, 10_000 + sign * 3))
    signal = engine.signal(START + timedelta(minutes=60))
    assert signal.regime == "RANGING"
    assert signal.model == "mean_reversion"
    assert signal.direction == direction
    assert signal.expected_move_bps > 0


def test_range_extreme_without_reversal_does_not_create_reversion_signal() -> None:
    engine = TimeBasedPillarTwoEngine("BTC_USDT")
    for index in range(59):
        engine.on_bar(bar(index, 10_000 + (2 if index % 2 else -2)))
    engine.on_bar(bar(59, 10_004))
    signal = engine.signal(START + timedelta(minutes=60))
    assert signal.direction == Direction.NEUTRAL
    assert signal.model == "none"


@pytest.mark.parametrize(("sign", "direction"), [(1, Direction.BULLISH), (-1, Direction.BEARISH)])
def test_breakout_requires_two_closes_beyond_prior_compressed_channel(
    sign: int, direction: Direction,
) -> None:
    engine = TimeBasedPillarTwoEngine("BTC_USDT")
    for index in range(60):
        engine.on_bar(bar(index))
    assert engine.signal(START + timedelta(minutes=60)).direction == Direction.NEUTRAL
    engine.on_bar(bar(60, 10_000 + sign * 2, width="0.5", volume=20))
    assert engine.signal(START + timedelta(minutes=61)).model != "breakout"
    engine.on_bar(bar(61, 10_000 + sign * 3, width="0.5", volume=20))
    signal = engine.signal(START + timedelta(minutes=62))
    assert signal.direction == direction
    assert signal.regime == "BREAKOUT_COMPRESSION"
    assert signal.model == "breakout"


def test_low_volume_breakout_is_not_confirmed() -> None:
    engine = TimeBasedPillarTwoEngine("BTC_USDT")
    for index in range(60):
        engine.on_bar(bar(index))
    engine.on_bar(bar(60, 10_002, width="0.5", volume=5))
    engine.on_bar(bar(61, 10_003, width="0.5", volume=5))
    assert engine.signal(START + timedelta(minutes=62)).model != "breakout"


def test_volatility_shock_closes_directional_gate() -> None:
    engine = warmed_engine()
    engine.on_bar(bar(60, 10_400))
    signal = engine.signal(START + timedelta(minutes=61))
    assert signal.regime == "HIGH_VOLATILITY"
    assert signal.direction == Direction.NEUTRAL
    assert signal.strength == 0


def test_insufficient_movement_is_untradeable() -> None:
    engine = TimeBasedPillarTwoEngine("BTC_USDT")
    for index in range(60):
        engine.on_bar(bar(index, width="0"))
    assert engine.signal(START + timedelta(minutes=60)).regime == "UNTRADEABLE"


@pytest.mark.parametrize(
    "overrides",
    [
        {"warmup_bars": 20}, {"interval_seconds": 0}, {"horizon_seconds": 1},
        {"min_atr_bps": float("nan")}, {"max_atr_bps": 0.1},
        {"min_atr_bps": True},
        {"fast_ema_period": 30}, {"breakout_persistence_bars": 1},
        {"range_efficiency": 0.5}, {"compression_atr_ratio": 2.0},
    ],
)
def test_invalid_configuration_rejected(overrides: dict[str, int | float]) -> None:
    with pytest.raises(ValueError):
        TimeBasedPillarTwoConfig(**overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected_move_bps": -1.0}, {"expected_move_bps": float("nan")},
        {"score": float("inf")}, {"probability": 1.1}, {"probability": float("nan")},
        {"horizon_seconds": -1}, {"horizon_seconds": 1.5},
        {"valid_until": START - timedelta(seconds=1)},
    ],
)
def test_extended_signal_validates_calibration_fields(overrides: dict[str, object]) -> None:
    signal = PillarTwoSignal("BTC_USDT", Direction.NEUTRAL, 0.0, "none", START, START, (), 0.0)
    assert signal.model == "legacy"
    assert signal.probability is None
    with pytest.raises(ValueError):
        replace(signal, **overrides)
