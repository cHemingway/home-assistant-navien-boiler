"""SigV4-presigned WebSocket path for AWS IoT Core MQTT (what the AWS IoT Android SDK does)."""

from datetime import UTC, datetime
import hashlib
import hmac
from urllib.parse import quote


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _q(value: str) -> str:
    return quote(value, safe="-_.~")


def presign_mqtt_path(
    host: str,
    region: str,
    service: str,
    access_key_id: str,
    secret_key: str,
    session_token: str | None,
    now: datetime | None = None,
) -> str:
    """Return `/mqtt?...` with SigV4 query auth. The session token is appended after signing, as IoT requires."""
    now = now or datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    scope = f"{date_stamp}/{region}/{service}/aws4_request"

    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key_id}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-SignedHeaders": "host",
    }
    query = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(params.items()))
    canonical_request = "\n".join(
        [
            "GET",
            "/mqtt",
            query,
            f"host:{host}\n",
            "host",
            hashlib.sha256(b"").hexdigest(),
        ]
    )
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    key = _hmac(f"AWS4{secret_key}".encode(), date_stamp)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    path = f"/mqtt?{query}&X-Amz-Signature={signature}"
    if session_token:
        path += f"&X-Amz-Security-Token={_q(session_token)}"
    return path
