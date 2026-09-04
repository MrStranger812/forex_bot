from datetime import UTC, datetime, timedelta

import pytest

from bot.domain.events import Direction, PillarTwoSignal
from bot.news.schemas import PillarOneOpinion
from bot.strategy.decision_gate import DecisionGate, GateContext
from bot.strategy.pillar_one import PillarOneBook


def opinions(
    now: datetime,
    p1_direction: Direction = Direction.BULLISH,
    p2_direction: Direction = Direction.BULLISH,
):
    p1 = PillarOneOpinion(p1_direction, 0.8, 0.7, 60, ("BTC_USDT",), "macro", False, now, "hash")
    p2 = PillarTwoSignal(
        "BTC_USDT", p2_direction, 0.8, "trend", now, now + timedelta(seconds=2), (), 0.8
    )
    return p1, p2


def context(now: datetime, **changes: object) -> GateContext:
    values = {
        "now": now,
        "spread_bps": 1,
        "depth_notional": 20_000,
        "expected_move_bps": 10,
        "fees_bps": 2,
        "slippage_bps": 1,
        "safety_margin_bps": 1,
    }
    values.update(changes)
    return GateContext(**values)  # type: ignore[arg-type]


@pytest.fixture
def gate() -> DecisionGate:
    return DecisionGate(
        pillar_one_min_confidence=0.65,
        pillar_two_min_strength=0.6,
        max_spread_bps=5,
        min_depth_notional=10_000,
    )


def test_gate_approves_full_alignment(gate: DecisionGate) -> None:
    now = datetime.now(UTC)
    p1, p2 = opinions(now)
    assert gate.evaluate("BTC_USDT", p1, p2, context(now)).approved


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"spread_bps": 6}, "spread"),
        ({"depth_notional": 1}, "depth"),
        ({"expected_move_bps": 4}, "insufficient_edge"),
        ({"circuit_breaker_active": True}, "circuit_breaker"),
        ({"uncertain_order": True}, "uncertain_order"),
        ({"risk_approved": False}, "risk"),
        ({"has_position": True}, "position_exists"),
    ],
)
def test_gate_rejects_context_failures(
    gate: DecisionGate, change: dict[str, object], reason: str
) -> None:
    now = datetime.now(UTC)
    p1, p2 = opinions(now)
    result = gate.evaluate("BTC_USDT", p1, p2, context(now, **change))
    assert not result.approved
    assert reason in result.reasons


def test_gate_rejects_disagreement(gate: DecisionGate) -> None:
    now = datetime.now(UTC)
    p1, p2 = opinions(now, p2_direction=Direction.BEARISH)
    assert "pillar_disagreement" in gate.evaluate("BTC_USDT", p1, p2, context(now)).reasons


def test_stale_pillar_one_rejected(gate: DecisionGate) -> None:
    old = datetime.now(UTC) - timedelta(minutes=2)
    p1, p2 = opinions(old)
    now = datetime.now(UTC)
    assert "pillar_one_missing_or_stale" in gate.evaluate("BTC_USDT", p1, p2, context(now)).reasons


def test_conflicting_pillar_one_has_no_consensus() -> None:
    now = datetime.now(UTC)
    book = PillarOneBook()
    bullish, _ = opinions(now)
    bearish = PillarOneOpinion(
        Direction.BEARISH, 0.9, 0.8, 60, ("BTC_USDT",), "macro", False, now, "other"
    )
    book.publish(bullish)
    book.publish(bearish)
    assert book.consensus("BTC_USDT", now) is None
