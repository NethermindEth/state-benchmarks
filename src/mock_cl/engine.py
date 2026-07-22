"""Minimal Engine-API JSON-RPC client used by the mock CL drivers.

Wraps ``engine_forkchoiceUpdatedV{1..3}``, ``engine_newPayloadV{1..4}`` and
``engine_exchangeCapabilities`` with JWT(HS256) auth. Versions are auto-picked
from payload contents so the same driver works across Paris..Prague forks.
"""
import logging
import time
from typing import Any, Dict, List, Optional

import requests

try:
    from .jwt_auth import make_jwt
except ImportError:  # pragma: no cover - flat sys.path layout
    from jwt_auth import make_jwt

logger = logging.getLogger(__name__)

# newPayload version per fork name (lower-cased).
FORK_TO_NEWPAYLOAD_VERSION = {
    "paris": 1,
    "shanghai": 2,
    "cancun": 3,
    "prague": 4,
}


def newpayload_version_for_fork(fork: Optional[str]) -> Optional[int]:
    """Map a fork name to its ``engine_newPayload`` version, or None if unknown."""
    if not fork:
        return None
    return FORK_TO_NEWPAYLOAD_VERSION.get(fork.strip().lower())


class EngineClient:
    """Thin JWT-authenticated Engine-API JSON-RPC client."""

    def __init__(self, engine_url: str, secret: bytes, timeout: int = 60):
        self.engine_url = engine_url
        self.secret = secret
        self.timeout = timeout
        self._id = 0

    def _rpc(self, method: str, params: List[Any]) -> Any:
        """POST a JSON-RPC request; raise RuntimeError on HTTP/JSON-RPC error.

        Retries on the server-side RPC timeout (-32002, e.g. geth's authrpc
        30s write timeout on a slow newPayload) and on transport-level
        timeouts / connection errors — at bloatnet scale multi-second
        newPayloads routinely outlive a single HTTP round-trip, and the EL
        keeps executing the request either way, so a retried call just blocks
        on the chain lock and returns the real status once the block lands.
        ``self.timeout`` is the total wall-clock budget for one call: each
        retry's request timeout is capped to the remaining budget, and the
        call raises once the budget is exhausted. (-32002 is also the generic
        "resource unavailable" JSON-RPC code, so a permanent error with that
        code burns the budget before raising — the warning logs the error
        message to make that diagnosable.)
        """
        self._id += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": self._id}
        start = time.monotonic()
        last_error: Optional[str] = None
        while True:
            headers = {
                "Authorization": f"Bearer {make_jwt(self.secret)}",
                "Content-Type": "application/json",
            }
            remaining = self.timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise RuntimeError(
                    f"{method} exceeded the {self.timeout}s wall-clock budget"
                    f" (last error: {last_error or 'none'})"
                )
            try:
                response = requests.post(
                    self.engine_url, json=body, headers=headers,
                    timeout=max(1.0, remaining),
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "%s transport error after %.0fs (%s); retrying within budget",
                    method, time.monotonic() - start, last_error,
                )
                time.sleep(1)
                continue
            if response.status_code != 200:
                raise RuntimeError(f"{method} HTTP {response.status_code}: {response.text}")
            data = response.json()
            error = data.get("error")
            if error is not None:
                if error.get("code") == -32002 and time.monotonic() - start < self.timeout:
                    logger.warning(
                        "%s returned -32002 (%s) after %.0fs; treating as a "
                        "server-side timeout and retrying",
                        method, error.get("message") or "no message",
                        time.monotonic() - start,
                    )
                    time.sleep(1)
                    continue
                raise RuntimeError(f"{method} JSON-RPC error: {error}")
            return data.get("result")

    def forkchoice_updated(
        self,
        head: str,
        safe: Optional[str] = None,
        finalized: Optional[str] = None,
        payload_attributes: Optional[Dict[str, Any]] = None,
        version: int = 3,
    ) -> Dict[str, Any]:
        """Call ``engine_forkchoiceUpdatedV{version}``; safe/finalized default to head."""
        safe = safe if safe is not None else head
        finalized = finalized if finalized is not None else head
        state = {
            "headBlockHash": head,
            "safeBlockHash": safe,
            "finalizedBlockHash": finalized,
        }
        return self._rpc(f"engine_forkchoiceUpdatedV{version}", [state, payload_attributes])

    def new_payload(
        self,
        payload: Dict[str, Any],
        versioned_hashes: Optional[List[str]] = None,
        parent_beacon_block_root: Optional[str] = None,
        version: Optional[int] = None,
        execution_requests: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """Call ``engine_newPayloadV{version}``; auto-pick version from payload shape.

        Auto-selection (when ``version`` is None): V4 if execution requests are
        supplied (Prague); V3 if the payload carries blobGasUsed/excessBlobGas
        (Cancun); V2 if it carries withdrawals (Shanghai); else V1 (Paris).

        Caveat: Cancun and Prague payloads are structurally identical (execution
        requests live NEXT TO the payload, not inside it), so a Prague block
        without a supplied ``execution_requests`` list auto-picks V3 and the EL
        answers -38005. Callers that know the fork must pass ``version``
        explicitly — the replay driver resolves it from the record's ``fork``
        field via :func:`newpayload_version_for_fork`.
        """
        if version is None:
            if execution_requests is not None:
                version = 4
            elif "blobGasUsed" in payload or "excessBlobGas" in payload:
                version = 3
            elif "withdrawals" in payload:
                version = 2
            else:
                version = 1

        if version == 1:
            params: List[Any] = [payload]
        elif version == 2:
            params = [payload]
        elif version == 3:
            params = [payload, versioned_hashes or [], parent_beacon_block_root]
        elif version == 4:
            params = [payload, versioned_hashes or [], parent_beacon_block_root, execution_requests or []]
        else:
            raise ValueError(f"unsupported newPayload version: {version}")

        return self._rpc(f"engine_newPayloadV{version}", params)

    def exchange_capabilities(self, methods: List[str]) -> List[str]:
        """Call ``engine_exchangeCapabilities`` to detect supported Engine methods."""
        result = self._rpc("engine_exchangeCapabilities", [methods])
        return result or []
