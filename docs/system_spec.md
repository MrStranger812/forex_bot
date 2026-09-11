# System specification

## Locked scope

- Product: Ourbit USDT-margined perpetual futures. Coin-M is out of scope.
- Rollout: `BTC_USDT` first; `ETH_USDT` only after BTC promotion gates pass.
- Cadence: event-driven trades/BBO/L2, with 1-second, 5-second, 15-second, and 1-minute features.
- Position model: at most one net position per symbol.
- Leverage: 1x in paper; at most 2x during early live validation.
- Architecture: asynchronous Python Ourbit gateway plus deterministic recorder/replay engine. No NautilusTrader live dependency.

## Governing entry rule

No entry unless all of the following are true:

1. Pillar 1 is valid, directional, fresh, and above its confidence threshold.
2. Pillar 2 is fresh, directional, and above its strength threshold.
3. Directions agree.
4. Spread and depth pass rolling liquidity limits.
5. Expected movement exceeds fees, spread, slippage, and the safety margin.
6. No circuit breaker or unresolved order exists.
7. The independent risk engine approves and sizes the order.

Pillar 1 failure, timeout, abstention, neutrality, or staleness blocks new entries. It does not independently liquidate a safe existing position. Pillar 2 controls timing. The LLM publishes opinions only and has no reference to exchange credentials or execution objects.

## Market and account safety

- Startup is read-only reconciliation before subscriptions or order placement.
- Private events are the primary account-state source; REST is for startup and gap recovery.
- A timed-out or 5xx order request has unknown status. Query the unique client-order ID before retrying.
- A public depth sequence gap invalidates the book until a fresh snapshot is loaded.
- Stale or out-of-order market events are rejected and recorded with a reason.
- Market-data failure cancels working entry orders. If actual position state becomes uncertain and cannot be reconciled, the system issues a reduce-only flatten request and halts.
- Every filled entry must immediately create or confirm a protective exit.
- Exchange fees, price/quantity increments, minimum notional, and contract multiplier are discovered metadata.

## Pillar 2 research baseline

`TimeBasedPillarTwoEngine` consumes completed time bars with a full warmup, rejects
overlapping/out-of-order bars, and resets its warmup after missing intervals. Its
trend, range-reversion, and compression-breakout hypotheses select signals using
interpretable historical price/volume features. The initial research configuration
uses one-minute bars and a five-minute holding horizon. The same engine can consume
completed trade-built bars in `PaperTradingEngine` via `time_based_config`.

Signal strength is a heuristic, not a calibrated probability. `probability` remains
unset until a separate calibration procedure has passed chronological validation.

The isolated `research.pillar_two_outcomes` study learns cost-adjusted opportunity
outcomes using separate chronological fitting, calibration, threshold-selection,
and validation periods. Its research signals may carry empirical `probability`
and `expected_net_return_bps` estimates. The latter already includes modeled costs,
so the research entry gate compares it with the safety margin. Execution still
charges costs on actual simulated fills. These fields do not authorize paper/live
promotion; the time-based runtime continues to emit uncalibrated signals.

The optional outcome research flow features use recorded taker-buy volume from
checksum-verified source candles, matched to normalized OHLCV and trade counts.
They do not substitute for executable quotes or L2 book events.

Research accepts identified Binance spot and USDT-M minute archives, with
separate cache/provenance and timestamp conventions. Mixed-market inputs are
rejected. The individual USDT-M trade audit creates measured five-second buckets
and compares minute OHLCV, counts, and taker volume to independent candles.
This sample is not integrated into the minute outcome learner. Published Ourbit
fees are isolated research scenarios; current account fees and actual execution
costs still require verification.

The isolated `research.signal_design_study` compares activity/flow momentum,
failed-pressure and failed-breakout reversals, a candle VWAP proxy, and futures/spot
basis reversion. Fixed regime routing and consensus preserve specialist movement
budgets; nested momentum ablations do not count as independent consensus votes.
Signals consume completed, exactly synchronized observations and enter at the
next open. Selection uses earlier training windows under base and stressed costs,
with cash as the fallback. All candidates and source/input/configuration hashes
are recorded before evaluation. These rules have no LLM or exchange dependency
and do not replace the runtime signal engine. See
[the registered design runbook](pillar_two_signal_research.md).

Expected movement is also a heuristic volatility budget, not a calibrated return
forecast. Archives with taker-buy volume supply aggregate trade-flow features;
candle history cannot supply L2 events, quote freshness, or actual executable
bid/ask prices. Those features and their confirmation gates remain a later
event-data milestone. Basis convergence is not booked as profit: only simulated
futures-leg fills determine the unhedged basis candidate's PnL.

