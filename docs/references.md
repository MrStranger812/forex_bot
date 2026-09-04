# External references and verification boundary

Reviewed on 2026-09-04. Re-check before every live promotion because exchange terms and APIs change.

## Ourbit primary sources

- [User Agreement](https://www.ourbit.com/terms) — updated May 30, 2026; excluded jurisdictions include Iran and prohibit false location representations.
- [API Creation Guide](https://www.ourbit.com/support/articles/17827791513175) — personal keys expire after 180 days and must be protected like passwords.
- [Spot V3 API documentation](https://ourbitdevelop.github.io/apidocs/spot_v3_en/) — documents signing, 5xx unknown execution semantics, the 24-hour WebSocket lifetime, heartbeat, subscription limits, and spot depth behavior. These details are not assumed to be the futures contract.
- [Futures Trading Tutorial](https://www.ourbit.com/support/articles/17827791511250) — describes USDT-M perpetual futures and states Coin-M is not currently supported.

## Training/runtime sources

- [Transformers bitsandbytes quantization](https://huggingface.co/docs/transformers/main/quantization/bitsandbytes) — NF4 and `torch.bfloat16` four-bit configuration.
- [TRL SFTTrainer](https://huggingface.co/docs/trl/sft_trainer) — conversational datasets and assistant-only loss requirements.

## Deliberate non-assumption

No complete current official Ourbit futures API contract was located. Spot endpoint paths, WebSocket topics, payload mappings, and signing behavior must not be copied into live futures configuration without current official confirmation and sanitized read-only fixtures. This is why all futures paths/topics are blank by default and live execution remains disabled.
