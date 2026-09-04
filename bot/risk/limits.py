from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RiskVerdict:
    approved: bool
    reasons: tuple[str, ...]


class RiskLimits:
    def __init__(
        self,
        starting_equity: Decimal,
        *,
        daily_loss_fraction: Decimal = Decimal("0.01"),
        max_drawdown_fraction: Decimal = Decimal("0.03"),
        max_consecutive_losses: int = 3,
        cooldown: timedelta = timedelta(minutes=15),
    ) -> None:
        if starting_equity <= 0:
            raise ValueError("starting_equity must be positive")
        self.starting_equity = starting_equity
        self.peak_equity = starting_equity
        self.session_equity = starting_equity
        self.realized_today = Decimal("0")
        self.session_date: date | None = None
        self.daily_loss_fraction = daily_loss_fraction
        self.max_drawdown_fraction = max_drawdown_fraction
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown = cooldown
        self.consecutive_losses = 0
        self.cooldown_until: datetime | None = None

    def roll_session(self, now: datetime, equity: Decimal) -> None:
        today = now.astimezone(UTC).date()
        if self.session_date != today:
            self.session_date = today
            self.session_equity = equity
            self.realized_today = Decimal("0")
            self.consecutive_losses = 0
            self.cooldown_until = None

    def record_realized(self, pnl: Decimal, now: datetime, equity: Decimal) -> None:
        self.roll_session(now, equity)
        self.realized_today += pnl
        self.peak_equity = max(self.peak_equity, equity)
        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.max_consecutive_losses:
                self.cooldown_until = now + self.cooldown
        elif pnl > 0:
            self.consecutive_losses = 0

    def evaluate(
        self,
        *,
        now: datetime,
        equity: Decimal,
        symbol_has_position: bool,
        uncertain_order: bool,
        market_data_certain: bool,
    ) -> RiskVerdict:
        self.roll_session(now, equity)
        self.peak_equity = max(self.peak_equity, equity)
        reasons: list[str] = []
        if self.realized_today <= -(self.session_equity * self.daily_loss_fraction):
            reasons.append("daily_loss_limit")
        if equity <= self.peak_equity * (Decimal("1") - self.max_drawdown_fraction):
            reasons.append("drawdown_limit")
        if self.cooldown_until is not None and now < self.cooldown_until:
            reasons.append("loss_cooldown")
        if symbol_has_position:
            reasons.append("position_exists")
        if uncertain_order:
            reasons.append("uncertain_order")
        if not market_data_certain:
            reasons.append("market_data_uncertain")
        return RiskVerdict(not reasons, tuple(reasons))
