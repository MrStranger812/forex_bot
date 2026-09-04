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
