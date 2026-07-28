import pandas as pd
import os

def verbalize_calendar(input_csv: str, output_txt: str):
    """
    Converts tabular economic calendar data into narrative text 
    that an Uncensored LLM can actually understand and analyze.
    """
    print(f"Loading calendar data from {input_csv}...")
    df = pd.read_csv(input_csv)
    
    # Filter out bank holidays and low impact events to reduce noise
    df = df[df['Impact'] != 'Non-Economic']
    df = df[df['Impact'] != 'Low Impact Expected']
    
    # Focus heavily on USD events, as Gold (XAUUSD) is priced in dollars
    # We also keep EUR, GBP, JPY, CNY as they affect the broader DXY (Dollar Index)
    target_currencies = ['USD', 'CNY', 'EUR', 'GBP', 'JPY']
    df = df[df['Currency'].isin(target_currencies)]
    
    # Sort by date
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values(by=['Date', 'Time'])
    
    os.makedirs(os.path.dirname(output_txt), exist_ok=True)
    
    print("Generating LLM-readable narratives...")
    
    with open(output_txt, 'w', encoding='utf-8') as f:
        # Group by day so the LLM gets a "Daily Briefing"
        for date, group in df.groupby(df['Date'].dt.date):
            daily_summary = f"Macroeconomic Report for {date.strftime('%B %d, %Y')}:\n"
            
            for _, row in group.iterrows():
                currency = row['Currency']
                event = row['Event']
                impact = row['Impact'].replace(" Impact Expected", "")
                
                # Check if we have actual vs forecast data
                actual = str(row['Actual']) if pd.notna(row['Actual']) else "Unknown"
                forecast = str(row['Forecast']) if pd.notna(row['Forecast']) else "Unknown"
                prev = str(row['Previous']) if pd.notna(row['Previous']) else "Unknown"
                
                if actual != "Unknown" and forecast != "Unknown":
                    # Determine if it beat or missed (requires custom logic depending on the metric, 
                    # but we provide the raw text for the LLM to deduce)
                    sentence = (f"At {row['Time']}, the {currency} zone released '{event}'. "
                                f"This was a {impact} impact event. The reported actual figure was {actual}, "
                                f"compared to a forecast of {forecast} (Previous: {prev}).\n")
                else:
                    sentence = (f"At {row['Time']}, a {impact} impact {currency} event occurred: '{event}'.\n")
                
                daily_summary += sentence
            
            daily_summary += "-" * 50 + "\n"
            f.write(daily_summary)

    print(f"✅ Verbalized calendar saved to {output_txt}")
    print("This file can now be fed into the LLM context window alongside actual news articles.")

if __name__ == "__main__":
    # Point this to your actual calendar CSV
    input_file = "data\\Final_Forex_Fast_2007_2026.csv"
    output_file = "data\\llm_training\\verbalized_calendar.txt"
    
    # Mocking execution for safety
    if os.path.exists(input_file):
        verbalize_calendar(input_file, output_file)
    else:
        print(f"⚠️ Could not find {input_file}. Ensure your path is correct.")