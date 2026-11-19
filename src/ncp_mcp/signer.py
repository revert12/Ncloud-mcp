"""NCP API Gateway request signing (signature v2, HMAC-SHA256).

Reference: https://api.ncloud-docs.com/docs/en/common-ncpapi
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


def make_signature(
    method: str, uri: str, timestamp: str, access_key: str, secret_key: str
) -> str:
    """Return the base64 signature for one request.

    ``uri`` is the path from the host root including the query string,
    e.g. ``/vserver/v2/getServerInstanceList?regionCode=KR``.
    """
    message = f"{method.upper()} {uri}\n{timestamp}\n{access_key}"
    digest = hmac.new(
        secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def signed_headers(
    method: str, uri: str, access_key: str, secret_key: str, timestamp: str | None = None
) -> dict[str, str]:
    ts = timestamp or str(int(time.time() * 1000))
    return {
        "x-ncp-apigw-timestamp": ts,
        "x-ncp-iam-access-key": access_key,
        "x-ncp-apigw-signature-v2": make_signature(method, uri, ts, access_key, secret_key),
    }
