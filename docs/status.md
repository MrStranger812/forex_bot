# Implementation status

Updated: 2026-09-08

## Implemented foundation

- Phase 0 scope, invariants, live interlock, and operations specification
- Phase 1 package layout, environment-only secrets, modern Python packaging
- Exchange-neutral domain models and configurable Ourbit adapter foundation
- Sequenced L2 book and deterministic raw-event/replay formats
- Strict Pillar 1 schema/deduplication and bounded LLM client
- Interpretable Pillar 2 score and two-pillar gate
- Decimal sizing, limits, kill switch, paper exchange, and reconciliation state machine
- Walk-forward/data-split/stress scenario utilities and baseline automated tests
- 291 passing unit, integration, replay, and deterministic chaos checks; Ruff and strict mypy clean

## Pillar Two research milestone

- Implemented the closed-bar regime engine with trend, range-reversion, and
  persistent breakout hypotheses, warmup, gap resets, and expiring signals.
- Added optional shared-engine integration to `PaperTradingEngine`; `run_paper`
  initializes it from `configs/paper.yaml`. The existing two-pillar execution gate
  still requires news. The standalone research runner evaluates Pillar Two alone.
- Added next-bar candle execution, explicit two-sided costs, stops/targets/time
  exits, 1x exposure, equity-risk sizing, loss cooldowns, daily loss controls,
  drawdown halts, chronological holdout, and doubled-cost evaluation.
- Acquired and SHA256-verified 177,120 Binance BTCUSDT minute candles for May
  through August 2026, with zero gaps. Original archives and provenance are local
  under `data/research/`; they are candle proxies, not Ourbit executable quotes.
- Reproduction commands and evidence limitations are in
  [the research runbook](pillar_two_backtesting.md).
- Recorded the failed [first baseline](pillar_two_baseline_results.md), then ran
  nine registered variants across thirteen development walk-forward folds with
  training-only selection, a one-hour embargo, and doubled costs.
- Added completed five-minute trend confirmation and causal cached replay to the
  research runner. Every candidate remained negative after costs; no candidate
  qualified and every training-selected fold held cash. Detailed
  [experiment results](pillar_two_experiment_results.md) distinguish these
  exploratory comparisons from evidence of a profitable strategy.

This completes the first LLM-free candle baseline tooling. Multi-timeframe
microstructure, probability calibration, full event replay, and the continuous
collector/shadow/paper applications remain unfinished. The paper CLI is still an
initialization smoke check; it is not a continuously running trading session.

Purged net-outcome labeling, local outcome-tree fitting, separate calibration,
threshold selection, and thirteen forward folds are now implemented. A second
iteration added measured taker-volume features from the original verified archives.
Both models rejected all entries and failed to beat the constant probability
benchmark. See [outcome results](pillar_two_outcome_results.md); zero trades are
not evidence of a profitable model.

The immediate priority is a new signal hypothesis using individual trades and
executable quotes, with measured costs/latency. Retain the outcome learner as a
testable filter. Preserve fresh data for a model that passes development first;
do not retune on the observed July–August baseline test.

## Deliberately blocked from live use

Ourbit's current public material documents spot V3 thoroughly but does not expose a complete, current futures REST/WebSocket contract sufficient to safely guess live paths, topics, signing differences, or order semantics. The adapter therefore requires a verified endpoint manifest and live mode remains fail-closed.

## Fine-tuning server status

**Do not rent the RTX 3090 yet.** The project is not at the server-training gate. Real reproducible crypto news/quote data, dataset-quality reporting, leakage controls, a locally evaluated baseline, chat-template validation, a tiny-model training smoke test, and the remote training runbook must be completed first.

Once every gate in `AGENT_INSTRUCTIONS.MD` passes, the agent must explicitly tell the user to rent the server and wait for confirmation before performing remote work.

## Next promotion work

1. Confirm jurisdiction/account permission and obtain the current official futures API contract from Ourbit.
2. Populate and verify a read-only endpoint manifest.
3. Capture BTC trades/BBO/L2/mark/funding/status continuously; validate 24-hour reconnect and sequence semantics experimentally.
4. Expand mapper fixtures from sanitized real payloads and run disconnect/gap chaos tests.
5. Collect sufficient BTC history, calibrate execution costs, and run 12+ walk-forward folds plus the 288-scenario stress matrix.
6. Run shadow and paper stages across several hundred qualified signals.
7. Only then enable smallest-size micro-live validation with a maximum of 2x leverage.

Legacy Forex/XAU/USD data remains quarantined and is not read by any new application or training pipeline.

Current legal blocker: Ourbit's May 30, 2026 User Agreement names Iran as an excluded jurisdiction. Live use cannot be promoted if the account holder or operation is in Iran or another excluded jurisdiction.
