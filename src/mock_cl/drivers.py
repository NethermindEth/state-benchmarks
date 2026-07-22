"""Mock CL drivers: snap-sync pivot loop and recorded-payload replay.

:class:`PivotDriver` repeatedly forkchoice-updates an EL to a frozen pivot so it
snap-syncs to that state. :class:`ReplayDriver` feeds recorded payloads through
``engine_newPayload`` + ``engine_forkchoiceUpdated`` to drive execution under a
live head and measures per-block latency.
"""
import csv
import logging
import os
import statistics
import time
from typing import Any, Dict, Iterable, List, Optional

import requests

try:
    from .engine import newpayload_version_for_fork
except ImportError:  # pragma: no cover - flat sys.path layout
    from engine import newpayload_version_for_fork

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
        fcu_wait_seconds: float = 600.0,
    ):
        self.engine = engine
        self.records = records
        self.advance_head = advance_head
        self.count = count
        self.latency_csv = latency_csv
        self.stop_event = stop_event
        # When set, forces engine_newPayload version for ALL records (overriding the
        # per-record newpayload_version); these are Prague blocks whose auto-pick
        # otherwise lands on V3. None => per-record version, then the record's
        # fork name, then the engine's shape-based auto-pick.
        self.newpayload_version = newpayload_version
        # How long to poll FCU for a SYNCING (async-queued) block to land before
        # giving up. Deliberately its own knob: the engine's per-request timeout
        # bounds one HTTP round-trip, while this bounds a block's whole async
        # execution, which at bloatnet scale can far exceed a single request.
        self.fcu_wait_seconds = fcu_wait_seconds

    def run(self) -> Dict[str, Any]:
        """Drive newPayload(+forkchoiceUpdated) per record; return latency summary."""
        rows: List[Dict[str, Any]] = []
        new_payload_ms: List[float] = []
        fcu_ms: List[float] = []
        sync_wait_ms: List[float] = []
        prev_hash: Optional[str] = None

        for i, record in enumerate(self.records):
            if self.count is not None and i >= self.count:
                break
            if self.stop_event is not None and self.stop_event.is_set():
                break

            # Validate parentHash chaining before touching the engine: a gapped
            # / off-by-one payload file makes every newPayload return SYNCING
            # (parent unknown) and the FCU poll spin fcu_wait_seconds per block
            # — a multi-hour silent hang that mis-measures every block.
            parent = record.payload.get("parentHash")
            if prev_hash is None:
                # First record: the EL must already know its parent (the file
                # must start at snapshot head + 1). FCU to a known hash returns
                # VALID (an ancestor just reorgs — harmless under overlay);
                # an unknown hash returns SYNCING, which is the gapped base.
                # Skipped when advance_head is off — that mode promises not to
                # touch the head, and an FCU could reorg it.
                if self.advance_head:
                    base = self.engine.forkchoice_updated(head=parent)
                    base_status = ((base or {}).get("payloadStatus") or {}).get("status")
                    if base_status != "VALID":
                        raise RuntimeError(
                            f"payload file base mismatch: EL does not recognize the first "
                            f"payload's parent {parent} (block {record.block_number}, FCU "
                            f"status {base_status}); the file must start at snapshot head + 1"
                        )
            elif parent != prev_hash:
                raise RuntimeError(
                    f"payload chain gap at block {record.block_number}: "
                    f"parentHash {parent} != previous blockHash {prev_hash}"
                )
            prev_hash = record.block_hash

            # Explicit override > per-record version > the record's fork name
            # (Cancun/Prague payloads are shape-identical, so the engine's
            # auto-pick can't tell them apart) > engine auto-pick.
            version = (
                self.newpayload_version
                or record.newpayload_version
                or newpayload_version_for_fork(record.fork)
            )
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

            def _fail(status_label: str, message: str) -> None:
                rows.append({
                    "block_number": record.block_number,
                    "new_payload_ms": np_ms,
                    "fcu_ms": None,
                    "sync_wait_ms": None,
                    "status": status_label,
                })
                self._maybe_write_csv(rows)
                raise RuntimeError(message)

            status = (np_result or {}).get("status")
            if status not in ("VALID", "ACCEPTED", "SYNCING"):
                # INVALID, INVALID_BLOCK_HASH, a None/unmodeled status, or a
                # null result all mean the block did NOT import — silently
                # recording the row and skipping the FCU would leave the head
                # at the snapshot base while the CSV looks full (a bogus run).
                _fail(
                    str(status) if status is not None else "NO_STATUS",
                    f"newPayload returned unexpected status {status!r} at block "
                    f"{record.block_number}: {np_result}",
                )

            fcu_ms_val: Optional[float] = None
            sync_wait_ms_val: Optional[float] = None
            if self.advance_head:
                # SYNCING = the EL queued the block for async processing (Nethermind
                # does this for heavy bloat blocks). An FCU sent now is a no-op for
                # the head, so poll FCU until the payload lands (VALID) — otherwise
                # the head freezes at the snapshot base while the replay burns the
                # whole payload file.
                wait_start = time.perf_counter()
                deadline = wait_start + self.fcu_wait_seconds
                while True:
                    t1 = time.perf_counter()
                    fcu_result = self.engine.forkchoice_updated(head=record.block_hash)
                    fcu_ms_val = (time.perf_counter() - t1) * 1000.0
                    fcu_status = ((fcu_result or {}).get("payloadStatus") or {}).get("status")
                    if fcu_status == "VALID":
                        break
                    if fcu_status == "INVALID":
                        _fail(
                            f"FCU_{fcu_status}",
                            f"forkchoiceUpdated returned INVALID at block "
                            f"{record.block_number}: {fcu_result}",
                        )
                    if time.perf_counter() > deadline:
                        _fail(
                            f"FCU_TIMEOUT_{fcu_status}",
                            f"forkchoiceUpdated stuck on {fcu_status} for "
                            f"{self.fcu_wait_seconds:g}s at block {record.block_number}",
                        )
                    time.sleep(2.0)
                # fcu_ms is the final (successful) FCU call only; the poll sleeps
                # and the block's async execution go to sync_wait_ms so the FCU
                # percentiles stay comparable across clients and runs.
                fcu_ms.append(fcu_ms_val)
                if status == "SYNCING":
                    sync_wait_ms_val = (time.perf_counter() - wait_start) * 1000.0
                    sync_wait_ms.append(sync_wait_ms_val)

            rows.append({
                "block_number": record.block_number,
                "new_payload_ms": np_ms,
                "fcu_ms": fcu_ms_val,
                "sync_wait_ms": sync_wait_ms_val,
                # A SYNCING newPayload that the FCU poll drove to VALID did land;
                # keep it distinguishable from a block that never imported.
                "status": "SYNCING_LANDED" if status == "SYNCING" and fcu_ms_val is not None else status,
            })
            # Flush per block: replays die mid-run (timeouts, kills) and an
            # end-of-loop-only flush loses every row when they do.
            self._maybe_write_csv(rows)

        # Also flush after the loop: with zero records this is the only writer,
        # and run_benchmark.sh existence-checks the CSV to tell "drove 0 blocks"
        # from "produced no latency CSV".
        self._maybe_write_csv(rows)

        return {
            "blocks": len(rows),
            "new_payload_ms_p50": _percentile(new_payload_ms, 50),
            "new_payload_ms_p95": _percentile(new_payload_ms, 95),
            "fcu_ms_p50": _percentile(fcu_ms, 50),
            "fcu_ms_p95": _percentile(fcu_ms, 95),
            "sync_wait_ms_p50": _percentile(sync_wait_ms, 50),
            "sync_wait_ms_p95": _percentile(sync_wait_ms, 95),
        }

    def _maybe_write_csv(self, rows: List[Dict[str, Any]]) -> None:
        if not self.latency_csv:
            return
        # Write to a temp file and atomically rename: this flush runs per block,
        # and a kill (the runner's exit trap) landing mid-rewrite would otherwise
        # leave a truncated CSV (lost tail rows).
        tmp = self.latency_csv + ".tmp"
        with open(tmp, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["block_number", "new_payload_ms", "fcu_ms", "sync_wait_ms", "status"]
            )
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, self.latency_csv)
