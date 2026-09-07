# Pillar Two research without an LLM

This experiment measures the deterministic market model on genuine historical BTC
candles. It is an initial price/volume baseline. It does not measure Ourbit execution
quality or the order-book ensemble described in `UserDoc.md`.

## Run locally

Use the repository virtual environment. Downloading data uses the network; the
subsequent backtest is fully offline. Large datasets and per-trade artifacts are
ignored by Git. The report retains dataset and configuration hashes.

```powershell
.venv\Scripts\python.exe -m research.market_data --start 2026-05-01 --end 2026-09-01 --output data/research/btcusdt_1m_2026_05_08.jsonl
.venv\Scripts\python.exe -m research.pillar_two_backtest --input data/research/btcusdt_1m_2026_05_08.jsonl --config configs/pillar_two_research.yaml --output-dir artifacts/pillar_two_baseline
```

The end date is exclusive. The downloader checks official SHA256 sidecars, retains
the original ZIP files and checksums, validates chronological UTC candles, and writes
`*.jsonl.provenance.json` with coverage and gap counts. The backtest writes
`report.json` and `trades.csv`; neither replay nor report generation calls the network.

The default hypothesis is frozen in `configs/pillar_two_research.yaml`: 60 completed
one-minute bars for warmup, a five-minute holding horizon, 20 bps stop, 30 bps target,
and 0.05% equity risk per trade capped at 1x notional exposure. The assumed costs are
5 bps fee per side, 1 bp full spread, and 1 bp slippage per side. That is approximately
13 bps per round trip, before the 2 bps entry safety margin. These are research
assumptions, not retrieved Ourbit account rates. Doubled-cost results apply higher
costs both to the entry gate and to execution, so the set of trades can change.

## How to read the results

Net return is the change in starting account equity after modeled trading costs.
Win rate counts closed trades with positive net PnL; a zero-net trade is not a win.
Profit factor compares net winning PnL with net losing PnL. Review these together
with drawdown, trade count, expectancy, risk halts, and the win-rate confidence
interval. A high win rate alone does not establish positive expectancy.

The earlier 70% is the development window and the later 30% is the test window,
separated by at least the holding horizon. Each evaluation starts with fresh cash
and no position. Earlier closed bars may warm features without transferring trades
or equity. This is a fixed baseline evaluation, not a trained probability model or
a completed multi-fold walk-forward study. Once test results are viewed, that
period is no longer an untouched test for subsequent strategy revisions.

## Execution and evidence limits

- Signals use completed bars and entries wait until a subsequent opening price.
- Spread and slippage are adverse assumed execution adjustments; fees apply to
  both entry and exit notionals.
- If a candle touches both stop and target, the stop wins. Opening gaps use the
  opening executable price when worse; candle OHLC cannot reveal the exact path.
- Protective stops and holding limits close positions; gaps and end-of-data
  handling are recorded. Simulated stops are not a guarantee of a maximum loss.
- Candle extremes supply a conservative drawdown estimate. There is no measured
  order queue, market impact, quote depth, or sub-minute latency in this dataset.
- Binance spot candles are a reference-price proxy for hypothetical long/short
  research. They are not Ourbit perpetual fills. Funding, short borrow, basis, and
  contract-specific precision still need actual venue data and account metadata.
- Signal strength and expected movement are interpretable hypotheses. Probability
  stays `null`; no heuristic score is described as a calibrated success probability.

## Next evidence gates

1. Review the first fixed baseline, including losing regimes and the cost stress.
2. Collect provenance-preserving BTC trades and executable bid/ask/L2 events from
   an authorized source; measure fees, spread, slippage, and funding for the target
   venue. Keep legacy Forex/XAU data quarantined.
3. Implement synchronized 5-second, 15-second, and one-minute microstructure
   confirmation, then evaluate model changes on development windows only.
4. Fit and validate probability calibration with purged chronological folds and
   reserve new untouched periods for final evaluation.
5. Complete the event replay and continuous shadow/paper stages across enough
   qualified trades and multiple regimes before considering promotion.

The source format and archive checksums are documented in the official
[Binance public-data repository](https://github.com/binance/binance-public-data).
