import hashlib
import hmac

import pytest

from bot.adapters.ourbit.auth import canonical_query, sign_params


def test_canonical_query_sorts_and_skips_none() -> None:
    assert canonical_query({"z": 2, "a": "hello world", "empty": None}) == "a=hello+world&z=2"


def test_signature_matches_hmac_sha256() -> None:
    expected = hmac.new(b"secret", b"a=1&b=2", hashlib.sha256).hexdigest()
    assert sign_params({"b": 2, "a": 1}, "secret") == expected


def test_empty_secret_rejected() -> None:
    with pytest.raises(ValueError):
        sign_params({"a": 1}, "")
