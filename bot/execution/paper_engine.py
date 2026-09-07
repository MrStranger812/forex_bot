from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from bot.domain.events import BboEvent, Direction, PillarTwoSignal, TradeEvent
from bot.domain.instruments import Instrument
from bot.domain.orders import OrderState, Side
from bot.features.bars import MultiIntervalBarBuilder
from bot.news.schemas import PillarOneOpinion
from bot.risk.kill_switch import KillSwitch
from bot.risk.limits import RiskLimits
from bot.risk.sizing import SizingRejected, position_size
from bot.strategy.decision_gate import DecisionGate, GateContext, GateDecision
from bot.strategy.pillar_one import PillarOneBook
from bot.strategy.pillar_two import PillarTwoEngine
from bot.strategy.time_based import TimeBasedPillarTwoConfig, TimeBasedPillarTwoEngine

from .order_manager import OrderManager
from .paper_exchange import PaperExchange


@dataclass(frozen=True, slots=True)
class PaperEngineResult:
    signal: PillarTwoSignal
    gate: GateDecision
    order: OrderState | None


class PaperTradingEngine:
    """Deterministic composition root for news, market math, risk, and paper execution."""

    def __init__(
        self,
        instrument: Instrument,
        *,
        starting_equity: Decimal,
        risk_fraction: Decimal,
        daily_loss_fraction: Decimal,
        max_drawdown_fraction: Decimal,
        pillar_one_min_confidence: float,
        pillar_two_min_strength: float,
        max_spread_bps: float,
        min_depth_notional: float,
        maker_fee_bps: Decimal,
        taker_fee_bps: Decimal,
        slippage_bps: Decimal,
        safety_margin_bps: Decimal,
        expected_move_scale_bps: Decimal,
        stop_distance_bps: Decimal,
        time_based_config: TimeBasedPillarTwoConfig | None = None,
    ) -> None:
        self.instrument = instrument
        self.risk_fraction = risk_fraction
        self.slippage_bps = slippage_bps
        self.safety_margin_bps = safety_margin_bps
        self.expected_move_scale_bps = expected_move_scale_bps
        self.stop_distance_bps = stop_distance_bps
        self.exchange = PaperExchange(
            {instrument.symbol: instrument},
            maker_fee_bps,
            taker_fee_bps,
            starting_equity,
        )
        self.orders = OrderManager(self.exchange)
        self.pillar_one = PillarOneBook()
        self.pillar_two = PillarTwoEngine(
            instrument.symbol,
            max_spread_bps=max_spread_bps,
            min_depth_notional=min_depth_notional,
        )
        self.time_based_pillar_two = (
            TimeBasedPillarTwoEngine(instrument.symbol, time_based_config)
            if time_based_config is not None
            else None
        )
        self.bar_builder = (
            MultiIntervalBarBuilder((time_based_config.interval_seconds,))
            if time_based_config is not None
            else None
        )
        self.gate = DecisionGate(
            pillar_one_min_confidence=pillar_one_min_confidence,
            pillar_two_min_strength=pillar_two_min_strength,
            max_spread_bps=max_spread_bps,
            min_depth_notional=min_depth_notional,
        )
        self.risk = RiskLimits(
            starting_equity,
            daily_loss_fraction=daily_loss_fraction,
            max_drawdown_fraction=max_drawdown_fraction,
        )
        self.kill_switch = KillSwitch()

    def publish_news(self, opinion: PillarOneOpinion) -> None:
        self.pillar_one.publish(opinion)

    def on_trade(self, event: TradeEvent) -> None:
        if event.symbol != self.instrument.symbol:
            raise ValueError("symbol mismatch")
        if self.bar_builder is not None and self.time_based_pillar_two is not None:
            for bar in self.bar_builder.update(event):
                self.time_based_pillar_two.on_bar(bar)
        else:
            self.pillar_two.on_trade(event)

    def on_bbo(self, event: BboEvent) -> PaperEngineResult:
        realized_before = self.exchange.realized_pnl
        self.orders.on_bbo(event)
        self.pillar_two.on_bbo(event)
        realized_change = self.exchange.realized_pnl - realized_before
        if realized_change:
            self.risk.record_realized(realized_change, event.received_ts, self.exchange.equity)
        signal = (
            self.time_based_pillar_two.signal(event.received_ts)
            if self.time_based_pillar_two is not None
            else self.pillar_two.signal(event.exchange_ts)
        )
        position = self.exchange.positions[event.symbol]
        risk = self.risk.evaluate(
            now=event.received_ts,
            equity=self.exchange.equity,
            symbol_has_position=not position.is_flat,
            uncertain_order=False,
            market_data_certain=True,
        )
        depth = event.bid_price * event.bid_quantity + event.ask_price * event.ask_quantity
        decision = self.gate.evaluate(
            event.symbol,
            self.pillar_one.consensus(event.symbol, event.exchange_ts),
            signal,
            GateContext(
                now=event.received_ts,
                spread_bps=float(event.spread_bps),
                depth_notional=float(depth),
                expected_move_bps=(
                    signal.expected_move_bps
                    if self.time_based_pillar_two is not None
                    else float(abs(Decimal(str(signal.score))) * self.expected_move_scale_bps)
                ),
                fees_bps=float(self.exchange.maker_fee_bps + self.exchange.taker_fee_bps),
                slippage_bps=float(self.slippage_bps),
                safety_margin_bps=float(self.safety_margin_bps),
                circuit_breaker_active=self.kill_switch.active,
                risk_approved=risk.approved,
                has_position=not position.is_flat,
            ),
        )
        order = self._enter(decision, event) if decision.approved else None
        return PaperEngineResult(signal, decision, order)

    def _enter(self, decision: GateDecision, event: BboEvent) -> OrderState | None:
        if decision.direction not in {Direction.BULLISH, Direction.BEARISH}:
            return None
        side = Side.BUY if decision.direction is Direction.BULLISH else Side.SELL
        entry = event.bid_price if side is Side.BUY else event.ask_price
        distance = entry * self.stop_distance_bps / Decimal(10_000)
        stop = entry - distance if side is Side.BUY else entry + distance
        entry = self.instrument.round_price(entry)
        stop = self.instrument.round_price(stop)
        try:
            quantity = position_size(
                equity=self.exchange.equity,
                risk_fraction=self.risk_fraction,
                entry_price=entry,
                stop_price=stop,
                instrument=self.instrument,
            )
        except SizingRejected:
            return None
        order, _ = self.orders.submit_entry_with_stop(
            symbol=event.symbol,
            side=side,
            quantity=quantity,
            entry_price=entry,
            stop_price=stop,
            now=event.received_ts,
        )
        return order
