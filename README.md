# Ourbit two-pillar event-driven scalper

This is a safety-first research and execution framework for Ourbit USDT-margined perpetual futures. It is not HFT. The initial instrument is `BTC_USDT`; `ETH_USDT` is staged behind validation gates.

The entry invariant is simple: fresh Pillar 1 direction must match a sufficiently strong Pillar 2 direction, and a separate risk gate must approve the trade. The LLM cannot submit orders.

> **Live-use warning:** Ourbit's User Agreement dated May 30, 2026 lists Iran and several other locations as excluded jurisdictions. This workspace uses the `Asia/Tehran` timezone. That does not prove residency, but live mode must remain disabled unless the account holder and deployment are eligible under current terms. Never use location masking to evade exchange restrictions.

## Current capability

- Strict domain, news-opinion, order, position, and instrument models
- Deterministic Pillar 2 features and two-pillar decision gate
- Risk limits, sizing, kill switches, and paper matching
- Signed asynchronous REST foundation with unknown-order reconciliation semantics
- Sequenced L2 book, reconnecting public WebSocket foundation, raw event recording
- Deterministic replay, walk-forward split, stress scenario generation, dataset tooling
- Live trading locked until Ourbit futures endpoints are verified and explicitly enabled
- 306 deterministic unit, integration, replay, and chaos checks

## Start

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,storage]"
python -m pytest
python -m apps.run_paper --config configs/paper.yaml
```

See `docs/system_spec.md`, `docs/operations.md`, and `docs/status.md` before connecting an account.

## Pillar Two without the LLM

The standalone research runner tests the time-based trend/range/breakout baseline
against public BTC candle history. It reports net win rate, return on starting
capital, profit factor, drawdown, chronological development/test results, and a
doubled-cost scenario. It requires no credentials, LLM, or GPU.

See [the backtest runbook](docs/pillar_two_backtesting.md) for reproducible commands,
cost assumptions, and the limits of using candle data for scalping research.

The [development walk-forward study](docs/pillar_two_experiment_results.md) compares
nine variants across thirteen folds. No variant currently qualifies as profitable
after the assumed costs; the training-based selector holds cash when evidence fails.

The [net-outcome model study](docs/pillar_two_outcome_results.md) adds matured labels,
separate calibration, and measured taker-volume features. It has not established
a positive net edge and remains research-only.

The [Ourbit fee validation](docs/pillar_two_ourbit_cost_results.md) repeats the
study with published fees on spot and USDT-M data. It still fails selection.
[Data acquisition and fees](docs/ourbit_data_and_costs.md) cover new futures
candles, audited individual trades, and unresolved Ourbit public-feed access.
