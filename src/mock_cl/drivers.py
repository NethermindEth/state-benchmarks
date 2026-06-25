"""Mock CL drivers: snap-sync pivot loop and recorded-payload replay.

:class:`PivotDriver` repeatedly forkchoice-updates an EL to a frozen pivot so it
snap-syncs to that state. :class:`ReplayDriver` feeds recorded payloads through
``engine_newPayload`` + ``engine_forkchoiceUpdated`` to drive execution under a
live head and measures per-block latency.
"""
import csv
import logging
import statistics
import time
from typing import Any, Dict, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)


def _percentile(values: List[float], pct: float) -> Optional[float]:
    """pct-th percentile (0..100) of `values`, or None when empty."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    quantiles = statistics.quantiles(values, n=100, method="inclusive")
    # quantiles[k] is the (k+1)-th percentile; clamp index into range.
    idx = min(max(int(pct) - 1, 0), len(quantiles) - 1)
    return quantiles[idx]


class PivotDriver:
    """Drive an EL to snap-sync to a frozen pivot via a repeated forkchoiceUpdated."""

    def __init__(
        self,
        engine,
        pivot_hash: str,
        interval: int = 12,
        version: int = 3,
        status_rpc_url: Optional[str] = None,
        pivot_number: Optional[int] = None,
    ):
        self.engine = engine
        self.pivot_hash = pivot_hash
        self.interval = interval
        self.version = version
        self.status_rpc_url = status_rpc_url
        self.pivot_number = pivot_number

    def tick(self) -> Dict[str, Any]:
        """One forkchoiceUpdated pointing head=safe=finalized at the frozen pivot."""
        return self.engine.forkchoice_updated(head=self.pivot_hash, version=self.version)

    def _status(self) -> Optional[Dict[str, Any]]:
        """Query the target's sync status; returns the synced view or None."""
        if not self.status_rpc_url:
            return None
        syncing = self._rpc("eth_syncing", [])
        head_hex = self._rpc("eth_blockNumber", [])
        try:
            head = int(head_hex, 16) if head_hex else 0
        except (TypeError, ValueError):
            head = 0
        synced = (
            syncing is False
            and head > 0
            and (self.pivot_number is None or head >= self.pivot_number - 32)
        )
        return {"syncing": syncing, "head": head, "synced": synced}

    def _rpc(self, method: str, params: List[Any]) -> Any:
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        try:
            resp = requests.post(self.status_rpc_url, json=body, timeout=5)
            if resp.status_code != 200:
                return None
            return resp.json().get("result")
        except (requests.exceptions.RequestException, ValueError):
            return None

    def run(self, stop_event=None, max_seconds: Optional[int] = None) -> Dict[str, Any]:
        """Loop tick() every `interval`s until stopped / timed out / target synced.

        Per-tick exceptions are caught and logged at debug so a transient EL
        hiccup never kills the resilient loop.
        """
        start = time.time()
        ticks = 0
        synced = False
        last_status: Optional[Dict[str, Any]] = None

        while True:
            if stop_event is not None and stop_event.is_set():
                break
            if max_seconds is not None and (time.time() - start) >= max_seconds:
                break
            try:
                self.tick()
            except Exception as exc:  # resilient loop: keep driving the pivot
                logger.debug("pivot tick failed (continuing): %s", exc)
            ticks += 1

            last_status = self._status()
            if last_status is not None and last_status.get("synced"):
                synced = True
                break

            time.sleep(self.interval)

        return {
            "ticks": ticks,
            "elapsed_s": time.time() - start,
            "synced": synced,
            "last_status": last_status,
        }


class ReplayDriver:
    """Replay recorded payloads to drive execution and measure per-block latency."""

    def __init__(
        self,
        engine,
        records: Iterable,
        advance_head: bool = True,
        count: Optional[int] = None,
        latency_csv: Optional[str] = None,
        stop_event=None,
        newpayload_version: Optional[int] = None,
    ):
        self.engine = engine
        self.records = records
        self.advance_head = advance_head
        self.count = count
        self.latency_csv = latency_csv
        self.stop_event = stop_event
        # When set, forces engine_newPayload version for ALL records (overriding the
        # per-record newpayload_version); these are Prague blocks whose auto-pick
        # otherwise lands on V3. None => fall back to each record's version (or auto).
        self.newpayload_version = newpayload_version

    def run(self) -> Dict[str, Any]:
        """Drive newPayload(+forkchoiceUpdated) per record; return latency summary."""
        rows: List[Dict[str, Any]] = []
        new_payload_ms: List[float] = []
        fcu_ms: List[float] = []

        for i, record in enumerate(self.records):
            if self.count is not None and i >= self.count:
                break
            if self.stop_event is not None and self.stop_event.is_set():
                break

            version = self.newpayload_version or record.newpayload_version
            t0 = time.perf_counter()
            np_result = self.engine.new_payload(
                record.payload,
                versioned_hashes=record.versioned_hashes,
                parent_beacon_block_root=record.parent_beacon_block_root,
                version=version,
                execution_requests=record.execution_requests,
            )
            np_ms = (time.perf_counter() - t0) * 1000.0
            new_payload_ms.append(np_ms)

            status = (np_result or {}).get("status")
            if status == "INVALID":
                rows.append({
                    "block_number": record.block_number,
                    "new_payload_ms": np_ms,
                    "fcu_ms": None,
                    "status": status,
                })
                self._maybe_write_csv(rows)
                raise RuntimeError(
                    f"newPayload returned INVALID at block {record.block_number}: {np_result}"
                )

            fcu_ms_val: Optional[float] = None
            if status in ("VALID", "ACCEPTED") and self.advance_head:
                t1 = time.perf_counter()
                self.engine.forkchoice_updated(head=record.block_hash)
                fcu_ms_val = (time.perf_counter() - t1) * 1000.0
                fcu_ms.append(fcu_ms_val)

            rows.append({
                "block_number": record.block_number,
                "new_payload_ms": np_ms,
                "fcu_ms": fcu_ms_val,
                "status": status,
            })

        self._maybe_write_csv(rows)

        return {
            "blocks": len(rows),
            "new_payload_ms_p50": _percentile(new_payload_ms, 50),
            "new_payload_ms_p95": _percentile(new_payload_ms, 95),
            "fcu_ms_p50": _percentile(fcu_ms, 50),
            "fcu_ms_p95": _percentile(fcu_ms, 95),
        }

    def _maybe_write_csv(self, rows: List[Dict[str, Any]]) -> None:
        if not self.latency_csv:
            return
        with open(self.latency_csv, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["block_number", "new_payload_ms", "fcu_ms", "status"]
            )
            writer.writeheader()
            writer.writerows(rows)
