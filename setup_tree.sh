#!/bin/bash

# Create the main application directories
mkdir -p bot/adapters
mkdir -p bot/core
mkdir -p bot/indicators
mkdir -p bot/strategies
mkdir -p data/historical
mkdir -p scripts
mkdir -p tests

# Create Python __init__ files to make them proper modules
touch bot/__init__.py
touch bot/adapters/__init__.py
touch bot/core/__init__.py
touch bot/indicators/__init__.py
touch bot/strategies/__init__.py

# Create core configuration files
touch bot/core/config.py
touch bot/core/logging.py

# Create skeleton files for our first strategy
touch bot/strategies/moving_average_cross.py

# Create scripts for running different modes
touch scripts/download_data.py
touch scripts/run_backtest.py
touch scripts/run_live.py

# Create standard project files
touch requirements.txt
touch .env.example
touch .gitignore

# Add basic ignores
echo "venv/
__pycache__/
*.pyc
.env
data/historical/*.csv
data/historical/*.parquet" > .gitignore

# Add NautilusTrader dependency
echo "nautilus_trader>=0.55.0
pandas
pyarrow
python-dotenv" > requirements.txt

echo "✅ Project tree successfully created! You can now run 'bash setup_tree.sh' in your terminal."