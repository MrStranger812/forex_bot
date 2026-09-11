# ARB/USDT observation run

This run observes **2026-09-12 02:36 through 2026-09-13 02:36 Asia/Tehran**
(2026-09-11 23:06 through 2026-09-12 23:06 UTC). The end is exclusive for
predictions. It collects the last closing candle at the end, with up to 90 seconds
for final receipt, then exits automatically.

The source is **KuCoin ARBUSDTM, a USDT-settled ARB perpetual**. Its public metadata
and candles were checked on September 12. Ourbit metadata failed DNS resolution,
Binance spot returned HTTP 451 and its futures feed timed out, and OKX/Kraken
returned HTTP 403. Evidence is in `data/research/arb_feed_probe_20260912/`.
This reference market does not establish prices, liquidity, or fills on Ourbit.

The runner applies the existing deterministic Pillar Two baseline, unchanged:
60 completed minute candles for warmup and a five-minute signal horizon. Each
minute gets a bullish, bearish, or neutral hypothesis with strength, regime, and
reasons. Strength is **not** a success probability; probability remains null.
There is no trained next-day price forecast or ARB-specific validation.

This isolated observer has no exchange credentials or order client. The combined
two-pillar strategy remains **HOLD** because Pillar One is unavailable. Its action
can therefore differ from the directional market signal.

## Run and inspect

The hidden background process started at approximately **02:45:49 Tehran on
September 12**. `process.json` contains the launch record; `status.json` contains
the worker PID and heartbeat. Windows virtualenv launchers can have a different
PID from their Python child. Verify both heartbeat and process when checking
liveness; an old `observing` status alone is insufficient.

Keep this computer awake, connected, and running for the observation period.
The process survives closing the terminal, but not shutdown/reboot.

```powershell
Get-Content data/research/arb_prediction_20260912/status.json
Import-Csv data/research/arb_prediction_20260912/predictions.csv | Select-Object -Last 10
```

- `status.json`: latest signal and freshness, first signal, coverage, heartbeat,
  errors, and separate forward/reconstructed directional accuracy.
- `predictions.csv`: signal history and matured five-minute outcomes.
- `observations.sqlite3`: durable observations, original HTTP payloads, response
  hashes, request/receipt times, and frozen code/configuration identity.
- `retrospective_backtest.json`: produced on normal finish, a one-pass run, or an
  offline report. Contains the existing candle simulation with baseline and
  doubled costs. This is retrospective Pillar Two research, not forward paper
  fills or combined-strategy performance.
- `stdout.log` / `stderr.log`: background process output.

To resume after an interruption, run the same command from the repository root:

```powershell
.venv\Scripts\python.exe -u -m apps.run_prediction_watch --config configs/arb_prediction_20260912.yaml
```

An OS lock rejects a second writer. Restart rebuilds the engine from saved bars
without reissuing predictions or changing their original recording times. Changes
to configuration or relevant source code require a new output directory. Restart
after the window can recover history, with those signals marked reconstructed.

For a separate smoke run, copy the YAML, change `output_dir`, and add `--once`.
To generate the candle backtest after the active process has stopped:

```powershell
.venv\Scripts\python.exe -m apps.run_prediction_watch --config configs/arb_prediction_20260912.yaml --offline-report
```

To stop gracefully, create this sentinel. Remove it before resuming.

```powershell
New-Item -ItemType File -Path data/research/arb_prediction_20260912/STOP
```

## Interpretation

Minutes missed during setup are reconstructed using only candles through each
historical event time. They were not predictions recorded at 02:36. A forward
observation must be recorded before the one-minute signal validity expires;
its actual receipt time is preserved separately.

Only closed candles are consumed, with a two-second publication buffer. Requests
use explicit ranges and pagination. Duplicate polls do not add signals; gaps reset
warmup. Revised closed candles or late repairs of previously observed gaps produce
a feed error and retain the original evidence. Transport errors use bounded
backoff; access-denied responses stop the process. Stale/erroring feeds expose no
current prediction. Recovered late signals remain reconstructed.

Directional accuracy compares the signal's closing price with the close five
minutes later. Neutral signals are abstentions. Missing target candles and targets
beyond the window remain unscored. Signals overlap; these counts are not independent
trades or profitability estimates. Volume is contracts; trade count is unavailable.

The retrospective simulation assumes 5 bps fees per side, 1 bp full spread, 1 bp
slippage per side, and a 2 bp entry safety margin. These are research assumptions,
not verified account costs. It retains the existing 1x exposure cap, 0.05% equity
risk, stops/targets, cooldown, daily loss gate, and drawdown halt. Funding,
executable depth, and latency are missing.

Source contract: [KuCoin public futures candles](https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-klines)
and [public contract metadata](https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-symbol).
