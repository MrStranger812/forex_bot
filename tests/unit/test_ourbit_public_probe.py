from __future__ import annotations

import json
from pathlib import Path

import pytest

from research import ourbit_public_probe as public


def test_probe_retains_failures_and_does_not_promote_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        if url == public.COLLECTION_URL:
            return json.dumps({"item": [{"request": {
                "method": "GET", "url": {"path": path.strip("/").split("/")},
            }} for path in public.PUBLIC_PATHS.values()]}).encode()
        if url.endswith("detail"):
            return b'{"success":true,"code":0,"data":[]}'
        raise TimeoutError("fixture connection timeout")

    monkeypatch.setattr(public, "fetch_public", fetch)
    report = public.probe(tmp_path / "probe")
    assert len(calls) == 6
    assert sum(row["transport_success"] for row in report["responses"]) == 1
    assert report["verified_payload_mapping"] is False
    assert report["live_enabled"] is False
    assert (tmp_path / "probe" / "detail.raw").exists()


def test_private_and_arbitrary_urls_never_reach_network() -> None:
    for url in (
        "https://contract.ourbit.com/api/v1/private/account/assets",
        "https://example.org/api/v1/contract/detail",
    ):
        with pytest.raises(ValueError, match="reviewed public"):
            public.fetch_public(url)
