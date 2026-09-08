# External references and verification boundary

Reviewed on 2026-09-08. Re-check before every live promotion because exchange terms and APIs change.

## Ourbit primary sources

- [Published futures fees](https://www.ourbit.com/support/articles/17827791510072) —
  2024-09-03 reference: maker 0.02%, taker 0.04%; re-read on 2026-09-08.
  Account fees remain unverified. The [Taiwan announcement](https://www.ourbit.com/support/articles/17827791511088)
  demonstrates regional variation, including VIP 0 taker 0.05%.
- [Official futures Postman collection, pinned revision](https://github.com/ourbitdevelop/ourbit-api-postman/blob/a6fcd511859e84e09be81cbc649fbc13be83b2bc/OURBIT%20V1%20contract.postman_collection.json) —
  partial public contract with duplicate/mislabeled examples. Five public probes
  failed at TLS. See [data and costs evidence](ourbit_data_and_costs.md).

- [User Agreement](https://www.ourbit.com/terms) — updated May 30, 2026; excluded jurisdictions include Iran and prohibit false location representations.
- [API Creation Guide](https://www.ourbit.com/support/articles/17827791513175) — personal keys expire after 180 days and must be protected like passwords.
- [Spot V3 API documentation](https://ourbitdevelop.github.io/apidocs/spot_v3_en/) — documents signing, 5xx unknown execution semantics, the 24-hour WebSocket lifetime, heartbeat, subscription limits, and spot depth behavior. These details are not assumed to be the futures contract.
- [Futures Trading Tutorial](https://www.ourbit.com/support/articles/17827791511250) — describes USDT-M perpetual futures and states Coin-M is not currently supported.

## Training/runtime sources

- [Binance public market-data archives](https://github.com/binance/binance-public-data) —
  official spot/USDT-M candle and individual-trade schemas, archive layout, SHA256
  sidecars, and the January 2025 spot timestamp change to microseconds. Used for
  explicitly labeled BTC research proxies, not Ourbit metadata or fees.

- [Transformers bitsandbytes quantization](https://huggingface.co/docs/transformers/main/quantization/bitsandbytes) — NF4 and `torch.bfloat16` four-bit configuration.
- [TRL SFTTrainer](https://huggingface.co/docs/trl/sft_trainer) — conversational datasets and assistant-only loss requirements.

## Signal research sources

- [The Price Impact of Order Book Events](https://arxiv.org/abs/1011.6402),
  Cont, Kukanov, and Stoikov, 2014 journal / 2011 author revision. Equity
  contemporaneous impact evidence; candle trade flow is not full book OFI.
- [Fragmentation, Price Formation, and Cross-Impact in Bitcoin Markets](https://www.stats.ox.ac.uk/~cucuring/fragmentation_bitcoin_markets_arXiv.pdf),
  Albers and colleagues, 2021 author manuscript. Sections 3.1.2, 3.1.4, and 5.1
  motivate magnitude/basis features and document negative default-fee baseline
  PnL. Subsecond forecasts do not establish minute-bar profitability.
- [Bitcoin intraday time series momentum](https://onlinelibrary.wiley.com/doi/abs/10.1111/fire.12290),
  Shen, Urquhart, and Wang, 2022 journal / 2021 online. Publisher abstract supports
  volume-conditioned session momentum; the project's rolling rule is not a replication.
- [Market impact and efficiency in cryptoassets markets](https://link.springer.com/article/10.1007/s42521-023-00095-9),
  Barucci and colleagues, 2023. Full text distinguishes contemporaneous impact
  from lagged prediction, including weak forward explanatory power for crypto pairs.
- [Technical trading and cryptocurrencies](https://link.springer.com/article/10.1007/s10479-019-03357-1),
  Hudson and Urquhart, 2021 journal / 2019 online. Section 6.5 reports failed
  out-of-sample Bitcoin channel-breakout performance after in-sample selection.
- [A Reality Check for Data Snooping](https://doi.org/10.1111/1468-0262.00152),
  White, 2000, and [The probability of backtest overfitting](https://escholarship.org/uc/item/4w1110bb),
  Bailey and colleagues, 2017. Primary abstracts support accounting for strategy
  search. The project's descriptive fold bootstrap is neither exact Reality Check
  nor PBO, and cannot correct prior research on these dates.

Reviewed 2026-09-08. See [signal research and results](pillar_two_signal_research.md)
for implemented hypotheses and the boundary between source evidence and inference.

## Deliberate non-assumption

An official futures Postman collection was located, but a complete current
futures response/WebSocket contract remains unverified. Its endpoint names alone
do not verify payload mappings, signing, sequence semantics, or order behavior.
Spot paths/topics must not be copied into futures configuration. All runtime
futures paths/topics stay blank by default and live execution remains disabled.
