# Data policy

The existing Forex CSV/TXT files and `llm_training/` assets predate the Ourbit redesign. They are quarantined legacy artifacts:

- No application, test, research runner, or training script in the current system reads them.
- Their XAU/USD labels and horizons are incompatible with the BTC/ETH event-driven specification.
- New raw Ourbit captures belong under `data/raw/ourbit/` or the configured `recordings/` directory and are ignored by Git.
- Reproducible processed datasets belong under `data/processed/` and must carry source, content-hash, timestamp, fee, quote, and split provenance.

Remove legacy assets only after the repository owner confirms they are no longer needed for archival purposes.

Public BTC candle research inputs live under the ignored `data/research/` directory.
`python -m research.market_data` downloads Binance spot archive candles, verifies
official SHA256 checksums, retains raw archives, and writes normalized one-minute
bars plus a provenance/quality sidecar. These files are explicit price/volume proxy
inputs for the isolated Pillar Two experiment; they are not target-venue executable
bid/ask data and cannot be substituted for the news-labeling or promotion datasets.
