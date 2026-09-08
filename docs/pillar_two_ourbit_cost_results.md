# Pillar Two validation with published Ourbit fees

Run on 2026-09-08. **No model passes the existing selection/promotion rules.**
Lower fees improve the benchmark but do not establish a profitable edge.

Four experiments retain the same model, thresholds, risk budget, 60-minute
trend/breakout candidate, five-minute decision grid, and thirteen development
folds. Only costs and source market change. Fitting, calibration, threshold
selection, and subsequent validation remain separate, with horizon purging and
one-hour embargoes. Both markets use May 1 through July 26 02:23 UTC. These
reused development periods are not a fresh holdout; the later baseline test
was not scored again.

Spot produces 4,892 opportunities and USDT-M 4,639. Both use actual taker-buy
volume from their own verified candle archives. No LLM or GPU is used. See
[fee evidence and limitations](ourbit_data_and_costs.md).

## Matching benchmark

Mean fold returns below refer to thirteen separate five-day accounts, each
starting with 10,000 USDT. They are not cumulative returns on one account.

| Market / modeled costs | Trades | Net win rate | Profit factor | Mean fold return |
|---|---:|---:|---:|---:|
| Previous spot, 13 bps | 966 | 34.78% | 0.503 | -0.464% |
| Spot, Ourbit fees only, 8 bps | 1,036 | 40.25% | 0.662 | -0.303% |
| Spot, fees + execution assumptions, 11 bps | 996 | 37.25% | 0.577 | -0.384% |
| USDT-M, Ourbit fees only, 8 bps | 1,047 | 38.49% | 0.634 | -0.340% |
| USDT-M, fees + execution assumptions, 11 bps | 1,019 | 35.23% | 0.543 | -0.441% |

Costs affect entry gating and sizing, so these are not identical trades with
fees subtracted afterward. Each new benchmark has only one positive base-cost
fold. All doubled-cost benchmarks remain negative.

## Outcome-model diagnostic and selection

The diagnostic uses the previously registered predicted-net threshold above
2 bps. It is distinct from the strategy selected using earlier data.

| Market / costs | Trades | Net win rate | Profit factor | Average net trade return* |
|---|---:|---:|---:|---:|
| Spot, fees only | 48 | 35.42% | 0.477 | -0.0721% |
| Spot, fees + execution assumptions | 0 | undefined | undefined | undefined |
| USDT-M, fees only | 30 | 33.33% | 0.880 | -0.0226% |
| USDT-M, fees + execution assumptions | 30 | 33.33% | 0.748 | -0.0527% |

*Average return on each trade's entry notional, not account return.*

The 30 USDT-M trades all occur in one fold, July 1 01:00 to July 6 01:00 UTC.
With fees alone they earn 9.80 USDT gross and pay 13.65 USDT fees:
**-3.85 USDT net**, or **-0.0385%** of that fold's starting account. Including
execution assumptions gives **-8.66 USDT**, or **-0.0866%** of that account.
The small loss does not establish an almost-profitable validated strategy:
the sample is small, concentrated in one fold, and fails earlier selection.

The actual selector chooses **cash in all thirteen folds in all four studies**.
Every doubled-cost diagnostic also abstains. No new final holdout is warranted.

Probability estimates still perform worse than their fitting-period constant:

| Study | Weighted Brier score | Constant benchmark |
|---|---:|---:|
| Spot, fees only | 0.23692 | 0.23152 |
| Spot, fees + execution assumptions | 0.22663 | 0.22115 |
| USDT-M, fees only | 0.23512 | 0.23116 |
| USDT-M, fees + execution assumptions | 0.22663 | 0.22220 |

Lower Brier is better. Scores are weighted by validation-label counts;
overlapping labels and dependent trades are not independent samples.

## Reproduce

From the repository root:

```powershell
python -m research.pillar_two_outcomes --input data/research/btcusdt_1m_2026_05_08.jsonl --config configs/pillar_two_outcomes_ourbit_fees.yaml --output-dir artifacts/pillar_two_ourbit_fees_spot_new
python -m research.pillar_two_outcomes --input data/research/btcusdt_1m_2026_05_08.jsonl --config configs/pillar_two_outcomes_ourbit_costs.yaml --output-dir artifacts/pillar_two_ourbit_costs_spot_new
python -m research.pillar_two_outcomes --input data/research/btcusdt_um_1m_2026_05_07_26.jsonl --config configs/pillar_two_outcomes_ourbit_fees.yaml --output-dir artifacts/pillar_two_ourbit_fees_um_new
python -m research.pillar_two_outcomes --input data/research/btcusdt_um_1m_2026_05_07_26.jsonl --config configs/pillar_two_outcomes_ourbit_costs.yaml --output-dir artifacts/pillar_two_ourbit_costs_um_new
```

Recorded directories: `artifacts/pillar_two_ourbit_{fees,costs}_{spot,um}_2026_09_08/`.
Each retains reports, models, labels, trade CSV, configurations, source hashes,
and market-data provenance. All 24 strategy/scenario ledger summaries reconcile
to their CSVs; source hashes and fold boundaries were checked. The USDT-M
candle SHA256 is
`3509c3b0167d733b65b603528d75b7b567695c5802bca8969903a516eebe4db7`.

The next bottleneck is a better predictive signal and executable quote evidence,
not relaxing the failed selection rules.
