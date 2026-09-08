# Pillar Two development experiments — 2026-09-08

Nine predeclared variants completed thirteen chronological folds. **None qualified
for a new holdout.** Every base-cost candidate lost money in aggregate, and none
had a positive base-cost validation fold. The training-selected strategy held cash
in all thirteen folds: zero trades, zero PnL, and an undefined win rate. This is
successful rejection of unsupported entries, not evidence of a profitable model.

Only the original development prefix was used: 123,983 candles ending no later
than 2026-07-26 02:23 UTC. Validation ran in disjoint five-day windows from May 22
01:00 through July 26 01:00 UTC. Each fold trained on the prior 21 days with a
one-hour embargo. No July–August baseline test bars entered this study.

## Results on the same development validation windows

Mean return below is the average **five-day fold account return**, not a continuous
account return. Every fold began independently with 10,000 USDT. Costs, risk per
entry, loss cooldown, and risk halts were retained from the original baseline.

| Variant | Base trades | Base net win rate | Base mean fold return | Doubled-cost mean fold return |
| --- | ---: | ---: | ---: | ---: |
| Original 5-minute ensemble | 1,352 | 22.26% | -2.075% | -0.933% |
| Trend/breakout, 5 minutes | 1,162 | 22.72% | -1.787% | -0.832% |
| Trend/breakout, 15 minutes | 1,690 | 24.73% | -1.669% | -1.042% |
| Trend/breakout, 30 minutes | 1,604 | 26.56% | -1.170% | -1.048% |
| Trend/breakout, 60 minutes | 1,173 | 32.65% | -0.613% | -0.839% |
| Strict trend, 30 minutes | 758 | 27.97% | -0.500% | -0.447% |
| Strict trend, 60 minutes | 701 | 32.24% | -0.366% | -0.467% |
| Strict trend + five-minute confirmation, 30 minutes | 233 | 28.76% | -0.171% | -0.180% |
| Strict trend + five-minute confirmation, 60 minutes | 225 | 29.78% | -0.133% | -0.178% |

The stressed cost gate rejects more signals and changes the trade population.
Its smaller aggregate loss for some variants does not mean higher fees help.
All-candidate comparisons are exploratory; selecting their best-looking row is
not an out-of-sample profitability claim.

## What this establishes

The highest win rate came from the 60-minute trend/breakout variant, but its net
profit factor was only 0.471. It made +20.77 USDT gross across independent fold
accounts, against 817.28 in fees and execution costs, for -796.51 net. The
confirmed 60-minute variant had the smallest average account loss, but also
traded far less and made -15.89 gross before its 157.14 costs.

Average net trade returns stayed near minus 13 basis points, close to the assumed
round-trip friction. This is consistent with weak gross directional expectancy.
Lower trading frequency and smaller positions explain much of the smaller account
loss; the study does not establish that the signal improved enough to trade.

The expected-movement gate estimates a volatility scale, not the conditional net
return of taking the predicted direction. Longer horizons increase that heuristic
and can admit more trades. That does not by itself create a directional edge.

## Next implementation milestone

1. Build trade-outcome labels using the same next-open entry, costs, stop/target,
   and holding rules. Retain decision-time features and the time each label becomes
   knowable; purge unfinished outcomes at every training boundary.
2. Fit a small deterministic model of net outcomes and calibrate its probabilities
   on separate chronological development windows. Compare against no-trade and
   unconditional outcomes; report calibration, coverage, and net expectancy.
3. Add actual trades and executable bid/ask data so spread, aggressive flow, and
   liquidity can be measured. Candle-derived order-book features would be invented
   evidence and must not enter the model.
4. Reserve fresh data for a model that first passes development and stressed-cost
   gates. No new final holdout was consumed in this study. Keep the paper/live
   strategy unchanged until evidence justifies a deliberate promotion.

The first two steps can proceed locally without an LLM or GPU rental. This study
does not complete the microstructure, probability-calibration, or continuous
paper milestones in `UserDoc.md`.

## Reproduction and validation

Use the [walk-forward runbook](pillar_two_backtesting.md#development-walk-forward-experiments)
and `configs/pillar_two_experiments.yaml`.

- Full report: `artifacts/pillar_two_experiments_2026_09_08/report.json`
- Trade ledger: `artifacts/pillar_two_experiments_2026_09_08/validation_trades.csv`
- Experiment SHA256: `8e400303821a60f8530e100471e8c729621c8cc51838e9901d845a129b13d125`
- Input SHA256: `54f0a83179fd3ef17a0b954760435db5211693f352ba8a021b43907c1bc229b7`
- Full suite: 264 passed; Ruff and strict mypy passed.
- All 18 candidate/scenario trade ledgers reconcile with reported PnL; validation
  exits remain within their folds and recorded source hashes match the executed code.
- Tests cover future-price exclusion, causal signal caching, incomplete higher
  timeframe bars, gap handling, horizon rescaling, training-only selection, minimum
  samples, stress rejection, and holding-horizon embargoes.
