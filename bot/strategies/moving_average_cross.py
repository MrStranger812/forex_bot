from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from nautilus_trader.indicators.averages import SimpleMovingAverage
from nautilus_trader.indicators.momentum import RelativeStrengthIndex
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity

class MovingAverageCrossConfig(StrategyConfig):
    instrument_id: str = "EUR/USD.SIM"
    fast_period: int = 10
    slow_period: int = 20
    baseline_period: int = 200  # The Macro Trend Filter
    rsi_period: int = 14        # The Momentum Filter
    trade_size: int = 100_000   # 1 Standard Lot

class MovingAverageCross(Strategy):
    def __init__(self, config: MovingAverageCrossConfig):
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.trade_size = config.trade_size
        
        # Initialize our indicator stack
        self.fast_ma = SimpleMovingAverage(config.fast_period)
        self.slow_ma = SimpleMovingAverage(config.slow_period)
        self.baseline_ma = SimpleMovingAverage(config.baseline_period)
        self.rsi = RelativeStrengthIndex(config.rsi_period)

    def on_start(self):
        # Define the exact BarType that matches our injected data
        bar_type = BarType.from_str(f"{self.instrument_id}-1-MINUTE-LAST-EXTERNAL")

        # Register indicators so the engine automatically updates them
        self.register_indicator_for_bars(bar_type, self.fast_ma)
        self.register_indicator_for_bars(bar_type, self.slow_ma)
        self.register_indicator_for_bars(bar_type, self.baseline_ma)
        self.register_indicator_for_bars(bar_type, self.rsi)
        
        # Request the data stream
        self.subscribe_bars(bar_type)

    def on_bar(self, bar: Bar):
        # Wait until our slowest indicator has initialized
        if not self.baseline_ma.initialized or not self.rsi.initialized:
            return

        fast = self.fast_ma.value
        slow = self.slow_ma.value
        baseline = self.baseline_ma.value
        rsi_val = self.rsi.value
        current_price = float(bar.close)

        # Check our current positions
        is_long = self.portfolio.is_net_long(self.instrument_id)
        is_short = self.portfolio.is_net_short(self.instrument_id)

        # -------------------------------------------------------------
        # EXIT LOGIC (Fast & Responsive)
        # -------------------------------------------------------------
        # If we are long, exit immediately when the fast MA crosses under the slow MA
        if is_long and fast < slow:
            self.log.info(f"[{bar.ts_init}] EXIT LONG: Trend reversed.")
            self.close_all_positions(self.instrument_id)
            is_long = False # Update state
            
        # If we are short, exit immediately when the fast MA crosses over the slow MA
        elif is_short and fast > slow:
            self.log.info(f"[{bar.ts_init}] EXIT SHORT: Trend reversed.")
            self.close_all_positions(self.instrument_id)
            is_short = False # Update state

        # -------------------------------------------------------------
        # ENTRY LOGIC (Strict & Filtered Confluence)
        # -------------------------------------------------------------
        # Only look for entries if we are flat (no open positions)
        if not is_long and not is_short:
            
            # Rule 1: Confirmed Bullish Confluence
            if fast > slow and current_price > baseline and rsi_val > 50.0:
                self.log.info(f"[{bar.ts_init}] ENTRY LONG: Confluence Met.")
                order = self.order_factory.market(
                    instrument_id=self.instrument_id,
                    order_side=OrderSide.BUY,
                    quantity=Quantity.from_int(self.trade_size),
                )
                self.submit_order(order)
                
            # Rule 2: Confirmed Bearish Confluence
            elif fast < slow and current_price < baseline and rsi_val < 50.0:
                self.log.info(f"[{bar.ts_init}] ENTRY SHORT: Confluence Met.")
                order = self.order_factory.market(
                    instrument_id=self.instrument_id,
                    order_side=OrderSide.SELL,
                    quantity=Quantity.from_int(self.trade_size),
                )
                self.submit_order(order)