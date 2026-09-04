from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal

from bot.config import load_config
from bot.domain.events import BboEvent
from bot.domain.instruments import Instrument
from bot.execution.paper_engine import PaperTradingEngine


def default_btc_instrument() -> Instrument:
    """Paper-only metadata. Live metadata must be discovered from Ourbit."""
    return Instrument(
        symbol="BTC_USDT",
        base_asset="BTC",
        quote_asset="USDT",
        settlement_asset="USDT",
        price_increment=Decimal("0.1"),
        quantity_increment=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("5"),
        contract_multiplier=Decimal("1"),
        max_leverage=Decimal("2"),
    )


def run(config_path: str) -> None:
    config = load_config(config_path)
    if config["mode"] != "paper":
        raise RuntimeError("paper runner requires paper configuration")
    instrument = default_btc_instrument()
    execution = config["execution"]
    strategy = config["strategy"]
    risk = config["risk"]
    engine = PaperTradingEngine(
        instrument,
        starting_equity=Decimal(str(risk["starting_equity"])),
        risk_fraction=Decimal(str(risk["risk_fraction"])),
        daily_loss_fraction=Decimal(str(risk["daily_loss_fraction"])),
        max_drawdown_fraction=Decimal(str(risk["max_drawdown_fraction"])),
        pillar_one_min_confidence=float(strategy["pillar_one_min_confidence"]),
        pillar_two_min_strength=float(strategy["pillar_two_min_strength"]),
        max_spread_bps=float(strategy["max_spread_bps"]),
        min_depth_notional=float(strategy["min_depth_notional"]),
        maker_fee_bps=Decimal(str(execution["maker_fee_bps"])),
        taker_fee_bps=Decimal(str(execution["taker_fee_bps"])),
        slippage_bps=Decimal("1"),
        safety_margin_bps=Decimal(str(strategy["min_edge_bps"])),
        expected_move_scale_bps=Decimal(str(strategy["expected_move_scale_bps"])),
        stop_distance_bps=Decimal(str(strategy["stop_distance_bps"])),
    )
    now = datetime.now(UTC)
    engine.exchange.update_bbo(
        BboEvent(
            symbol="BTC_USDT",
            exchange_ts=now,
            received_ts=now,
            bid_price=Decimal("59999.9"),
            bid_quantity=Decimal("1"),
            ask_price=Decimal("60000.1"),
            ask_quantity=Decimal("1"),
        )
    )
    print("Paper exchange ready: BTC_USDT, 1x leverage policy, no network orders.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local deterministic paper exchange")
    parser.add_argument("--config", default="configs/paper.yaml")
    args = parser.parse_args()
    run(args.config)


if __name__ == "__main__":
    main()
