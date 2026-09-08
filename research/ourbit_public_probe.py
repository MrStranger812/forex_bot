"""One bounded, unauthenticated probe of officially listed Ourbit futures data paths.

This saves raw evidence and failures, never configures an execution adapter or
claims that a Postman collection fully specifies futures/WebSocket behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COLLECTION_COMMIT = "a6fcd511859e84e09be81cbc649fbc13be83b2bc"
COLLECTION_URL = (
    "https://raw.githubusercontent.com/ourbitdevelop/ourbit-api-postman/"
    f"{COLLECTION_COMMIT}/OURBIT%20V1%20contract.postman_collection.json"
)
PUBLIC_PATHS = {
    "detail": "/api/v1/contract/detail",
    "deals": "/api/v1/contract/deals/BTC_USDT",
    "kline": "/api/v1/contract/kline/BTC_USDT",
    "ticker": "/api/v1/contract/ticker",
    "depth_commits": "/api/v1/contract/depth_commits/BTC_USDT/20",
}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def collection_paths(items: list[dict[str, Any]]) -> Iterator[str]:
    for item in items:
        if "item" in item:
            yield from collection_paths(item["item"])
        else:
            request = item.get("request", {})
            if request.get("method") == "GET":
                path = "/" + "/".join(request.get("url", {}).get("path", []))
                if path in PUBLIC_PATHS.values():
                    yield path


def fetch_public(url: str) -> bytes:
    allowed = {COLLECTION_URL} | {
        "https://contract.ourbit.com" + path for path in PUBLIC_PATHS.values()
    }
    if url not in allowed:
        raise ValueError("URL is not one of the reviewed public read-only resources")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self, req: urllib.request.Request, fp: Any, code: int, msg: str,
            headers: Any, newurl: str,
        ) -> None:
            return None

    request = urllib.request.Request(url, headers={"User-Agent": "pillar-two-research/1"})
    # No credentials, private paths, redirected hosts, or location overrides.
    with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
        payload: bytes = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("public response exceeds size limit")
    return payload


def probe(output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError("use a new directory to retain prior public evidence")
    collection = fetch_public(COLLECTION_URL)
    paths = set(collection_paths(json.loads(collection)["item"]))
    if paths != set(PUBLIC_PATHS.values()):
        raise ValueError("pinned official collection does not contain every reviewed path")
    output_dir.mkdir(parents=True)
    responses: list[dict[str, Any]] = []
    for name, path in PUBLIC_PATHS.items():
        url = "https://contract.ourbit.com" + path
        row: dict[str, Any] = {"name": name, "url": url, "method": "GET"}
        row["requested_at"] = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        try:
            payload = fetch_public(url)
            raw_path = output_dir / (name + ".raw")
            raw_path.write_bytes(payload)
            row.update({
                "transport_success": True, "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload), "raw_path": str(raw_path),
            })
            try:
                data = json.loads(payload)
                row["json_object"] = isinstance(data, dict)
                if isinstance(data, dict):
                    row["api_success_field"] = data.get("success")
                    row["api_code_field"] = data.get("code")
            except (ValueError, UnicodeDecodeError):
                row["json_object"] = False
        except (OSError, ValueError) as exc:
            row.update({"transport_success": False, "error_type": type(exc).__name__})
            row["error"] = str(exc)
            if isinstance(exc, urllib.error.HTTPError):
                row["http_status"] = exc.code
        row["received_at"] = datetime.now(UTC).isoformat()
        row["request_elapsed_ms"] = (time.perf_counter() - started) * 1000
        responses.append(row)
        print(f"{name}: {'response captured' if row['transport_success'] else row['error']}",
              flush=True)
    report = {
        "mode": "public_read_only_contract_probe", "collection_url": COLLECTION_URL,
        "collection_commit": COLLECTION_COMMIT,
        "collection_sha256": hashlib.sha256(collection).hexdigest(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "verified_payload_mapping": False, "live_enabled": False,
        "responses": responses,
        "limitations": [
            "Collection has duplicate/mislabeled examples and no complete response contract.",
            "Transport success is not payload, sequence, fee, or execution verification.",
            "Elapsed request time includes network/TLS; it is not measured order latency.",
        ],
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    probe(args.output_dir)


if __name__ == "__main__":
    main()
