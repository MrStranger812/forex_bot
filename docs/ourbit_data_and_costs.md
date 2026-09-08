# Ourbit fees and market data

Reviewed and tested on 2026-09-08. Public-data acquisition and offline Pillar Two
research only; no LLM, account credentials, or exchange orders.

## Published fee evidence

Ourbit's [2024-09-03 announcement](https://www.ourbit.com/support/articles/17827791510072)
states futures maker **0.02%** and taker **0.04%**, equivalent to 2 and 4 basis
points per fill. Two taker fills cost approximately 8 bps round trip; the
executor charges each fee on that fill's actual notional.

This is a published reference, not this user's verified current account fee.
The [2025-04-23 Taiwan announcement](https://www.ourbit.com/support/articles/17827791511088)
lists VIP 0 taker 0.05%, demonstrating regional variation. The general
[fee page](https://www.ourbit.com/fee) did not expose a usable current futures
table during this review. No VIP discount, regional eligibility, rebate, or
maker execution is assumed. Zero spot fees do not apply to futures.

| Configuration | Fee per fill | Full spread | Slippage per fill | Approximate round trip |
|---|---:|---:|---:|---:|
| Previous baseline | 5 bps | 1 bp | 1 bp | 13 bps |
| `pillar_two_ourbit_fees.yaml` | 4 bps | excluded | excluded | 8 bps |
| `pillar_two_ourbit_costs.yaml` | 4 bps | 1 bp assumed | 1 bp assumed | 11 bps |

The fee-only scenario isolates fees as requested. Excluded costs have not been
measured as zero. The other scenario retains previous execution assumptions.
Funding, depth impact, and order latency remain unmodeled. The 2 bp safety
margin is an entry threshold, not an accounting expense. Both scenarios also
run with doubled modeled costs. YAML and reports retain the fee source, review
date, and unverified account status.

## Ourbit public-data boundary

The official [futures Postman collection](https://github.com/ourbitdevelop/ourbit-api-postman/blob/a6fcd511859e84e09be81cbc649fbc13be83b2bc/OURBIT%20V1%20contract.postman_collection.json)
provides a partial contract. The new probe pins this revision and verifies that
its five requested public paths occur in the collection. It makes only
unauthenticated GET requests to the documented `contract.ourbit.com` host.

Detail, recent deals, candles, ticker, and depth-commit requests all failed at
TLS in this environment. Initial requests timed out; the recorded final probe
received unexpected EOF errors. No usable payload or executable quote was
captured. These errors do not establish their underlying cause.

The collection contains duplicate/mislabeled examples: `DepthBySymbol`, for
example, points to support currencies. Endpoint names alone cannot verify
payload mappings, depth continuity, WebSocket topics, order behavior, or fees.
Runtime futures paths remain unverified.

```powershell
python -m research.ourbit_public_probe --output-dir artifacts/ourbit_public_probe_new
```

The probe saves raw responses when available, timestamps, request duration,
source hashes, and individual failures. A response does not verify the execution
adapter. It makes one bounded pass and requires a new output directory to retain
prior evidence. Recorded report: `artifacts/ourbit_public_probe_2026_09_08/report.json`.

## Acquired futures proxy data

[Binance's official archives](https://github.com/binance/binance-public-data)
provide USDT-M candles and individual trades with SHA256 sidecars. The new
`binance_um_klines` source uses separate provenance and caching. Mixed spot/futures
normalized inputs are rejected. USDT-M inputs here use milliseconds and a CSV
header; the existing post-2025 spot input uses microseconds.

`data/research/btcusdt_um_1m_2026_05_07_26.jsonl` contains **125,280** minute
candles from May 1 through July 26, with zero gaps or zero-volume bars. Model
validation uses only 123,983 candles through July 26 02:23 UTC. The daily archive
tail beyond that cutoff is not used for model selection or scoring.

```powershell
python -m research.market_data --market um --start 2026-05-01 --end 2026-07-27 --output data/research/btcusdt_um_1m_2026_05_07_26.jsonl
```

The **July 20** sample contains **3,998,559 individual trades** and produces
**17,280 five-second buckets**. Aggregation exactly matches all 1,440 source
minute candles on OHLC, volume, trade count, and taker-buy volume. Original CSVs
remain in verified ZIPs; normalized buckets include observed buyer/seller flow.
This sample is available for subsequent sub-minute research and is not an input
feature in the current minute-model validation.

There are 5,404 skipped numeric ID values across 5,349 transitions. IDs remain
strictly increasing. The documented ID field does not establish consecutive
numbering; these gaps are disclosed, not silently filled. Exact independent
candle counts and OHLCV/flow agreement gate normalization, but do not prove that
every possible exchange event is present.

```powershell
python -m research.futures_trades --day 2026-07-20 --candles data/research/btcusdt_um_1m_2026_05_07_26.jsonl --output-dir data/research/btcusdt_um_trades_new_audit
```

The completed report and five-second JSONL are in
`data/research/btcusdt_um_trades_2026_07_20_audited/`. The initial audit, which
conservatively rejected numeric ID gaps, remains separately recorded without
normalized output. One day tests data plumbing, not model profitability.
The checked July 20 historical `bookTicker` archive URL returned 404; matching
quote history was not acquired. No order-book or quote data is synthesized.

## Next experiment

See [cost validation results](pillar_two_ourbit_cost_results.md). Extend audited
individual trades across complete development folds, then register a causal
trade-intensity/aggressor-flow hypothesis before evaluating it. Obtain Ourbit
quotes before treating spread/slippage assumptions or hypothetical maker fills
as execution evidence. Preserve fresh future data for a configuration that
passes development first. Binance futures remain an explicitly labeled proxy.
