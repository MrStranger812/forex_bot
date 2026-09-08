# Pillar Two research without an LLM

This experiment measures the deterministic market model on genuine historical BTC
candles. It is an initial price/volume baseline. It does not measure Ourbit execution
quality or the order-book ensemble described in `UserDoc.md`.

The [first recorded baseline](pillar_two_baseline_results.md) failed after costs.
The [next development study results](pillar_two_experiment_results.md) use the
walk-forward procedure below.

The subsequent [net-outcome study](pillar_two_net_outcomes.md) adds purged labels,
separate model fitting and calibration, and measured taker-volume features.

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
and 0.05% equity risk per trade capped at 1x notional exposure at entry. The assumed costs are
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

## Development walk-forward experiments

```powershell
.venv\Scripts\python.exe -m research.pillar_two_experiments --input data/research/btcusdt_1m_2026_05_08.jsonl --config configs/pillar_two_experiments.yaml --output-dir artifacts/pillar_two_experiments_2026_09_08
```

This study explicitly excludes every bar ending after 2026-07-26 02:23 UTC from
features, execution, and candidate selection. The already observed July–August
test is not reused. The complete input checksum is still recorded for provenance.

Nine hypotheses are registered in the experiment YAML: the original baseline,
trend/breakout without range reversion, longer 15/30/60-minute holding windows,
stricter trend strength, and agreement with completed five-minute trend bars.
The costs, equity risk, drawdown limit, and loss cooldown remain the baseline values.
The longer windows test whether price movement can cover costs; they are hypotheses,
not changes to a live or paper trading configuration.

Thirteen folds each use the previous 21 days for training, a one-hour embargo,
and five later days for validation. Training windows roll and overlap; validation
windows do not overlap. Signals are computed once in chronological order and the
execution loop can query only its latest consumed close. Longer trend/breakout
movement estimates use the same square-root horizon formula as the original engine.
Range-reversion estimates cannot use that rescaling and retain the original horizon.

For each fold, candidate selection requires at least 30 training trades, positive
net PnL, net profit factor of at least 1.1, and no drawdown kill switch. The default
policy also requires at least 30 profitable-in-aggregate training trades under
doubled costs. Selection ranks eligible candidates by the lower of the two training
account returns. When none qualifies, the selected fold holds cash. No validation
result participates in that fold's selection.

All candidate validation results are retained for diagnosis. These exploratory
comparisons are subject to selection bias. A candidate qualifies for a **new**
holdout only with at least 100 validation trades, positive aggregate net PnL,
positive returns in at least 60% of folds, and no halted folds, under both costs.
Qualification does not promote a strategy or establish reliable profitability.

The report separates all-candidate diagnostics from the strategy selected using
training data. It writes a validation trade ledger, frozen configuration and source
hashes, per-fold training/validation metrics, and the qualification outcome.
Accounts and risk state restart at each fold; mean fold return is not a continuous
account return. Flat folds have zero PnL and no defined win rate. Preserving cash
when nothing qualifies is not evidence that a model has an edge.
