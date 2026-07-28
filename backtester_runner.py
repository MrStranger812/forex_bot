import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone

from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.objects import Money
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model.identifiers import Venue, InstrumentId
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import PriceType, AggregationSource
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from bot.strategies.moving_average_cross import MovingAverageCross, MovingAverageCrossConfig

def generate_random_walk_bars(instrument_id_str, start_price, num_bars, period_str="1min", trend=0.0001, volatility=0.02):
    """
    Generates synthetic Nautilus Bar objects using a random walk.
    """
    # Create time series index
    timestamps = pd.date_range(
        start=datetime.now(timezone.utc) - timedelta(minutes=num_bars),
        periods=num_bars,
        freq=period_str
    )
    
    # Generate prices using a Gaussian random walk + trend
    prices = [start_price]
    for _ in range(1, num_bars):
        change = np.random.normal(trend, volatility / np.sqrt(num_bars))
        prices.append(prices[-1] * (1 + change))

    # Construct the Bar objects
    bars = []
    
    try:
        # FIXED: Replaced the dot with a hyphen after instrument_id_str
        bar_type = BarType.from_str(f"{instrument_id_str}-1-MINUTE-LAST-EXTERNAL")
    except AttributeError:
        # Fallback to the manual instantiation approach if from_str is not available
        bar_type = BarType(
            instrument_id=InstrumentId.from_str(instrument_id_str),
            bar_type_id=f"{instrument_id_str}-{period_str}-LAST-EXTERNAL",
            price_type=PriceType.LAST,
            aggregation_source=AggregationSource.EXTERNAL
        )

    for i in range(len(timestamps)):
        # To simulate a realistic bar range
        noise = volatility * prices[i]
        o = prices[i]
        c = prices[i+1] if i < num_bars - 1 else prices[i]
        h = max(o, c) + (noise * 0.5)
        low_bound = min(o, c) - (noise * 0.5)
        l = max(0, low_bound)
        
        bar = Bar(
            bar_type=bar_type,
            open=Price.from_str(f"{o:.5f}"),
            high=Price.from_str(f"{h:.5f}"),
            low=Price.from_str(f"{l:.5f}"),
            close=Price.from_str(f"{c:.5f}"),
            volume=Quantity.from_int(np.random.randint(10, 100)),
            ts_event=int(timestamps[i].timestamp() * 1_000_000_000),
            ts_init=int(timestamps[i].timestamp() * 1_000_000_000)
        )
        bars.append(bar)
        
    return bars

def main():
    print("1. Initializing Nautilus Backtest Engine...")
    engine_config = BacktestEngineConfig(trader_id="FOREX-MVP")
    engine = BacktestEngine(config=engine_config)


    # Add a simulated broker venue 
    engine.add_venue(
        venue=Venue("SIM"),
        oms_type=OmsType.NETTING,        # Standard Forex netting (offsets opposite positions)
        account_type=AccountType.MARGIN, # Forex operates on margin
        base_currency=USD,               # Base account currency
        starting_balances=[Money(100_000, USD)],
    )

    print("2. Generating Synthetic EUR/USD Market Data...")
    
    # Setup the synthetic instrument in the Rust cache
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    
    # Dynamically extract the safe, immutable ID string that the engine generated
    instrument_str = str(instrument.id)
    engine.cache.add_instrument(instrument)

    # Generate 10,000 minutes of realistic synthetic price action
    bars = generate_random_walk_bars(
        instrument_id_str=instrument_str, 
        start_price=1.0850, 
        num_bars=10_000
    )
    
    # Add the generated data to the backtester (sort=True ensures chronological safety)
    engine.add_data(bars, sort=True)

    print("3. Loading Strategy...")
    config = MovingAverageCrossConfig(
        instrument_id=instrument_str,
        fast_period=10,
        slow_period=20
    )
    strategy = MovingAverageCross(config=config)
    engine.add_strategy(strategy=strategy)

    print("4. Running Backtest (Simulating matching engine natively in Rust)...")
    engine.run()

    print("\n--- 🏁 BACKTEST COMPLETE ---")
    
    # Extract the results from the underlying Rust cache
    portfolio = engine.trader.portfolio
    final_balance = portfolio.balance(currency="USD")
    
    print("Starting Balance: $100,000.00")
    print(f"Final Balance:    ${final_balance.free:,.2f}")
    


if __name__ == "__main__":
    main()