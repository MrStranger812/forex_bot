# Net-outcome learning results — 2026-09-08

**No positive net trading edge was established.** The outcome model completed
thirteen development folds. The original price/volume feature run and the added
measured taker-flow run both rejected all proposed entries. Neither a fixed
diagnostic threshold nor the selected strategy executed a trade. Their zero return
is abstention, not profitable trade performance.

The study built 4,892 fully resolved counterfactual opportunities from 123,983
development candles. It retained known-at timestamps and purged full holding
horizons at fitting/calibration boundaries. Thirteen disjoint validation periods
contained 3,706 labeled opportunities; their overlapping horizons mean this count
must not be treated as independent observations.

## Comparison within this study

The benchmark retains the 60-minute trend/breakout signal, but uses the same
five-minute decision grid as the learned model. These figures therefore differ
from the previous every-minute hypothesis study. Each five-day fold starts with
an independent 10,000 USDT account.

| Strategy | Base trades | Base net win rate | Mean five-day fold return | Doubled-cost mean fold return |
| --- | ---: | ---: | ---: | ---: |
| Matching trend/breakout benchmark | 966 | 34.78% | -0.464% | -0.643% |
| Outcome model, fixed diagnostic gate | 0 | Undefined | 0% | 0% |
| Outcome model, threshold selected on earlier data | 0 | Undefined | 0% | 0% |

The benchmark made +70.99 USDT gross across independent fold accounts but paid
673.74 in modeled fees and execution costs, leaving -602.75 net. The model's
abstention avoided that simulated loss without demonstrating an ability to choose
profitable trades.

## Did the model learn predictive information?

| Validation metric | Outcome tree | Constant predictor from fitting period |
| --- | ---: | ---: |
| Brier score, lower is better | 0.21866 | 0.21401 |
| Net-return mean absolute error | 30.892 bps | 30.546 bps |

The tree was worse on both measures. Its weighted calibration error was 0.0624.
Only 2,631 validation opportunities mapped to leaves with sufficient calibration
support. No supported opportunity had a positive predicted net return; supported
leaf means ranged from -26.84 to -0.99 bps. This explains the zero-trade result
before threshold selection, rather than attributing it only to selection strictness.

The second run recovered measured taker-buy volume for every development candle
from the retained official archives. The reader reverified checksums and exact
OHLCV/trade-count matches. The derived one-, five-, and fifteen-minute flow features
varied normally, but none was selected by the fitted tree in any fold. Consequently,
both feature runs produced identical tree predictions and execution results. This
does not establish that trade flow is useless; this particular model, signal
population, sampling frequency, and dataset did not extract additional skill.

## What changed in the project

- Reusable causal opportunity features and cost-adjusted outcome labels with
  explicit label availability and full-horizon purge rules.
- An interpretable regression tree with separate later leaf calibration,
  shrinkage, and minimum sample support.
- Separate threshold-selection replay before each validation window, under both
  cost scenarios, with a constant-predictor accuracy benchmark.
- Explicit net-return forecasts in research signals. The entry gate compares a
  net forecast with the safety margin, avoiding double-counted costs; simulated
  fills continue to pay fees, spread, and slippage.
- A source-verified archive flow reader, reusable for subsequent signal research.

## Next evidence needed

The next hypothesis should change the information or signal-generation process:
for example, short-horizon trade-flow exhaustion or continuation using individual
trades and executable quotes. Measure target-venue costs and latency alongside
that data, then test whether the available gross return can cover those costs.
Retain this learned outcome gate and fixed benchmark to evaluate the new signal
population. Merely fitting a more complicated model to these same opportunities
does not yet have supporting evidence.

The existing July–August test was excluded, and no fresh final holdout was consumed.
Paper/live configuration remains unchanged. There is no basis here for claiming
increased positive net trade outcomes or promoting this model.

## Artifacts and validation

Commands and methodology: [net-outcome runbook](pillar_two_net_outcomes.md).

- First outcome run: `artifacts/pillar_two_outcomes_2026_09_08/report.json`
- Flow run: `artifacts/pillar_two_outcomes_flow_2026_09_08/report.json`
- Each directory contains `outcomes.jsonl` and `validation_trades.csv`, with the
  complete models, configuration/source hashes, and split dates in its report.
- Flow configuration SHA256:
  `632250b4759577ab01f0db841aab0283050385bda4bd33d2e3f93eff5721ee41`
- Validation: 291 tests passed, Ruff passed, strict mypy passed. Label chronology,
  artifact hashes, validation exit boundaries, and all execution ledgers reconciled.

The first outcome report retains the source hashes from before the additional
flow-feature implementation. Its run was not overwritten or retroactively relabeled
as a flow experiment.
