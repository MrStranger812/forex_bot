import asyncio

import pytest
from aiohttp import web

from bot.adapters.ourbit.rest import (
    EndpointManifest,
    GatewayError,
    OurbitRestClient,
    RateLimited,
    UnknownExecutionState,
)


async def server(handler):
    app = web.Application()
    app.router.add_route("*", "/order", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    socket = site._server.sockets[0]  # noqa: SLF001
    return runner, f"http://127.0.0.1:{socket.getsockname()[1]}"


@pytest.mark.asyncio
async def test_http_5xx_order_is_unknown_not_failed() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(status=503, text="maybe accepted")

    runner, url = await server(handler)
    try:
        async with OurbitRestClient(
            url, "key", "secret", EndpointManifest(order="/order")
        ) as client:
            with pytest.raises(UnknownExecutionState) as raised:
                await client.submit_order({"clientOrderId": "unique"})
            assert raised.value.client_order_id == "unique"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_order_timeout_is_unknown() -> None:
    async def handler(_: web.Request) -> web.Response:
        await asyncio.sleep(0.05)
        return web.json_response({"ok": True})

    runner, url = await server(handler)
    try:
        async with OurbitRestClient(
            url, "key", "secret", EndpointManifest(order="/order"), timeout_seconds=0.001
        ) as client:
            with pytest.raises(UnknownExecutionState):
                await client.submit_order({"clientOrderId": "unique"})
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_rate_limit_is_explicit() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(status=429, text="slow down")

    runner, url = await server(handler)
    try:
        async with OurbitRestClient(
            url, "", "", EndpointManifest(order="/order"), max_read_retries=0
        ) as client:
            with pytest.raises(RateLimited):
                await client._request("GET", "/order")  # noqa: SLF001
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_get_rate_limit_retries_with_backoff() -> None:
    calls = 0

    async def handler(_: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return web.Response(status=429, headers={"Retry-After": "0.01"})
        return web.json_response({"ok": True})

    runner, url = await server(handler)
    try:
        async with OurbitRestClient(
            url, "", "", EndpointManifest(order="/order"), max_read_retries=2
        ) as client:
            assert await client._request("GET", "/order") == {"ok": True}  # noqa: SLF001
        assert calls == 3
    finally:
        await runner.cleanup()


def test_live_manifest_fails_closed() -> None:
    with pytest.raises(GatewayError):
        EndpointManifest(order="/order").validate_live()
    with pytest.raises(GatewayError):
        EndpointManifest(verified=True, order="/order").validate_live()
