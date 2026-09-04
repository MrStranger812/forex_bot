from __future__ import annotations

from collections import OrderedDict

from .schemas import NewsItem


class NewsDeduplicator:
    def __init__(self, capacity: int = 100_000) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._hashes: OrderedDict[str, None] = OrderedDict()
        self._urls: OrderedDict[str, None] = OrderedDict()

    def accept(self, item: NewsItem) -> bool:
        if item.content_hash in self._hashes or item.canonical_url in self._urls:
            return False
        self._hashes[item.content_hash] = None
        self._urls[item.canonical_url] = None
        while len(self._hashes) > self.capacity:
            self._hashes.popitem(last=False)
        while len(self._urls) > self.capacity:
            self._urls.popitem(last=False)
        return True
