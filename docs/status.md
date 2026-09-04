# Implementation status

Updated: 2026-09-04

## Implemented foundation

- Phase 0 scope, invariants, live interlock, and operations specification
- Phase 1 package layout, environment-only secrets, modern Python packaging
- Exchange-neutral domain models and configurable Ourbit adapter foundation
- Sequenced L2 book and deterministic raw-event/replay formats
- Strict Pillar 1 schema/deduplication and bounded LLM client
- Interpretable Pillar 2 score and two-pillar gate
- Decimal sizing, limits, kill switch, paper exchange, and reconciliation state machine
- Walk-forward/data-split/stress scenario utilities and baseline automated tests
- 130 passing unit, integration, replay, and deterministic chaos checks; Ruff and strict mypy clean

## Deliberately blocked from live use

Ourbit's current public material documents spot V3 thoroughly but does not expose a complete, current futures REST/WebSocket contract sufficient to safely guess live paths, topics, signing differences, or order semantics. The adapter therefore requires a verified endpoint manifest and live mode remains fail-closed.

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
