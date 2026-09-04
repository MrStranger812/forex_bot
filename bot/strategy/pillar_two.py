from __future__ import annotations

from datetime import datetime, timedelta

from bot.domain.events import BboEvent, Direction, PillarTwoSignal, TradeEvent
from bot.features.microstructure import TradeFlow, book_imbalance, liquidity_quality
from bot.features.momentum import MomentumFeatures
from bot.features.trend import TrendFeatures
from bot.features.volatility import VolatilityFeatures


class PillarTwoEngine:
    def __init__(
        self,
        symbol: str,
        *,
        max_spread_bps: float = 5.0,
        min_depth_notional: float = 10_000.0,
        signal_ttl: timedelta = timedelta(seconds=2),
    ) -> None:
        self.symbol = symbol
        self.max_spread_bps = max_spread_bps
        self.min_depth_notional = min_depth_notional
        self.signal_ttl = signal_ttl
        self.trend = TrendFeatures()
        self.momentum = MomentumFeatures()
        self.volatility = VolatilityFeatures()
        self.flow = TradeFlow()
        self.ema_score = 0.0
        self.dm_score = 0.0
        self.roc_score = 0.0
        self.book_score = 0.0
        self.flow_score = 0.0
        self.volatility_value = 0.0
        self.volatility_suitability = 0.0
        self.liquidity = 0.0
        self.last_book_at: datetime | None = None

    def on_trade(self, event: TradeEvent) -> None:
        if event.symbol != self.symbol:
            raise ValueError("symbol mismatch")
        price = float(event.price)
        self.ema_score, self.dm_score = self.trend.update(price)
        self.roc_score = self.momentum.update(price)
        self.volatility_value, self.volatility_suitability = self.volatility.update(price)
        self.flow_score = self.flow.update(event.aggressor_side, event.quantity)

    def on_bbo(self, event: BboEvent) -> None:
        if event.symbol != self.symbol:
            raise ValueError("symbol mismatch")
        self.book_score = book_imbalance(event.bid_quantity, event.ask_quantity)
        depth_notional = float(
            event.bid_price * event.bid_quantity + event.ask_price * event.ask_quantity
        )
        self.liquidity = liquidity_quality(
            float(event.spread_bps), self.max_spread_bps, depth_notional, self.min_depth_notional
        )
        self.last_book_at = event.exchange_ts

    def signal(self, now: datetime) -> PillarTwoSignal:
        score = (
            0.30 * self.ema_score
            + 0.20 * self.dm_score
            + 0.15 * self.roc_score
            + 0.15 * self.book_score
            + 0.20 * self.flow_score
        )
        strength = min(1.0, abs(score)) * self.liquidity * self.volatility_suitability
        direction = (
            Direction.BULLISH
            if score > 0
            else Direction.BEARISH
            if score < 0
            else Direction.NEUTRAL
        )
        reasons = []
        if abs(self.ema_score) >= 0.2:
            reasons.append("positive_ema_slope" if self.ema_score > 0 else "negative_ema_slope")
        if abs(self.flow_score) >= 0.2:
            reasons.append("buy_flow" if self.flow_score > 0 else "sell_flow")
        if abs(self.book_score) >= 0.2:
            reasons.append("book_imbalance")
        regime = self._regime(score)
        return PillarTwoSignal(
            symbol=self.symbol,
            direction=direction,
            strength=strength,
            regime=regime,
            generated_at=now,
            valid_until=now + self.signal_ttl,
            reasons=tuple(reasons),
            score=score,
        )

    def _regime(self, score: float) -> str:
        trend = abs(score) >= 0.35
        high_volatility = self.volatility_value >= 0.001
        if high_volatility and trend:
            return "high_volatility_trend"
        if high_volatility:
            return "high_volatility_chop"
        if trend:
            return "low_volatility_trend"
        return "low_volatility_chop"
