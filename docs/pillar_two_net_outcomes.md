# Learning Pillar Two net outcomes

The outcome study learns the net result of a proposed trade. It replaces the
volatility-based movement screen in this research path with an estimate of return
after fees, spread, and slippage. It does not change the paper/live configuration.

The [recorded results](pillar_two_outcome_results.md) found no validated positive
net edge in either the first model or its measured-flow feature iteration.

```powershell
.venv\Scripts\python.exe -m research.pillar_two_outcomes --input data/research/btcusdt_1m_2026_05_08.jsonl --config configs/pillar_two_outcomes.yaml --output-dir artifacts/pillar_two_outcomes_2026_09_08
.venv\Scripts\python.exe -m research.pillar_two_outcomes --input data/research/btcusdt_1m_2026_05_08.jsonl --config configs/pillar_two_outcomes_flow.yaml --output-dir artifacts/pillar_two_outcomes_flow_2026_09_08
```

No LLM, GPU, added dependency, or network call is needed for this run. The input
remains the verified Binance spot candle proxy. The July–August baseline test is
excluded by the explicit development cutoff.

## What is learned

The fixed signal population is the prior 60-minute trend/breakout hypothesis, with
80 bps stop and 120 bps target. Decisions are sampled on a five-minute UTC grid.
This reduces repeated opportunities; labels can still overlap and their raw count
is not an independent sample size. The benchmark uses the same decision schedule.

Each label enters at the next candle opening and follows the same reference-price
stop, target, opening-gap, ambiguity, and time-exit conventions as the backtester.
Fees and adverse execution adjustments are charged on both sides. Labels retain
gross return, base net return, stressed net return, and the time the exit is known.
Unresolved tails or missing data before the exit receive no label. These are
counterfactual one-unit opportunities, not a multi-position account ledger.

Eighteen core features describe only information available at the decision: signal
strength/direction, trend versus breakout, ATR, realized volatility, aligned
returns over several horizons, path efficiency, price deviation, candle position,
relative volume/trade counts, completed five-minute trend agreement, and UTC time.
No spread, order flow, or book features are invented from candles.

The second configuration adds measured taker-flow imbalance over the last one,
five, and fifteen minutes, plus an availability flag. These use recorded taker-buy
base volume from column ten of the original archive, not a guess from candle
direction. The [official Binance schema](https://github.com/binance/binance-public-data)
documents this field. The local flow reader rechecks the saved official archive
checksums and matches each record's OHLCV and trade count to its normalized candle.
Its complete coverage and source hashes are retained in the report. In the first
configuration these four optional feature slots are zero with availability false.
This is aggregated trade flow; executable bid/ask, depth, and quote updates are
still unavailable. The flow addition is a development iteration after observing
the first outcome run, not an independent final test.

A depth-three regression tree partitions earlier fitting outcomes by net return.
Each split considers at most four quantile boundaries per feature and requires
at least 100 fitting observations in a leaf. The structure remains fixed while
later calibration observations estimate each leaf's net expectancy and probability
of a positive base-cost trade. Estimates shrink toward the fitting-period mean
with 40 prior observations. Fewer than 40 calibration observations leave a leaf
unsupported and unable to create entries.

## Chronology and execution

Each of thirteen outer folds has a 21-day training window and a later five-day
validation window. Inside training, the last five days are reserved for threshold
selection, the preceding four for calibration, and the earlier portion for fitting.
A one-hour embargo separates fitting from calibration, calibration from selection,
and selection from validation. Only fully matured labels enter fitting/calibration:
both the actual exit and the full possible holding horizon must precede the cutoff.
This also avoids including only early stops near a boundary.

Six registered thresholds compare predicted net returns above 2 or 5 bps with
minimum win probabilities of 0%, 40%, or 50%. Win rate alone does not determine
entry. Threshold selection runs the actual one-position backtester on its separate
selection period and requires at least ten trades, positive net PnL, profit factor
at least 1.1, and no drawdown halt under both base and doubled costs. Failed selection
holds cash in the subsequent validation window.

The replay explicitly distinguishes a net-return prediction from the old gross
movement heuristic. Net predictions must exceed the safety margin, and execution
still deducts actual modeled costs; fees are not subtracted twice in the entry gate.
For stressed execution, the model uses leaf estimates of stressed net returns.
Its reported win probability continues to mean a positive base-cost outcome.

## Reading the outputs

- `outcomes.jsonl`: decision features, matured labels, and exit availability times.
- `report.json`: per-fold boundaries, model splits/estimates, thresholds, provenance,
  Brier score versus the fitting-period constant predictor, reliability bins,
  return-prediction error, coverage, and execution results.
- `validation_trades.csv`: actual simulated one-position validation trades.

The report separates the benchmark, a fixed diagnostic rule (net prediction above
2 bps), and the rule that passed threshold selection. The diagnostic is evaluated
even when selection rejects trading; it cannot be treated as a promoted strategy.
Accounts restart for each fold, so mean fold return is not continuous account PnL.

At least 100 selected validation trades, positive aggregate net PnL, positive
returns in 60% of folds, and no halted folds are required under both cost scenarios
to qualify for new untouched evaluation. This remains development research after
previous experiments on these dates. Learned probabilities are estimates whose
accuracy must be assessed; fitting a calibration stage does not establish skill.

Target-venue quotes, funding, depth, latency, continuous paper trading, and a new
untouched final period remain separate evidence requirements.
