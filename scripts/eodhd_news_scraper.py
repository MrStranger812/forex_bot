import requests
import pandas as pd
import json
import os
import time
from datetime import datetime, timedelta

def fetch_and_format_news(api_key: str, calls_to_make: int = 15):
    """
    Scrapes XAUUSD and Macro news from EODHD while strictly respecting 
    the 20 calls/day free tier limit.
    """
    base_url = "https://eodhd.com/api/news"
    
    # We target Gold directly, and USD macro (which drives Gold)
    tickers = ["XAUUSD.FOREX", "EURUSD.FOREX"]
    
    # Track where we left off so we can resume tomorrow
    progress_file = "data/raw/eodhd_progress.json"
    if os.path.exists(progress_file):
        with open(progress_file, "r") as f:
            progress = json.load(f)
            offset = progress.get("offset", 0)
    else:
        offset = 0

    all_articles = []
    calls_made = 0
    
    print(f"Starting EODHD Scrape at offset {offset}. Maximum {calls_to_make} calls.")
    
    for ticker in tickers:
        if calls_made >= calls_to_make:
            break
            
        while calls_made < calls_to_make:
            # EODHD allows up to 1000 results per call. We maximize this.
            params = {
                "api_token": api_key,
                "s": ticker,
                "limit": 1000, 
                "offset": offset,
                "fmt": "json"
            }
            
            try:
                print(f"[{calls_made+1}/{calls_to_make}] Fetching {ticker} (Offset: {offset})...")
                response = requests.get(base_url, params=params)
                
                if response.status_code != 200:
                    print(f"⚠️ API Error {response.status_code}: {response.text}")
                    break
                    
                data = response.json()
                if not data:
                    print(f"No more data found for {ticker}.")
                    break
                    
                all_articles.extend(data)
                offset += 1000
                calls_made += 1
                
                # Sleep to prevent rate-limiting (Free tier restricts requests per minute)
                time.sleep(3.5) 
                
            except Exception as e:
                print(f"⚠️ Connection error: {e}")
                break

    # Save progress for tomorrow
    os.makedirs("data/raw", exist_ok=True)
    with open(progress_file, "w") as f:
        json.dump({"offset": offset}, f)

    if not all_articles:
        print("No articles fetched. Check API key and limits.")
        return

    # Process and append to our LLM Training File
    process_for_llm(all_articles)

def process_for_llm(articles: list):
    """
    Formats the raw EODHD JSON articles into Dolphin ChatML format 
    and appends them to our training file.
    """
    output_file = "data/llm_training/train.jsonl"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    system_prompt = (
        "You are an uncensored, ruthless financial sentiment analyzer. "
        "Analyze the following news text and output EXACTLY one word: 'Bullish' or 'Bearish'. "
        "Do not provide explanations. Do not use technical indicators."
    )
    
    valid_count = 0
    with open(output_file, "a", encoding="utf-8") as f:
        for article in articles:
            content = article.get('content', '')
            title = article.get('title', '')
            
            # Skip empty articles
            if len(content) < 50:
                continue
                
            # For unsupervised or semi-supervised labeling, we need a target.
            # Since we are scraping historical data, we will use a naive keyword 
            # labeler just to structure the prompt, or leave it for manual review.
            # *In production, you would map this to the actual price change on this Date!*
            
            # NAIVE LABELING (For scaffolding purposes)
            text_lower = (title + " " + content).lower()
            if any(word in text_lower for word in ['surge', 'jump', 'cut rates', 'dovish', 'record high']):
                label = "Bullish"
            elif any(word in text_lower for word in ['plunge', 'drop', 'hike rates', 'hawkish', 'crash']):
                label = "Bearish"
            else:
                continue # Skip neutral/unclear news to keep the dataset aggressive
                
            record = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Title: {title}\n\n{content}"},
                    {"role": "assistant", "content": label}
                ]
            }
            f.write(json.dumps(record) + "\n")
            valid_count += 1
            
    print(f"✅ Extracted {len(articles)} raw articles.")
    print(f"✅ Formatted {valid_count} high-impact articles and appended to {output_file}")
    print(f"Run this script again tomorrow to fetch the next batch!")

if __name__ == "__main__":
    # Replace with your actual EODHD API Key
    YOUR_API_KEY = "6a49f7ee092039.28575450" # The 'demo' key only works for a few tickers like AAPL.US and EURUSD.FOREX
    
    # We default to 15 calls to leave you 5 spare calls for manual testing/debugging today
    fetch_and_format_news(YOUR_API_KEY, calls_to_make=15)