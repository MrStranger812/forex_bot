from __future__ import annotations

from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True, slots=True)
class StressScenario:
    symbol: str
    regime: str
    cost_multiplier: float
    latency_ms: int


SYMBOLS = ("BTC_USDT", "ETH_USDT", "SOL_USDT")
REGIMES = (
    "strong_uptrend",
    "strong_downtrend",
    "low_volatility_chop",
    "high_volatility_chop",
    "flash_decline_rebound",
    "news_spread_expansion",
)
COST_MULTIPLIERS = (1.0, 1.25, 1.5, 2.0)
LATENCIES_MS = (50, 150, 500, 1500)


def scenario_matrix() -> tuple[StressScenario, ...]:
    return tuple(
        StressScenario(*values)
        for values in product(SYMBOLS, REGIMES, COST_MULTIPLIERS, LATENCIES_MS)
    )


if __name__ == "__main__":
    scenarios = scenario_matrix()
    assert len(scenarios) == 288
    print(f"Generated {len(scenarios)} deterministic stress scenarios")
