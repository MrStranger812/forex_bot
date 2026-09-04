from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from urllib.parse import urlencode


def canonical_query(params: Mapping[str, object]) -> str:
    pairs = [(str(key), str(value)) for key, value in params.items() if value is not None]
    return urlencode(sorted(pairs), doseq=True)


def sign_params(params: Mapping[str, object], secret: str) -> str:
    if not secret:
        raise ValueError("API secret is empty")
    payload = canonical_query(params).encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def signed_params(params: Mapping[str, object], secret: str) -> dict[str, object]:
    result = dict(params)
    result["signature"] = sign_params(result, secret)
    return result
