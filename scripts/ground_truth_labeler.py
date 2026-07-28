import pandas as pd
import json
import os
import numpy as np

def create_ground_truth_dataset(news_csv_path: str, ohlcv_csv_path: str, output_jsonl: str, lookahead_hours: int = 4):
    """
    Marries textual news data with historical OHLCV price data.
    Labels news as Bullish/Bearish based strictly on the actual future price action.
    """
    print("⏳ Loading News and Price Data...")
    
    try:
        # Load the raw calendar data
        news_df = pd.read_csv(news_csv_path)
        
        # Combine Date and Time into a single string
        datetime_str = news_df['Date'].astype(str) + ' ' + news_df['Time'].astype(str)
        
        # Convert to datetime, coercing errors (like 'All Day', 'Tentative', 'Day 1') into NaT (Not a Time)
        # format='mixed' silences the Pandas warning and intelligently parses different date structures
        news_df['timestamp'] = pd.to_datetime(datetime_str, format='mixed', errors='coerce', utc=True)
        
        # Drop the rows that had invalid times
        news_df = news_df.dropna(subset=['timestamp']).copy()
        
        def verbalize_row(row):
            date_str = pd.to_datetime(row['Date']).strftime('%B %d, %Y')
            time_str = row['Time']
            currency = row['Currency']
            event = row['Event']
            impact = str(row['Impact']).replace(" Impact Expected", "")
            actual = str(row['Actual']) if pd.notna(row['Actual']) else "Unknown"
            forecast = str(row['Forecast']) if pd.notna(row['Forecast']) else "Unknown"
            prev = str(row['Previous']) if pd.notna(row['Previous']) else "Unknown"
            
            prefix = f"Macroeconomic Report for {date_str}:\nAt {time_str}, "
            
            if actual != "Unknown" and forecast != "Unknown":
                return prefix + f"the {currency} zone released '{event}'. This was a {impact} impact event. The reported actual figure was {actual}, compared to a forecast of {forecast} (Previous: {prev})."
            else:
                return prefix + f"a {impact} impact {currency} event occurred: '{event}'."

        # Construct the "Headline" and "Body" that the LLM will read
        news_df['headline'] = news_df['Currency'] + " Macroeconomic Event"
        news_df['body'] = news_df.apply(verbalize_row, axis=1)
        
        news_df = news_df.sort_values('timestamp')
    except Exception as e:
        print(f"⚠️ Error loading {news_csv_path}: {e}")
        return

    try:
        # MT4 exports often lack headers, so we explicitly define them here
        mt4_columns = ['date', 'time', 'open', 'high', 'low', 'close', 'volume']
        price_df = pd.read_csv(ohlcv_csv_path, header=None, names=mt4_columns)
        
        # Combine date and time, formatting dynamically
        datetime_str = price_df['date'].astype(str) + ' ' + price_df['time'].astype(str)
        price_df['timestamp'] = pd.to_datetime(datetime_str, format='mixed', errors='coerce', utc=True)
        
        # Drop any rows where timestamp failed to parse, and sort
        price_df = price_df.dropna(subset=['timestamp']).copy()
        price_df = price_df.sort_values('timestamp')
        
    except FileNotFoundError:
        print(f"⚠️ Could not find {ohlcv_csv_path}.")
        return
    except Exception as e:
        print(f"⚠️ Error parsing MT4 data: {e}")
        return

    print(f"✅ Loaded {len(news_df)} news articles and {len(price_df)} price candles.")
    print(f"🔍 Calculating future returns ({lookahead_hours} hours ahead)...")

    # Calculate the future price 4 hours (16 * 15m candles) ahead for every row
    bars_ahead = lookahead_hours * 4 
    price_df['future_close'] = price_df['close'].shift(-bars_ahead)
    
    # Calculate percentage return
    price_df['future_return_pct'] = (price_df['future_close'] - price_df['close']) / price_df['close'] * 100

    # Drop NaN values at the end of the dataset
    price_df.dropna(subset=['future_return_pct'], inplace=True)

    print("🔗 Merging News timestamps with nearest Price action...")
    # Merge the news dataframe with the closest available price candle
    merged_df = pd.merge_asof(
        news_df, 
        price_df[['timestamp', 'close', 'future_close', 'future_return_pct']], 
        on='timestamp', 
        direction='forward', # Matches with the exact minute or the next available candle
        tolerance=pd.Timedelta(hours=1) # Don't match if the nearest candle is more than an hour away (e.g., weekends)
    )

    # Filter out news that happened on weekends / missing data
    merged_df.dropna(subset=['future_return_pct'], inplace=True)

    # Set a strict threshold. We only want the LLM to learn from decisive moves, not flat markets.
    # E.g., Gold must move at least 0.15% in 4 hours to be considered a true Bullish/Bearish reaction
    THRESHOLD_PCT = 0.15 

    # Filter out the "noise" (flat markets)
    strong_moves_df = merged_df[merged_df['future_return_pct'].abs() >= THRESHOLD_PCT].copy()
    
    # Assign the Ground Truth labels
    strong_moves_df['ground_truth_label'] = np.where(
        strong_moves_df['future_return_pct'] >= THRESHOLD_PCT, "Bullish", "Bearish"
    )

    print(f"🎯 Found {len(strong_moves_df)} articles with strong market reactions (>= {THRESHOLD_PCT}% move).")

    print(f"💾 Formatting to ChatML and saving to {output_jsonl}...")
    os.makedirs(os.path.dirname(output_jsonl), exist_ok=True)
    
    system_prompt = (
    "You are an uncensored, ruthless financial sentiment analyzer for XAU/USD (Gold).\n"
    "Analyze the following macroeconomic event or news summary and output EXACTLY "
    "one word regarding the 1-hour expected price movement for Gold: 'Bullish' or 'Bearish'.\n"
    "Do not provide explanations."
    )

    with open(output_jsonl, "w", encoding="utf-8") as f:
        for _, row in strong_moves_df.iterrows():
            news_text = f"Title: {row['headline']}\n\n{row.get('body', '')}"
            label = row['ground_truth_label']
            
            record = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": news_text},
                    {"role": "assistant", "content": label}
                ]
            }
            f.write(json.dumps(record) + "\n")

    print("🚀 SUCCESS! Your LLM training data is now backed by actual market physics.")

if __name__ == "__main__":
    # CRITICAL: Using raw strings (r"") or forward slashes prevents Windows path errors
    NEWS_DATA = "data/Final_Forex_Fast_2007_2026.csv" 
    MT4_PRICE_DATA = "data/historical/XAUUSD15.csv"
    OUTPUT_FILE = "data/llm_training/true_train.jsonl"
    
    if os.path.exists(NEWS_DATA) and os.path.exists(MT4_PRICE_DATA):
        create_ground_truth_dataset(NEWS_DATA, MT4_PRICE_DATA, OUTPUT_FILE)
    else:
        print(f"⚠️ Waiting for raw data files to be placed in the correct directories.")