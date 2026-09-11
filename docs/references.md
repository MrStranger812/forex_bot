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

## Gold and fundamental capture sources

Reviewed 2026-09-08/09:

- [Gold (XAU) listing](https://www.ourbit.com/support/articles/17827791513443),
  Ourbit, 2026-08-24: displayed GOLD(XAU)/USDT launch at 04:20 UTC. Official HTML
  retained locally. This does not establish an API symbol or full contract terms.
- [Federal Reserve RSS directory](https://www.federalreserve.gov/feeds/feeds.htm)
  identifies monetary-policy, press, and speech/testimony feeds. The
  [press-release index](https://www.federalreserve.gov/newsevents/pressreleases.htm)
  references its website script and the official
  [historical JSON index](https://www.federalreserve.gov/json/ne-press.json).
  Archive dates lack an explicit timezone, and some entries contain dates only.
- [BEA RSS](https://www.bea.gov/rss) redirects to the official
  [release feed](https://apps.bea.gov/rss/rss.xml). Capture uses the direct URL
  and publisher summaries; no article-body completeness is claimed.
- [BLS feed directory](https://www.bls.gov/feed/) identifies CPI and employment
  feeds. [BLS iCalendar help](https://www.bls.gov/help/hlpiCAL.htm) identifies its
  release calendar. Direct collection returns HTTP 403 in this environment;
  parsed test fixtures are not a claim of successful calendar collection.
- [Treasury XML documentation](https://home.treasury.gov/treasury-daily-interest-rate-xml-feed)
  specifies annual nominal/real yield feeds and history availability. Years
  2003 onward were captured for both curves. Missing yields are not zeroes;
  newly downloaded history does not prove what was available at earlier dates.
- [World Gold Council's gold framework](https://www.gold.org/goldhub/research/gold-outlook-2026)
  groups drivers into expansion, risk/uncertainty, opportunity cost, and momentum.
  This motivates the collection scope; its monthly framework is not evidence of
  profitable intraday XAU entries.

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

## ARB public reference candles

Reviewed 2026-09-12: [KuCoin futures candles](https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-klines)
and [contract metadata](https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-symbol).
Public responses verified `ARBUSDTM` as an open ARB/USDT-settled perpetual and the
minute candle schema used by the isolated observer. Raw probe evidence is saved in
`data/research/arb_feed_probe_20260912/`; ongoing responses and receipt times are in
the observation database. No signing, order semantics, or executable Ourbit prices
are inferred from this evidence. See [the ARB runbook](arb_prediction_watch.md).

## Deliberate non-assumption

An official futures Postman collection was located, but a complete current
futures response/WebSocket contract remains unverified. Its endpoint names alone
do not verify payload mappings, signing, sequence semantics, or order behavior.
Spot paths/topics must not be copied into futures configuration. All runtime
futures paths/topics stay blank by default and live execution remains disabled.
