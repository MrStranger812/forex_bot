# Pillar Two signal design research

Run date: 2026-09-08. Offline BTC USDT-M research without an LLM.

**No profitable design was validated.** Seventeen active candidates lost under
all three cost scenarios; two basis candidates made no trades. The selector using
only earlier data chose cash in all 13 folds. No runtime model was promoted.

## Evidence and hypotheses

The [primary-source boundary](references.md#signal-research-sources) distinguishes
contemporaneous price impact from future-return prediction. Relative imbalance can
be extreme on tiny volume, so the study compares activity-conditioned momentum,
relative flow, and flow magnitude. Session momentum research motivates context
filters but does not establish the project's rolling 30-minute specification.
Failed pressure, failed breakouts, VWAP reversion, and unhedged basis reversion are
explicit new hypotheses, not published profitable strategy replications.

The fixed registration is `configs/pillar_two_signal_designs.yaml`:

| Family | Trigger from completed observations |
|---|---|
| Activity momentum | 30-minute move >= 1 ATR; 5-minute and spot 30-minute direction agree; volume and trade-count activity >= 1.25 times their baseline. |
| Relative-flow momentum | Activity momentum plus aligned signed/total five-minute volume >= 0.20. |
| Flow-magnitude momentum | Activity momentum plus aligned signed five-minute volume / preceding expected five-minute volume >= 0.50. |
| Failed pressure | Activity and flow magnitude are high, but five-minute progress <= 0.25 ATR and the final minute turns against flow. |
| Failed breakout | A prior 60-minute extreme is breached and the candle closes inside, near the rejecting end of its range; high activity required. Double-sided sweeps abstain. |
| VWAP reversion | Low 60-minute efficiency, >= 2 standard deviations from a preceding close-volume-weighted reference, and a completed turn toward it. |
| Basis reversion | Futures/spot log-basis deviation >= 2 prior 240-minute standard deviations and starts shrinking; 0.5 bp standard-deviation floor. |

Direction rules are mirrored for long and short. Signed volume is twice recorded
taker-buy base volume minus total base volume. The activity baseline excludes the
five-minute signal window; the VWAP and basis baselines exclude the current candle.
The VWAP reference is a candle proxy. Missing paired observations reset warmup.

Independent specialists, a fixed regime router, and consensus each receive two
exit presets: 30-minute maximum hold with 60/90 bp stop/target, and 60 minutes with
80/120 bp stop/target. The previous 60-minute trend/breakout baseline is retained
as a control, giving 19 candidates. Decisions are scheduled every five minutes.

The router uses flow-magnitude momentum at efficiency >= 0.35; efficiency <= 0.25
uses failed breakout, then VWAP reversion, then failed pressure. Intermediate
regimes abstain. Consensus requires two agreeing mechanisms and no opposing vote;
the nested momentum variants count as one. Movement budgets remain uncalibrated
volatility heuristics, capped by reference distance for reversion candidates.
Composition preserves those caps. Basis PnL comes only from futures fills; it is
not a hedged convergence portfolio.

## Data and execution

The study pairs 123,983 Binance USDT-M minute candles with exactly synchronized
spot candles, from May 1 through July 26 at 02:23 UTC, 2026. It creates 24,748
feature snapshots after warmup. Taker-buy volume comes from verified original
archives. The separate one-day individual-trade audit is not used in this run.

Thirteen chronological folds each use 21 training days, a one-hour embargo, and
five validation days. The last validation ends July 26 at 01:00 UTC. These dates
have been used for prior project research: the comparison is exploratory and no
new holdout is scored. Parameters were fixed before this run's results were read.

The shared candle executor applies next-open entry, conservative stop handling,
protective exits, cooldowns, risk sizing, and a 1x entry exposure cap. Each fold
starts a separate 10,000 USDT account. Mean fold return is not a compounded return
on a continuous account. Trade return percentages use entry notional.

| Scenario | Assumption |
|---|---|
| Fees only | Published Ourbit 4 bp taker reference per fill; approximately 8 bp round trip. |
| Base | Same fees plus assumed 1 bp full spread and 1 bp slippage per side; approximately 11 bp round trip. |
| Stress | Double all base costs; approximately 22 bp round trip. |

The fee schedule remains unverified for the actual account. Funding, executable
quotes, depth impact, queue position, latency, and exchange lot-size execution
are absent. Binance data are an explicit proxy, not Ourbit execution evidence.
See [data acquisition and fees](ourbit_data_and_costs.md).

## Results

Selected 60-minute comparisons under base costs:

| Design | Trades | Net win rate | Profit factor | Mean fold return | Average trade return |
|---|---:|---:|---:|---:|---:|
| Previous baseline | 1,019 | 35.23% | 0.543 | -0.4407% | -0.1025% |
| Flow-magnitude momentum | 693 | 36.22% | 0.545 | -0.2902% | -0.0992% |
| Failed-breakout reversal | 192 | 39.58% | 0.611 | -0.0678% | -0.0835% |
| VWAP reversion | 315 | 39.68% | 0.596 | -0.1075% | -0.0808% |
| Regime router | 466 | 37.77% | 0.509 | -0.2134% | -0.1085% |
| Consensus | 34 | 32.35% | 0.602 | -0.0133% | -0.0924% |

Failed-breakout reversal has the highest base profit factor among new designs
with at least 100 trades. Across independent fold accounts, it earns 27.87 USDT
gross, pays 84.34 in fees and 31.63 in assumed execution costs, and loses 88.09 net.
Its fees-only profit factor is 0.737; stress is 0.365. This is still negative
expectancy. Consensus's smaller total loss is accompanied by much lower activity.

Basis dislocations do generate hypotheses, but fail the movement-versus-cost
gate. Zero trades do not establish a profitable strategy. The earlier-data
selector requires >= 30 training trades, positive net PnL, base profit factor
>= 1.1, no kill switch, and positive stressed results with >= 30 trades. Every
fold falls back to cash. No fixed candidate passes the validation/stress gates.

A seeded circular bootstrap uses 2,000 draws with common indices in two-fold
blocks. The failed-breakout 60-minute descriptive 95% interval for mean fold
return is [-0.123%, -0.004%]. The centered maximum-mean diagnostic over all 19
candidates has tail fraction 1.0 in each cost scenario. This is not exact White
Reality Check or PBO, and cannot adjust for previous project trials. Thirteen
folds and reused data limit inference; candidate returns are correlated.

## Reproduce and inspect

Acquire the input files and original archive caches using the commands in
[the data runbook](ourbit_data_and_costs.md). Run from the repository root:

```powershell
.venv\Scripts\python.exe -m research.signal_design_study `
  --futures data/research/btcusdt_um_1m_2026_05_07_26.jsonl `
  --spot data/research/btcusdt_1m_2026_05_08.jsonl `
  --config configs/pillar_two_signal_designs.yaml `
  --output-dir artifacts/pillar_two_signal_designs_rerun
```

The output directory must not exist. Recorded results are locally available at
`artifacts/pillar_two_signal_designs_2026_09_08/`:

- `registration.json`: complete candidate/configuration registration, input
  provenance, and source SHA256 values written before evaluation. This is a local
  audit trail, not an externally timestamped preregistration.
- `report.json`: per-fold training, selection, validation, summaries, diagnostics,
  and limitations.
- `comparison.csv`: all 19 candidates plus the selector across three scenarios.
- `validation_trades.csv`: all candidate/scenario fill ledgers with fold labels.

All 57 candidate/scenario ledgers reconcile gross PnL, costs, and net PnL;
entries/exits fit validation boundaries and source hashes match the registration.
The nine new tests cover causal feature prefixes, paired-data gaps, mirrored
rules, composition, movement caps, next-open fills, and bootstrap reproducibility.
The full suite passes 315 tests; Ruff and strict mypy pass.

## Next experiment boundary

Preserve the full failed search and fresh future data. Extend audited trades and
collect synchronized quote/depth history with exchange and receive timestamps.
Then test whether rejected breakouts coincide with measurable replenishment,
diminishing aggressive flow, or a reference-market move. These minute-candle
results do not justify further threshold tuning, more model complexity, or live
promotion without a new observable predictive feature.
