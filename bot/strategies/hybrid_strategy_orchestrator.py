import time
import requests
from typing import Optional

from nautilus_trader.model.data import Bar
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from nautilus_trader.indicators.ema import ExponentialMovingAverage
from nautilus_trader.indicators.atr import AverageTrueRange
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.core.message import Event

class HybridRegimeConfig(StrategyConfig):
    instrument_id: str = "XAU/USD.SIM"
    fast_ema_period: int = 20    # Represents short-term momentum
    slow_ema_period: int = 50    # Represents the micro-regime
    atr_period: int = 14         # For dynamic stop losses
    trade_size: int = 100_000    # 1 Standard Lot
    llm_api_url: str = "http://localhost:8000/analyze" # Your FastAPI LLM Server
    llm_poll_interval_hours: int = 4 # How often to ask the LLM for a new bias

class HybridRegimeOrchestrator(Strategy):
    """
    Two-Pillar Strategy:
    Pillar 1: Uncensored LLM dictates the Macro Bias.
    Pillar 2: EMA/ATR Rule-based engine dictates Micro-Regime and Execution.
    """
    def __init__(self, config: HybridRegimeConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.trade_size = config.trade_size
        
        # Micro-Cycle Indicators (Pillar 2)
        self.fast_ema = ExponentialMovingAverage(config.fast_ema_period)
        self.slow_ema = ExponentialMovingAverage(config.slow_ema_period)
        self.atr = AverageTrueRange(config.atr_period)
        
        # Macro State (Pillar 1)
        self.current_macro_bias: str = "Neutral"
        self.last_llm_poll_time: float = 0
        self.llm_api_url = config.llm_api_url
        self.poll_interval_seconds = config.llm_poll_interval_hours * 3600

    def on_start(self):
        # Register indicators for automatic updates from the engine
        self.register_indicator_for_bars(self.instrument_id, self.fast_ema)
        self.register_indicator_for_bars(self.instrument_id, self.slow_ema)
        self.register_indicator_for_bars(self.instrument_id, self.atr)
        
        # Request 15-minute historical and live bars
        self.request_bars(self.instrument_id)
        self.log.info("🚀 Hybrid Regime Orchestrator Online. Awaiting data...")

    def update_macro_bias(self, current_timestamp: float):
        """
        Polls the local Uncensored LLM API every 4 hours to get the macro bias.
        In a production HFT environment, this would be handled asynchronously via an Event Bus 
        so the main trading thread never blocks, but this simulates the architecture.
        """
        if current_timestamp - self.last_llm_poll_time >= self.poll_interval_seconds:
            self.log.info("🧠 Polling Uncensored LLM for Macro Regime Shift...")
            
            try:
                # In live trading, you'd pass the latest news headlines here
                payload = {
                    "headline": "Latest Gold Market News",
                    "body": "Global markets react to recent central bank policy shifts..."
                }
                # Timeout set to 2 seconds to prevent freezing the matching engine
                response = requests.post(self.llm_api_url, json=payload, timeout=2.0)
                
                if response.status_code == 200:
                    data = response.json()
                    self.current_macro_bias = data.get("sentiment", "Neutral")
                    self.log.info(f"🎯 LLM Macro Bias Updated: {self.current_macro_bias.upper()}")
                else:
                    self.log.warning(f"⚠️ LLM API returned status {response.status_code}")
                    
            except requests.exceptions.RequestException as e:
                # If the API is down or starting up, default to Neutral
                self.log.warning(f"⚠️ LLM API unreachable. Defaulting to Neutral. Error: {e}")
                self.current_macro_bias = "Neutral"
                
            self.last_llm_poll_time = current_timestamp

    def on_bar(self, bar: Bar):
        # Ensure indicators are fully warmed up with historical data
        if not self.slow_ema.initialized or not self.atr.initialized:
            return

        # 1. Update Macro Bias (Pillar 1)
        current_time_sec = bar.ts_init / 1_000_000_000  # Convert nanoseconds to seconds
        self.update_macro_bias(current_time_sec)

        # 2. Determine Micro Regime (Pillar 2)
        fast = self.fast_ema.value
        slow = self.slow_ema.value
        volatility = self.atr.value
        
        # Rule: Is the micro-trend aligned and expanding?
        is_bull_regime = fast > slow
        is_bear_regime = fast < slow
        
        # We don't trade flat, dead markets. ATR must show movement.
        # (Assuming XAU/USD, an ATR of > 1.5 implies acceptable 15m volatility)
        has_volatility = volatility > 1.5 

        # 3. The Orchestrator Logic: The Matrix Check
        # We only execute if Pillar 1 (LLM) perfectly aligns with Pillar 2 (Rule Engine)
        
        net_long = self.portfolio.is_net_long(self.instrument_id)
        net_short = self.portfolio.is_net_short(self.instrument_id)

        # Bullish Matrix Alignment
        if self.current_macro_bias == "Bullish" and is_bull_regime and has_volatility:
            if not net_long:
                self.log.info(f"[{bar.ts_init}] 🟢 ALIGNMENT: LLM Bullish + Micro Regime Bullish. Executing LONG.")
                self.close_all_positions(self.instrument_id) # Flip position if short
                self.submit_market_order(
                    instrument_id=self.instrument_id,
                    side=OrderSide.BUY,
                    quantity=self.trade_size
                )
                # Note: In a live system, you would attach an OCO (One-Cancels-Other) bracket 
                # order here using `volatility * 2` for the stop loss.

        # Bearish Matrix Alignment
        elif self.current_macro_bias == "Bearish" and is_bear_regime and has_volatility:
            if not net_short:
                self.log.info(f"[{bar.ts_init}] 🔴 ALIGNMENT: LLM Bearish + Micro Regime Bearish. Executing SHORT.")
                self.close_all_positions(self.instrument_id) # Flip position if long
                self.submit_market_order(
                    instrument_id=self.instrument_id,
                    side=OrderSide.SELL,
                    quantity=self.trade_size
                )

        # 4. Trailing Stop / Exhaustion Exit Logic
        # Even if the macro news is bullish, if the 15m trend exhausts (price breaks below slow EMA), get out to protect capital.
        if net_long and bar.close.as_double() < slow:
            self.log.info(f"[{bar.ts_init}] 🛡️ RISK: Long trend exhausted on micro-level. Liquidating position.")
            self.close_all_positions(self.instrument_id)
            
        elif net_short and bar.close.as_double() > slow:
            self.log.info(f"[{bar.ts_init}] 🛡️ RISK: Short trend exhausted on micro-level. Liquidating position.")
            self.close_all_positions(self.instrument_id)