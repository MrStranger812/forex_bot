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
Expected movement is also a heuristic estimate. Candle history cannot supply L2,
trade aggressor flow, quote freshness, or actual executable bid/ask prices; those
features and their confirmation gates remain a later event-data milestone.

The offline `research.pillar_two_backtest` deliberately isolates Pillar Two without
news or model inference. It uses next-bar entries, assumed execution costs, protective
stops, targets, time exits, and risk limits. Results are split chronologically with an
embargo and a separately reported later test window. This research exception does not
change the governing entry rule in the execution runtime.

## Legacy Pillar 2 benchmark

The signed score is `S = 0.30E + 0.20D + 0.15R + 0.15B + 0.20F`, where E is EMA alignment/slope, D is directional movement, R is rate of change, B is book imbalance, and F is aggressive trade-flow imbalance. Inputs are normalized only from information available at the event time. Strength is `min(1, abs(S)) * liquidity_quality * volatility_suitability`.

Weights are hypotheses. They may be fit only on training windows, then evaluated on later untouched windows with stressed execution costs.

## Pillar 1 contract

News records retain source ID, canonical URL, publication/receipt times, affected symbols, headline/body/language, source reliability, and content hash. Deduplication occurs before inference and duplicate content never renews an opinion.

The model returns strict JSON containing direction, confidence, strength, horizon, affected symbols, event type, and abstention. Invalid, contradictory, expired, or late output fails closed. Recommended local inference is a short-context 7B–8B four-bit instruct model, batch one, near-zero temperature, in a process separate from trading.

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
