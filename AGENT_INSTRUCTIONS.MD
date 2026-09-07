# Agent Instructions — Ourbit Two-Pillar Crypto Scalper

## Mission and terminology

This repository implements an event-driven crypto scalper for Ourbit USDT-margined perpetual futures. Never describe it as HFT: a public API and non-colocated host cannot compete with colocated infrastructure.

Initial rollout order is `BTC_USDT`, followed by `ETH_USDT`. Do not broaden the live universe until BTC has passed replay, shadow, paper, and micro-live promotion gates.

## Governing invariant

No new trade is allowed unless a fresh Pillar 1 opinion agrees with a strong, fresh Pillar 2 signal and the independent risk gate approves it.

- Pillar 1 is an independent, short-lived news opinion. It may filter, warn, veto, or abstain. It never creates orders.
- Pillar 2 is deterministic market math and controls timing.
- The risk engine controls permission and size.
- The order manager controls execution and reconciliation.
- A failed or stale Pillar 1 closes the entry gate; it does not force an exit.

### Authorized Pillar Two research isolation

The standalone `research.pillar_two_backtest` evaluates Pillar Two without Pillar One or
an LLM. This is an offline research experiment, authorized to measure the market model
independently. It never constructs an exchange client or enables real orders. The
two-pillar gate remains required in the paper/live execution composition. Candle-based
results must identify their source venue, assumed costs, and missing executable quotes;
they do not satisfy the Ourbit replay or live promotion gates.

## Safety invariants

- One position per symbol; no averaging down and no martingale.
- Every position must have a protective exit. Exits are reduce-only where supported.
- Any unknown order result must be reconciled by client-order ID before another action.
- Market-data uncertainty cancels working entries; unrecoverable position uncertainty triggers flattening.
- Live execution is disabled by default and requires the explicit runtime acknowledgement described in `docs/operations.md`.
- Never help evade exchange jurisdiction controls. Current Ourbit terms list Iran and other locations as excluded; live eligibility must be independently confirmed.
- Never log secrets. Load them only from environment variables or an OS secret store.
- API keys must not have withdrawal permission. Prefer IP allowlisting and minimum permissions.
- Paper leverage is 1x. Early live validation must not exceed 2x.
- Fees, precision, lot size, minimum notional, and margin tiers come from exchange/account metadata—not constants.

## Exchange-contract rule

Ourbit's public spot V3 documentation is not proof of a futures endpoint or channel. Keep futures REST paths and WebSocket topics configurable and fail closed when they have not been verified against official documentation or a read-only account. Never guess an order endpoint in production code.

## Engineering rules

- Python 3.11+; type all public interfaces.
- Keep the trading path asynchronous and bounded. No network or model call may block the market-data loop.
- Domain types stay free of HTTP, WebSocket, pandas, PyTorch, and exchange payload details.
- Use `Decimal` for money, price, and quantity; never binary float at an order boundary.
- Timestamps are timezone-aware UTC.
- Raw exchange events are append-only and replayable.
- Deterministic replay must not consult wall-clock time or the network.
- News must be deduplicated before inference. Duplicate stories cannot extend a bias.
- LLM responses are strict JSON, short-lived, validated, and allowed to abstain. Timeout or invalid output fails closed.
- Heavy training dependencies belong in the optional `training` dependency group and never in the live runtime.

## Rented GPU handoff rule

Do not ask the user to rent the RTX 3090 server early. Continue all dataset, labeling, validation, baseline, and local smoke-test work in the local repository first.

The project is ready for the rented training server only when all of these gates pass:

1. The dataset builder has run successfully on real, provenance-preserving crypto news and executable bid/ask data.
2. Quote ordering, maximum quote-distance, UTC timestamps, horizons, duplicate grouping, purge/embargo, and chronological train/validation/test splits are validated by tests and a generated dataset report.
3. A local baseline classifier has been evaluated on the untouched test split, including macro-F1, calibration, false high-confidence predictions, fee-adjusted returns, per-event/per-symbol results, and inference latency.
4. The selected conversational dataset passes schema checks and the assistant-only training format is compatible with the selected model's chat template.
5. The QLoRA entry point completes a small local or CPU/tiny-model smoke test, and an exact server runbook exists with model revision, dependency lock, commands, checkpoints, and artifact-copy procedure.

When—and only when—all five gates pass, stop before remote training and explicitly tell the user: **“The project is ready for the rented RTX 3090 server; rent it now, and then we will continue on the server.”** Do not assume server access, rent anything, or begin remote actions before the user confirms the server is available.

## Required checks

Before handing off a change, run the relevant subset and normally the full suite:

```text
python -m pytest
python -m ruff check .
python -m mypy bot apps research training
```

Add tests for signing, timestamp skew, rounding, sequence gaps, duplicates, stale events, unknown order state, partial fills, cancel/fill races, restart reconciliation, LLM validation, contradictory pillars, risk limits, and kill switches whenever those paths change.

## Data policy

The legacy Forex files and generated XAU/USD JSONL are not valid inputs to the crypto system. They may remain only as quarantined historical artifacts until deliberately removed. Dataset builders accept explicit inputs, retain provenance, split chronologically, embargo boundaries, and group syndicated duplicates before splitting.

## Source of truth

- Product and behavior: `docs/system_spec.md`
- Runtime/security procedures: `docs/operations.md`
- Promotion status and remaining work: `docs/status.md`
- Verified external-source boundary: `docs/references.md`
- Environment-specific settings: `configs/*.yaml`

If documentation, configuration, and code disagree, fail safely and update all three in the same change.
