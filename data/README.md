# Data policy

The existing Forex CSV/TXT files and `llm_training/` assets predate the Ourbit redesign. They are quarantined legacy artifacts:

- No application, test, research runner, or training script in the current system reads them.
- Their XAU/USD labels and horizons are incompatible with the BTC/ETH event-driven specification.
- New raw Ourbit captures belong under `data/raw/ourbit/` or the configured `recordings/` directory and are ignored by Git.
- Reproducible processed datasets belong under `data/processed/` and must carry source, content-hash, timestamp, fee, quote, and split provenance.

Remove legacy assets only after the repository owner confirms they are no longer needed for archival purposes.

Public BTC candle research inputs live under the ignored `data/research/` directory.
`python -m research.market_data` downloads Binance spot or `--market um` futures
archive candles, verifies
official SHA256 checksums, retains raw archives, and writes normalized one-minute
bars plus a provenance/quality sidecar. These files are explicit price/volume proxy
inputs for the isolated Pillar Two experiment; they are not target-venue executable
bid/ask data and cannot be substituted for the news-labeling or promotion datasets.

`research.archived_flow` can recover the recorded taker-buy volume from the saved
official candle archives without changing the original normalized dataset. It
reverifies each checksum and matches OHLCV/trade counts before exposing flow at
the candle close. This measured aggregate volume is not a reconstructed L2 book
or an executable bid/ask stream.

`research.futures_trades` retains verified individual USDT-M trade ZIPs and
audits their five-second aggregates against independent minute OHLCV, counts,
and taker-buy volume. Numeric ID gaps remain in the report; no missing trades
or quotes are invented. The July 20 sample contains 3,998,559 trades but is not
large enough for model validation. See [acquisition and fee evidence](../docs/ourbit_data_and_costs.md).
