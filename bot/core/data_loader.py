import pandas as pd
from datetime import timezone
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.enums import PriceType, AggregationSource
from nautilus_trader.model.objects import Price, Quantity

def load_csv_bars(filepath: str, instrument_id_str: str) -> list[Bar]:
    """
    Loads historical Forex CSV data from HistData into NautilusTrader Bar objects.
    Expects header: timestamp;open;high;low;close;volume
    """
    print(f"Reading file: {filepath}")
    
    # 1. Read the CSV using Pandas (HistData uses semicolons)
    df = pd.read_csv(filepath, sep=';')
    
    # 2. Convert HistData timestamp (e.g., '20240101 000000') to timezone-aware UTC
    df['timestamp'] = pd.to_datetime(df['timestamp'], format='%Y%m%d %H%M%S', utc=True)

    # 3. Define the exact BarType for the engine
    bar_type = BarType.from_str(f"{instrument_id_str}-1-MINUTE-LAST-EXTERNAL")

    bars = []
    
    # 4. Loop through the rows and construct the strict Bar objects
    for _, row in df.iterrows():
        o = f"{row['open']:.5f}"
        h = f"{row['high']:.5f}"
        l = f"{row['low']:.5f}"
        c = f"{row['close']:.5f}"
        
        # HistData volume is often 0, so we enforce a minimum of 1 for the engine
        v = int(row['volume']) if int(row['volume']) > 0 else 1
        
        ts_ns = int(row['timestamp'].timestamp() * 1_000_000_000)

        bar = Bar(
            bar_type=bar_type,
            open=Price.from_str(o),
            high=Price.from_str(h),
            low=Price.from_str(l),
            close=Price.from_str(c),
            volume=Quantity.from_int(v),
            ts_event=ts_ns,
            ts_init=ts_ns
        )
        bars.append(bar)
        
    return bars