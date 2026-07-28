🤖 AI Agent Context & Instructions

Project Name: Hybrid HFT MT4 Orchestrator
Core Framework: NautilusTrader (Python/Rust) + MetaTrader 4 (ZMQ)
AI Stack: Local Fine-tuned Dolphin Llama-3 (8B) via FastAPI + PEFT/LoRA

🏗️ 1. Project Architecture Overview

This is an institutional-grade, hybrid algorithmic trading system for XAU/USD (Gold). It strictly separates Machine Learning / Heavy Computation from the lightning-fast Execution Engine.

It is built on a Two-Pillar System:

Pillar 1 (The Brain / Macro): A local, uncensored LLM fine-tuned on historical ground-truth economic data. It runs as a standalone FastAPI server and outputs a macro directional bias (Bullish, Bearish, Neutral).

Pillar 2 (The Trigger / Micro): A Rule-Based Regime Engine running inside NautilusTrader on a 15-minute timeframe. It uses EMA crosses for trend alignment and ATR for volatility/stop-loss calculations.

Execution: NautilusTrader does NOT connect directly to a standard broker API. It connects to a MetaTrader 4 (MT4) terminal using a custom ZeroMQ (ZMQ) Bridge.

🎯 2. Current Primary Objectives

Whenever assisting with this project, prioritize the following goals:

Pillar Integration: Ensure seamless, non-blocking communication between the NautilusTrader strategy (bot/strategies/hybrid_regime_orchestrator.py) and the LLM API Server (scripts/llm_api_server.py). The trading thread must never be blocked by API latency.

MT4 ZeroMQ Bridge: Build and stabilize the connection between NautilusTrader and MT4.

Python Side: A custom NautilusTrader Execution/Data Client using pyzmq.

MQL4 Side: An Expert Advisor (EA) running on the MT4 terminal listening on a local port (e.g., TCP 5555 or 5556) to execute trades and stream tick data.

📜 3. Strict Coding Directives

A. Separation of Concerns (CRITICAL)

Execution vs. AI: Never put heavy ML imports (PyTorch, Transformers, Pandas) inside the bot/ directory. The bot/ directory is strictly for NautilusTrader execution logic. All AI and data processing lives in scripts/.

Non-Blocking Logic: NautilusTrader relies on high-speed event loops. Any external API calls (like pinging the LLM) must be handled asynchronously or with strict, minimal timeouts (e.g., timeout=2.0 max).

B. The ZMQ MT4 Bridge Protocol

When writing code for the MT4 Bridge, adhere to these patterns:

Order Execution (REQ/REP): Python sends an execution string (e.g., BUY|XAUUSD|1.0). MT4 receives it, executes via OrderSend(), and replies with the TicketID or an error.

Market Data (PUB/SUB): MT4 publishes live tick data. Python subscribes and ingests it into NautilusTrader's Cache.

Resiliency: ZMQ connections can drop. Always include timeout handling, reconnection logic, and heartbeat pings.

C. Trading Logic Rules

The Matrix Check: A trade is ONLY executed if Pillar 1 (Macro Bias) and Pillar 2 (Micro Trend) perfectly align.

Risk Management: Always utilize dynamic stop losses based on ATR (Average True Range) to account for current market volatility. Do not use static pip stops.

📂 4. Project Structure Reference

bot/adapters/: Custom ZMQ clients for MT4.

bot/strategies/: Nautilus strategy files (e.g., hybrid_regime_orchestrator.py).

bot/core/: Utility files (Market sessions, risk management).

scripts/: LLM API servers, data labelers, EODHD scrapers.

data/: Historical MT4 CSVs and LLM jsonl training data.

models/: Saved LoRA adapters for the Dolphin LLM.

🛠️ 5. Interaction Guidelines for the AI

Do not suggest deep learning for OHLCV data. We have proven it fails. Stick to the Rule-Based engine for pricing data (Pillar 2).

Keep NautilusTrader imports up-to-date (using version 0.100.0+ patterns).

When writing MQL4 code for the ZMQ bridge, ensure strict syntax compatibility with standard MetaTrader 4 compilers. 