The offline `research.pillar_two_backtest` deliberately isolates Pillar Two without
news or model inference. It uses next-bar entries, assumed execution costs, protective
stops, targets, time exits, and risk limits. Results are split chronologically with an
embargo and a separately reported later test window. This research exception does not
change the governing entry rule in the execution runtime.

## Legacy Pillar 2 benchmark

The signed score is `S = 0.30E + 0.20D + 0.15R + 0.15B + 0.20F`, where E is EMA alignment/slope, D is directional movement, R is rate of change, B is book imbalance, and F is aggressive trade-flow imbalance. Inputs are normalized only from information available at the event time. Strength is `min(1, abs(S)) * liquidity_quality * volatility_suitability`.

Weights are hypotheses. They may be fit only on training windows, then evaluated on later untouched windows with stressed execution costs.

## Pillar 1 contract

Gold is the current fundamental research focus, with internal research symbol
`XAU_USDT` isolated from BTC, XAUT, and the quarantined legacy XAU/USD corpus.
The public Ourbit listing establishes a displayed Gold (XAU)/USDT market; exact
API identity and execution details remain unverified.

`apps.run_fundamental_collector` captures official RSS/Atom releases, the Fed
historical headline index, BLS calendar events, and Treasury nominal/real curves.
It stores raw response hashes, original request/receipt timestamps, immutable
versions, source provenance, duplicate sightings, failures, and retry checkpoints.
Calendar timezone ambiguity is rejected; date-only archive entries retain dates
without invented release times. Missing yields remain null. Latest historical
snapshots are explicitly distinct from original economic-data vintages.

`FundamentalStore.context(at)` only reads versions received by `at`. Revisions and
duplicates cannot renew an old story's freshness; monthly releases using the same
URL remain distinct events. Headline-only archives are excluded from fresh news
context. Dated yield curves expose their age. Actuals, consensus, prior values,
and surprises remain unset until a separately verified release parser/provider
supplies them. The context abstains and is not a `PillarOneOpinion`.

The existing inference service and training builder remain separate from this
unlabeled corpus. Gold requires its own quote labels and chronological validation;
the prior BTC backtests do not establish gold performance. See
[gold fundamental collection](pillar_one_xau_capture.md).

News records retain source ID, canonical URL, publication/receipt times, affected symbols, headline/body/language, source reliability, and content hash. Deduplication occurs before inference and duplicate content never renews an opinion.

The model returns strict JSON containing direction, confidence, strength, horizon, affected symbols, event type, and abstention. Invalid, contradictory, expired, or late output fails closed. Recommended local inference is a short-context 7B–8B four-bit instruct model, batch one, near-zero temperature, in a process separate from trading.

## ARB reference-market observation

The isolated `apps.run_prediction_watch` observes the September 12-13 ARB/USDT
window using KuCoin perpetual reference candles, the existing closed-bar Pillar Two
engine, and actual response receipt times. It distinguishes reconstructed history
from forward observations, stores raw responses, and expires stale current signals.
It contains no order client; the combined strategy remains HOLD without Pillar One.
The retrospective candle report retains explicit assumed costs and risk limits.
This observer does not expand the live universe. See [the runbook](arb_prediction_watch.md).

## Risk baseline

- Risk per trade: configurable 0.05–0.10% of equity; default 0.05%.
- Daily realized loss stop: 1%.
- Total drawdown stop: 3%.
- No averaging down, martingale, or simultaneous second position in a symbol.
- Cooldown follows the configured consecutive-loss count.
- Sizing uses equity risk divided by stop distance and contract multiplier, rounded down to lot size and checked against min notional.

## Security and external constraints

Development and live keys are separate, have no withdrawal permission, use minimum trading permissions, and are IP-allowlisted when available. Secrets live only in environment variables or an OS secret store. The exposed legacy EODHD key must be revoked by its owner; removing it from the tree does not revoke it or purge Git history.

Before live operation, the account owner must obtain current confirmation that automated derivatives trading is allowed for the account, jurisdiction, and deployment location. Ourbit's User Agreement dated May 30, 2026 explicitly lists Iran among its excluded jurisdictions. The workspace's `Asia/Tehran` timezone does not prove residency, but live use from or by an excluded jurisdiction is prohibited and must not be attempted. A futures testnet and exact futures API contract must also be confirmed with Ourbit. Until then, only offline replay, research, and local paper modes are permitted; even public collection must comply with the applicable terms and data-use restrictions.
