from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime

import aiohttp

from .schemas import NewsItem, PillarOneOpinion

SYSTEM_PROMPT = """You classify short-lived crypto market impact.
Return one JSON object only with exactly these fields: direction
(bullish|bearish|neutral), confidence (0..1), strength (0..1),
horizon_seconds (>0), affected_symbols (BTC_USDT/ETH_USDT only),
event_type, and abstain (boolean). Use neutral with abstain=true when
evidence is ambiguous. Do not give trading instructions."""


class LlmUnavailable(RuntimeError):
    pass


class LlmOpinionClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        deadline_seconds: float = 1.5,
        max_horizon_seconds: int = 3600,
    ) -> None:
        self.url = f"{base_url.rstrip('/')}/v1/chat/completions"
        self.model = model
        self.api_key = api_key
        self.deadline_seconds = deadline_seconds
        self.max_horizon_seconds = max_horizon_seconds

    async def analyze(self, item: NewsItem, generated_at: datetime) -> PillarOneOpinion:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 100,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "published_at": item.published_at.isoformat(),
                            "received_at": item.received_at.isoformat(),
                            "symbols": item.symbols,
                            "headline": item.headline,
                            "body": item.body,
                            "source_reliability": item.source_reliability,
                        }
                    ),
                },
            ],
        }
        try:
            timeout = aiohttp.ClientTimeout(total=self.deadline_seconds)
            async with (
                aiohttp.ClientSession(headers=headers) as session,
                session.post(self.url, json=payload, timeout=timeout) as response,
            ):
                if response.status != 200:
                    raise LlmUnavailable(f"LLM HTTP {response.status}")
                result = await response.json()
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            raise LlmUnavailable("LLM request failed") from exc
        try:
            raw = result["choices"][0]["message"]["content"]
            opinion = PillarOneOpinion.from_json(
                raw, generated_at=generated_at, source_content_hash=item.content_hash
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmUnavailable("LLM output failed strict validation") from exc
        if opinion.horizon_seconds > self.max_horizon_seconds:
            raise LlmUnavailable("LLM horizon exceeds configured limit")
        age_seconds = (generated_at - item.published_at).total_seconds()
        if age_seconds < 0:
            raise LlmUnavailable("news publication timestamp is in the future")
        remaining = opinion.horizon_seconds - int(age_seconds)
        if remaining <= 0:
            raise LlmUnavailable("news is older than the permitted opinion horizon")
        return replace(opinion, horizon_seconds=remaining)
