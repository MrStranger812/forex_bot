from __future__ import annotations

from datetime import datetime

from bot.domain.events import Direction, ensure_utc
from bot.news.schemas import PillarOneOpinion


class PillarOneBook:
    """Latest independent opinion per symbol; conflicting fresh opinions abstain."""

    def __init__(self) -> None:
        self._opinions: dict[str, list[PillarOneOpinion]] = {}

    def publish(self, opinion: PillarOneOpinion) -> None:
        for symbol in opinion.affected_symbols:
            current = self._opinions.setdefault(symbol, [])
            if any(item.source_content_hash == opinion.source_content_hash for item in current):
                return
            current.append(opinion)
            if len(current) > 100:
                del current[:-100]

    def consensus(self, symbol: str, now: datetime) -> PillarOneOpinion | None:
        now = ensure_utc(now)
        fresh = [item for item in self._opinions.get(symbol, []) if item.applies_to(symbol, now)]
        if not fresh:
            return None
        directions = {item.direction for item in fresh}
        if len(directions) != 1 or Direction.NEUTRAL in directions:
            return None
        return max(fresh, key=lambda item: (item.generated_at, item.confidence))
