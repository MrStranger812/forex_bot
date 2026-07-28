import sys
import os

# Add the project root to the Python path so the 'bot' module is found
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.identifiers import Venue, AccountId
from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from bot.strategies.moving_average_cross import MovingAverageCross, MovingAverageCrossConfig
from bot.core.data_loader import load_csv_bars

def run_single_backtest(bars, instrument_str: str, fast: int, slow: int) -> float:
    """Runs a single simulation and returns the final balance."""
    engine_config = BacktestEngineConfig(
        trader_id=f"OPT-{fast}-{slow}", 
        logging=LoggingConfig(log_level="ERROR")
    )
    engine = BacktestEngine(config=engine_config)

    engine.add_venue(
        venue=Venue("SIM"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(100_000, USD)],
    )

    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    engine.cache.add_instrument(instrument)
    
    # Load the pre-parsed data into the engine
    engine.add_data(bars)

    # Attach the strategy with the current tweaked parameters
    config = MovingAverageCrossConfig(
        instrument_id=instrument_str,
        fast_period=fast,
        slow_period=slow
    )
    engine.add_strategy(strategy=MovingAverageCross(config=config))

    # Run the simulation
    engine.run()
    
    # FIXED: Retrieve final balance from the engine Cache, not the Trader
    account = engine.cache.account(AccountId("SIM-001"))
    return float(account.balance(USD).free)

def main():
    print("1. Loading historical CSV data into memory...")
    
    # Generate the instrument to extract its immutable ID
    base_instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    instrument_str = str(base_instrument.id)
    
    # FIXED: Using forward slashes to prevent escape sequence warnings
    bars = load_csv_bars("data/historical/DAT_ASCII_EURUSD_M1_202605.csv", instrument_str)
    
    print("2. Starting Optimization Sweep...")
    
    # Define our tweaked parameter combinations
    fast_periods = [5, 10, 15]
    slow_periods = [20, 30, 40, 50]
    
    best_pnl = float('-inf')
    best_params = (0, 0)

    # Loop through every combination
    for fast in fast_periods:
        for slow in slow_periods:
            if fast >= slow:
                continue 
            
            final_balance = run_single_backtest(bars, instrument_str, fast, slow)
            profit = final_balance - 100_000
            
            print(f"Tested MAs ({fast}/{slow}) -> Net Profit: ${profit:,.2f}")
            
            if profit > best_pnl:
                best_pnl = profit
                best_params = (fast, slow)

    print(f"\n🏆 BEST COMBINATION: Fast {best_params[0]} / Slow {best_params[1]}")
    print(f"💰 Max Profit: ${best_pnl:,.2f}")

if __name__ == "__main__":
    main()