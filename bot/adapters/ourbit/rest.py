from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, fields
from typing import Any

import aiohttp

from .auth import signed_params


class GatewayError(RuntimeError):
    pass


class RateLimited(GatewayError):
    pass


class UnknownExecutionState(GatewayError):
    def __init__(self, client_order_id: str, message: str) -> None:
        super().__init__(message)
        self.client_order_id = client_order_id


@dataclass(frozen=True, slots=True)
class EndpointManifest:
    server_time: str = ""
    instruments: str = ""
    book_snapshot: str = ""
    balance: str = ""
    positions: str = ""
    open_orders: str = ""
    order: str = ""
    fills: str = ""
    funding: str = ""
    listen_key: str = ""
    verified: bool = False

    def require(self, name: str) -> str:
        value = getattr(self, name)
        if not value:
            raise GatewayError(f"unverified or missing futures endpoint: {name}")
        return str(value)

    def validate_live(self) -> None:
        if not self.verified:
            raise GatewayError("futures endpoint manifest is not verified")
        missing = [
            field.name
            for field in fields(self)
            if field.name != "verified" and not getattr(self, field.name)
        ]
        if missing:
            raise GatewayError(f"futures endpoint manifest missing: {', '.join(missing)}")


class OurbitRestClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        endpoints: EndpointManifest,
        *,
        timeout_seconds: float = 5.0,
        max_clock_skew_ms: int = 1000,
        max_read_retries: int = 3,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.endpoints = endpoints
        self.timeout_seconds = timeout_seconds
        self.max_clock_skew_ms = max_clock_skew_ms
        self.max_read_retries = max_read_retries
        self.clock_offset_ms = 0
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> OurbitRestClient:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, object] | None = None,
        *,
        private: bool = False,
        mutating_client_order_id: str | None = None,
    ) -> Any:
        if self._session is None:
            raise GatewayError("client must be used as an async context manager")
        request_params = dict(params or {})
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if private:
            if not self.api_key or not self.api_secret:
                raise GatewayError("API credentials are required")
            request_params.setdefault("timestamp", self.timestamp_ms())
            request_params = signed_params(request_params, self.api_secret)
            headers["X-OURBIT-APIKEY"] = self.api_key
        attempts = self.max_read_retries + 1 if method.upper() == "GET" else 1
        for attempt in range(attempts):
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    query_params = {key: str(value) for key, value in request_params.items()}
                    async with self._session.request(
                        method,
                        f"{self.base_url}{path}",
                        params=query_params,
                        headers=headers,
                    ) as response:
                        body = await response.text()
                        if response.status == 429:
                            if attempt + 1 < attempts:
                                retry_after = float(response.headers.get("Retry-After", 2**attempt))
                                await asyncio.sleep(min(max(retry_after, 0.01), 5.0))
                                continue
                            raise RateLimited(body)
                        if response.status >= 500 and mutating_client_order_id:
                            raise UnknownExecutionState(mutating_client_order_id, body)
                        if response.status >= 400:
                            raise GatewayError(f"HTTP {response.status}: {body}")
                        try:
                            return await response.json()
                        except (aiohttp.ContentTypeError, ValueError) as exc:
                            raise GatewayError("exchange returned non-JSON response") from exc
            except TimeoutError as exc:
                if mutating_client_order_id:
                    raise UnknownExecutionState(
                        mutating_client_order_id, "request timed out"
                    ) from exc
                raise GatewayError("request timed out") from exc
        raise GatewayError("request attempts exhausted")

    def timestamp_ms(self) -> int:
        return int(time.time() * 1000) + self.clock_offset_ms

    async def synchronize_time(self) -> int:
        started = int(time.time() * 1000)
        payload = await self._request("GET", self.endpoints.require("server_time"))
        ended = int(time.time() * 1000)
        server_ms = int(payload.get("serverTime", payload.get("server_time")))
        local_midpoint = (started + ended) // 2
        self.clock_offset_ms = server_ms - local_midpoint
        if abs(self.clock_offset_ms) > self.max_clock_skew_ms:
            raise GatewayError(f"clock skew {self.clock_offset_ms}ms exceeds limit")
        return self.clock_offset_ms

    async def discover_instruments(self) -> Any:
        return await self._request("GET", self.endpoints.require("instruments"))

    async def book_snapshot(self, symbol: str) -> Any:
        return await self._request(
            "GET", self.endpoints.require("book_snapshot"), {"symbol": symbol}
        )

    async def balance(self) -> Any:
        return await self._request("GET", self.endpoints.require("balance"), private=True)

    async def positions(self) -> Any:
        return await self._request("GET", self.endpoints.require("positions"), private=True)

    async def open_orders(self, symbol: str | None = None) -> Any:
        return await self._request(
            "GET", self.endpoints.require("open_orders"), {"symbol": symbol}, private=True
        )

    async def query_order(self, client_order_id: str) -> Any:
        return await self._request(
            "GET",
            self.endpoints.require("order"),
            {"clientOrderId": client_order_id},
            private=True,
        )

    async def submit_order(self, payload: dict[str, object]) -> Any:
        client_order_id = str(payload.get("clientOrderId", ""))
        if not client_order_id:
            raise ValueError("clientOrderId is required")
        return await self._request(
            "POST",
            self.endpoints.require("order"),
            payload,
            private=True,
            mutating_client_order_id=client_order_id,
        )

    async def cancel_order(self, symbol: str, client_order_id: str) -> Any:
        return await self._request(
            "DELETE",
            self.endpoints.require("order"),
            {"symbol": symbol, "clientOrderId": client_order_id},
            private=True,
            mutating_client_order_id=client_order_id,
        )

    async def fills(self, symbol: str) -> Any:
        return await self._request(
            "GET", self.endpoints.require("fills"), {"symbol": symbol}, private=True
        )

    async def funding_history(self, symbol: str) -> Any:
        return await self._request(
            "GET", self.endpoints.require("funding"), {"symbol": symbol}, private=True
        )

    async def create_listen_key(self) -> str:
        payload = await self._request("POST", self.endpoints.require("listen_key"), private=True)
        listen_key = str(payload.get("listenKey", ""))
        if not listen_key:
            raise GatewayError("listen-key response omitted listenKey")
        return listen_key

    async def keepalive_listen_key(self, listen_key: str) -> None:
        await self._request(
            "PUT",
            self.endpoints.require("listen_key"),
            {"listenKey": listen_key},
            private=True,
        )

    async def close_listen_key(self, listen_key: str) -> None:
        await self._request(
            "DELETE",
            self.endpoints.require("listen_key"),
            {"listenKey": listen_key},
            private=True,
        )
