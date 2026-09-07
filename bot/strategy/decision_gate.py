from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.domain.events import Direction, PillarTwoSignal, ensure_utc
from bot.news.schemas import PillarOneOpinion


@dataclass(frozen=True, slots=True)
class GateContext:
    now: datetime
    spread_bps: float
    depth_notional: float
    expected_move_bps: float
    fees_bps: float
    slippage_bps: float
    safety_margin_bps: float
    circuit_breaker_active: bool = False
    uncertain_order: bool = False
    risk_approved: bool = True
    has_position: bool = False


@dataclass(frozen=True, slots=True)
class GateDecision:
    approved: bool
    direction: Direction | None
    reasons: tuple[str, ...]


class DecisionGate:
    def __init__(
        self,
        *,
        pillar_one_min_confidence: float,
        pillar_two_min_strength: float,
        max_spread_bps: float,
        min_depth_notional: float,
    ) -> None:
        self.p1_min = pillar_one_min_confidence
        self.p2_min = pillar_two_min_strength
        self.max_spread = max_spread_bps
        self.min_depth = min_depth_notional

    def evaluate(
        self,
        symbol: str,
        pillar_one: PillarOneOpinion | None,
        pillar_two: PillarTwoSignal | None,
        context: GateContext,
    ) -> GateDecision:
        now = ensure_utc(context.now)
        rejected: list[str] = []
        if pillar_one is None or not pillar_one.applies_to(symbol, now):
            rejected.append("pillar_one_missing_or_stale")
        elif pillar_one.confidence < self.p1_min:
            rejected.append("pillar_one_confidence")
        if pillar_two is None or pillar_two.symbol != symbol or not pillar_two.is_fresh(now):
            rejected.append("pillar_two_missing_or_stale")
        elif pillar_two.strength < self.p2_min:
            rejected.append("pillar_two_strength")
        if pillar_one and pillar_two and pillar_one.direction != pillar_two.direction:
            rejected.append("pillar_disagreement")
        if context.spread_bps > self.max_spread:
            rejected.append("spread")
        if context.depth_notional < self.min_depth:
            rejected.append("depth")
        required_edge = (
            context.fees_bps + context.spread_bps
            + context.slippage_bps + context.safety_margin_bps
        )
        if context.expected_move_bps <= required_edge:
            rejected.append("insufficient_edge")
        if context.circuit_breaker_active:
            rejected.append("circuit_breaker")
        if context.uncertain_order:
            rejected.append("uncertain_order")
        if not context.risk_approved:
            rejected.append("risk")
        if context.has_position:
            rejected.append("position_exists")
        direction = pillar_two.direction if not rejected and pillar_two else None
        return GateDecision(not rejected, direction, tuple(rejected))
