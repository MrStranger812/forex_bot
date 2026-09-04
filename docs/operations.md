# Operations and security runbook

## Modes

- `collector`: public, read-only market capture.
- `shadow`: live signals plus simulated orders; no exchange write permission.
- `paper`: deterministic local matching and risk accounting.
- `live`: real orders; locked by configuration, environment acknowledgement, verified endpoint manifest, and startup reconciliation.

Never give a collector or shadow key trade or withdrawal permission. Live keys must not have withdrawal permission.

## Live interlock

`run_live` refuses to start unless all of these hold:

1. `configs/live.yaml` has `execution.enabled: true`.
2. `OURBIT_LIVE_ACK=I_UNDERSTAND_REAL_ORDERS_WILL_BE_SENT`.
3. `OURBIT_ACCOUNT_ELIGIBILITY_CONFIRMED=YES_CURRENT_TERMS_REVIEWED`, set only after a current account/jurisdiction review.
4. The configured futures endpoint manifest is marked verified and contains no blank required paths.
5. API credentials are present.
6. Startup balance, position, and open-order reconciliation succeeds.

The acknowledgement is intentionally never stored in YAML or source control.

Ourbit's User Agreement dated May 30, 2026 lists Iran as an excluded jurisdiction. Do not use an Ourbit account, API, VPN, VPS, or false location representation to evade that restriction. The `Asia/Tehran` development timezone is a warning signal, not proof of the account holder's residence; eligibility requires independent confirmation.

## Incident actions

- Public feed stale/gapped: invalidate the book, cancel working entries, reconnect, snapshot, replay contiguous deltas.
- Private stream gap: stop entries and REST-reconcile orders, positions, and balances.
- Order timeout/5xx: mark `UNKNOWN`, query by client-order ID, and do not retry while unresolved.
- Position uncertainty: stop entries; attempt bounded REST recovery; flatten reduce-only and halt if recovery is impossible.
- Daily loss/drawdown breach: cancel entries, flatten per policy, latch the kill switch until an operator resets it for a new session.
- Key suspected exposed: disable it at Ourbit immediately, create a least-privilege replacement, update the secret store, and audit order/fill history.

Develop on Linux or WSL2 and deploy on a monitored Linux VPS chosen after measuring latency to Ourbit endpoints. Use NTP, process supervision, disk-space alerts, log rotation, encrypted storage, and a separate low-privilege OS user.

The EODHD credential formerly committed in `scripts/eodhd_news_scraper.py` must be rotated in the EODHD dashboard. Git history may still contain it; consider a coordinated history rewrite only after rotation and after confirming repository-sharing implications.
