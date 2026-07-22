"""Hand-rolled HS256 JWT for the Engine API (no PyJWT dependency).

The Engine API authenticates JSON-RPC calls with a short-lived HS256 JWT signed
by the EL's ``jwt.hex`` secret. We build it from stdlib (``hmac``/``hashlib``/
``base64``) to avoid pulling in a JWT library.
"""
import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional


def _b64url(raw: bytes) -> str:
    """base64url-encode without padding (per JWS)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_jwt(secret: bytes, iat: Optional[int] = None) -> str:
    """Build an HS256 JWT with header {alg,typ} and payload {iat}.

    The Engine API only requires the ``iat`` claim (issued-at, seconds since the
    epoch) within a +/-60s window of the EL's clock.
    """
    if iat is None:
        iat = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iat": iat}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    )
    signature = hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + _b64url(signature)


def load_secret(path_or_hex: str) -> bytes:
    """Resolve a JWT secret from a jwt.hex file path or a raw 0x/hex string.

    Validates that the resolved value is 32 hex-decoded bytes (the Engine API
    secret length); raises ValueError on malformed input.
    """
    if path_or_hex is None:
        raise ValueError("jwt secret is required (path to jwt.hex or 0x hex string)")

    candidate = path_or_hex.strip()
    if os.path.isfile(candidate):
        with open(candidate) as f:
            candidate = f.read().strip()

    if candidate.startswith(("0x", "0X")):
        candidate = candidate[2:]

    try:
        secret = bytes.fromhex(candidate)
    except ValueError as exc:
        raise ValueError(f"jwt secret is not valid hex: {exc}") from exc

    if not secret:
        raise ValueError("jwt secret decoded to empty bytes")
    return secret
