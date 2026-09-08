# First Pillar Two candle baseline — 2026-09-07

The initial deterministic strategy did not establish positive net expectancy.
This is a recorded failed hypothesis, retained as the benchmark for later work.

Data: 177,120 SHA256-verified Binance BTCUSDT spot minute candles, 2026-05-01
through 2026-09-01 exclusive, no missing bars. These are candle proxies, not
Ourbit executable quotes. No LLM, fitted probability, or parameter search was used.

| Window | Costs | Trades | Net win rate | Account return | Net profit factor | Maximum estimated drawdown |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Development | Base | 162 | 22.84% | -3.011% | 0.169 | 3.029% |
| Development | Doubled | 112 | 25.00% | -3.022% | 0.037 | 3.022% |
| Later test | Base | 152 | 21.71% | -3.003% | 0.163 | 3.004% |
| Later test | Doubled | 106 | 27.36% | -3.001% | 0.040 | 3.001% |

Development ended at 2026-07-26 02:23 UTC. After the five-minute embargo, the
test began at 02:28 UTC and ended at 2026-09-01 00:00 UTC. Accounts started
independently at 10,000 USDT. All four accounts hit their drawdown halt; they did
not continue entering throughout the nominal window. The base test stopped on
August 12; the stressed test stopped on August 24. The stressed trade population
changes because the cost gate is also tightened.

In the base test, gross PnL was -5.01 USDT, fees were 227.16, and spread/slippage
cost 68.15, giving -300.31 net. Of 152 exits, 97 were time exits, 18 targets,
36 stops, and one risk halt. Trend signals made +36.25 gross but -148.60 net;
range reversion made -38.13 gross and -119.46 net; breakout made -3.13 gross
and -32.26 net. These observations motivate development experiments with holding
horizons and model selection. They are not evidence that a longer horizon works.

The win-rate Wilson interval for the base test is 15.90–28.92%; serial dependence
limits its statistical interpretation. The full JSON report includes costs,
resolved defaults, source hashes, risk events, and regime/model summaries:

- Local report: `artifacts/pillar_two_baseline/report.json`
- Local trade ledger: `artifacts/pillar_two_baseline/trades.csv`
- Dataset SHA256: `54f0a83179fd3ef17a0b954760435db5211693f352ba8a021b43907c1bc229b7`
- Configuration SHA256: `83834de5fc3eac2bae92f4e4f29794676ce7e16dde4f930f8f41d20566d8695b`

Validation at this milestone: 251 tests passed, Ruff and strict mypy passed,
paper initialization passed, and all four CSV ledgers independently reconciled
with report PnL and ending equity. Reproduction: [runbook](pillar_two_backtesting.md).
The July–August test has been observed and cannot be presented as untouched for
subsequent strategy revisions.
