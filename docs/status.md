# Implementation status

Updated: 2026-09-12

## ARB/USDT observation experiment

The user-requested September 12 02:36 to September 13 02:36 Tehran window now has
a bounded public-candle observer using verified KuCoin `ARBUSDTM` metadata. The
background worker started around 02:45:49 Tehran; elapsed signals are reconstructed
and new observations retain actual receipt times. Initial 02:36 reconstruction:
neutral, ranging, no confirmed reversion. This is not a next-day price forecast.

All 373 tests pass, including 13 observer checks; Ruff and strict mypy pass.
The process has restart deduplication, raw response retention, missing/stale-feed
handling, separate forward/reconstructed outcomes, and retrospective cost stress.
The full two-pillar strategy remains HOLD without Pillar One. No orders are sent.
See [the runbook](arb_prediction_watch.md) and local `status.json` for current
liveness and results. The 24-hour window is still in progress.

## Implemented foundation

- Phase 0 scope, invariants, live interlock, and operations specification
- Phase 1 package layout, environment-only secrets, modern Python packaging
- Exchange-neutral domain models and configurable Ourbit adapter foundation
- Sequenced L2 book and deterministic raw-event/replay formats
- Strict Pillar 1 schema/deduplication and bounded LLM client
- Interpretable Pillar 2 score and two-pillar gate
- Decimal sizing, limits, kill switch, paper exchange, and reconciliation state machine
- Walk-forward/data-split/stress scenario utilities and baseline automated tests
- 343 passing unit, integration, replay, and deterministic chaos checks; Ruff and strict mypy clean

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
The separate ARB observer above now supplies bounded candle signal collection;
continuous two-pillar paper execution remains unfinished.

Purged net-outcome labeling, local outcome-tree fitting, separate calibration,
threshold selection, and thirteen forward folds are now implemented. A second
iteration added measured taker-volume features from the original verified archives.
Both models rejected all entries and failed to beat the constant probability
benchmark. See [outcome results](pillar_two_outcome_results.md); zero trades are
not evidence of a profitable model.

The [Ourbit cost study](pillar_two_ourbit_cost_results.md) compares published
4 bp taker fees, with and without earlier execution assumptions, on spot and
USDT-M proxies. All four studies fail selection. The USDT-M diagnostic has 30
trades at profit factor 0.880 with fees alone, falling to 0.748 with assumed
execution costs; it is confined to one fold and is not a validated improvement.

Downloaded 125,280 USDT-M minute candles and audited 3,998,559 individual trades
for July 20 against all 1,440 minute candles. The sample provides 17,280 measured
five-second records; numeric ID gaps are disclosed. Commands and evidence are
in [the data and fees report](ourbit_data_and_costs.md).

The [signal design study](pillar_two_signal_research.md) adds seven deterministic
families, fixed regime routing, consensus, and selection using earlier data. Nineteen
fixed candidates were evaluated over 13 folds under fees-only, base, and doubled
costs. Every active candidate loses in every scenario; the selector holds cash in
all folds. Failed-breakout reversal at 60 minutes has 192 trades, 39.58% net win
rate, and base profit factor 0.611. Its mean fold return is -0.0678%, with an
average trade return of -0.0835% of entry notional. Basis candidates make no trades.
These are exploratory comparisons on reused development dates, not a fresh test.

The remaining BTC research work is extending audited trades across complete folds and
obtaining synchronized executable quotes with exchange/receive timestamps. Use
that evidence to test whether failed breakouts coincide with measurable depth
replenishment, flow decay, or a reference-market move. Current Ourbit account fees
and measured execution costs/latency remain missing. Preserve fresh data for a
model that passes development first; do not retune on the observed baseline test.

## Pillar One gold capture milestone

The user's research priority is now XAU-USDT fundamentals. Added a resumable,
bounded collector for official Fed, BEA, BLS, and Treasury sources with raw response
hashes, durable versions/sightings, failure backoff, and restart checkpoints.
Historical dates do not override receipt time. An as-of context exposes recent
news, upcoming scheduled events, and dated yield curves while abstaining.

The audited bootstrap has 16,557 records: 82 feed summaries, 4,626 Fed archive
headlines (including 1,071 monetary-policy entries), and 11,849 daily yield curves
covering 2003-01-02 through 2026-09-04. The curves contain 101,085 non-missing tenor
observations. These counts describe different data units, not independent gold
trades or training labels. Subsequent collection may add versions. See
[the XAU collection runbook](pillar_one_xau_capture.md).

The source audit verified 55 saved response hashes and SQLite integrity. A rebuild
from the original responses fixed date-only archive parsing and story grouping
while preserving original receipt times. Original captures remain available.
The 28 new tests cover time handling, missing values, revisions, monthly release
URLs, replay, corruption detection, HTTP validators, and restart behavior.

The September 9 refresh recovered Fed monetary RSS and raised the corpus to 16,574
records. A bounded 24-hour local collection run started at 19:00 UTC; its startup
process and heartbeat were verified. Current liveness is recorded in
`data/research/pillar_one_xau_v1/status.json` and `collector_process.json`.
This is ongoing capture, not a completed 24-hour stability test.

BLS feeds/calendar returned HTTP 403; Fed monetary RSS initially timed out.
On September 9, the bounded Ourbit public metadata probe again failed at TLS.
The public Gold (XAU) listing does not verify the API symbol, executable quotes,
multiplier, financing, or account fees. No XAUT proxy is silently substituted.

Next: sustain timestamped collection; acquire full release text, licensed
point-in-time consensus estimates and broader geopolitical/dollar data; resolve
Ourbit XAU metadata/quotes; then create leakage-checked event labels. Compare a
simple fundamental baseline, Pillar Two alone on gold, and the combined gate on
the same untouched periods. Neither GPU training nor a profitable fundamental
filter has been validated.

## Deliberately blocked from live use

An official futures Postman collection has been located and pinned. It is partial
and contains inconsistent examples; five public data probes failed at TLS.
Payloads, WebSocket topics, signing differences, and order semantics remain
unverified. The execution adapter still requires a verified endpoint manifest.

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
