"""ExecutionPayload records: load from JSONL and record from a live EL.

The replay driver consumes :class:`PayloadRecord`s. A record bundles the
ExecutionPayload with the Cancun+ extras (versioned hashes, parent beacon block
root) that ``engine_newPayloadV3/V4`` require but that a plain
``eth_getBlockByNumber`` cannot supply — see :func:`record_from_el`.
"""
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class PayloadRecord:
    """One recorded block, ready to feed to ``engine_newPayload``."""

    payload: Dict[str, Any]
    block_hash: str
    block_number: int
    versioned_hashes: Optional[List[str]]
    parent_beacon_block_root: Optional[str]
    fork: Optional[str]


def _block_number(payload: Dict[str, Any]) -> int:
    raw = payload.get("blockNumber")
    if raw is None:
        return -1
    if isinstance(raw, int):
        return raw
    return int(raw, 16)


def load_payloads(path: str) -> Iterator[PayloadRecord]:
    """Yield :class:`PayloadRecord`s from a JSONL file.

    Each line is a JSON object with a required ``payload`` (ExecutionPayload) and
    optional ``versioned_hashes``, ``parent_beacon_block_root`` and ``fork``.
    """
    with open(path) as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_no}: missing or non-object 'payload'")
            yield PayloadRecord(
                payload=payload,
                block_hash=payload.get("blockHash"),
                block_number=_block_number(payload),
                versioned_hashes=obj.get("versioned_hashes"),
                parent_beacon_block_root=obj.get("parent_beacon_block_root"),
                fork=obj.get("fork"),
            )


# Block fields (eth_getBlockByNumber) -> ExecutionPayload fields. Same-named
# fields are listed for clarity; renamed fields use the (src, dst) form below.
_DIRECT_FIELDS = (
    "parentHash",
    "stateRoot",
    "receiptsRoot",
    "logsBloom",
    "gasLimit",
    "gasUsed",
    "timestamp",
    "extraData",
    "baseFeePerGas",
    "transactions",
)
_RENAMED_FIELDS = (
    ("miner", "feeRecipient"),
    ("mixHash", "prevRandao"),
    ("number", "blockNumber"),
    ("hash", "blockHash"),
)
# Cancun+ block fields that map straight onto the ExecutionPayload.
_OPTIONAL_FIELDS = ("withdrawals", "blobGasUsed", "excessBlobGas")


def _block_to_payload(block: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort eth_getBlockByNumber block -> ExecutionPayload-shaped dict."""
    payload: Dict[str, Any] = {}
    for field in _DIRECT_FIELDS:
        if field in block:
            payload[field] = block[field]
    for src, dst in _RENAMED_FIELDS:
        if src in block:
            payload[dst] = block[src]
    for field in _OPTIONAL_FIELDS:
        if block.get(field) is not None:
            payload[field] = block[field]
    return payload


def record_from_el(source_rpc_url: str, start_block: int, count: int, out_path: str) -> int:
    """Pull `count` full blocks from a live EL and write them as a JSONL payload file.

    Returns the number of records written. Best-effort: blob sidecars /
    versioned-hashes and parentBeaconBlockRoot are NOT recoverable from
    eth_getBlockByNumber alone, so Cancun+ (V3/V4) replay needs a true
    CL-recorded source instead — we log a clear warning when this matters.
    """
    written = 0
    warned_cancun = False
    with open(out_path, "w") as out:
        for n in range(start_block, start_block + count):
            block = _eth_get_block(source_rpc_url, n)
            if block is None:
                logger.warning("Block %d unavailable from %s; stopping.", n, source_rpc_url)
                break
            payload = _block_to_payload(block)
            if ("blobGasUsed" in payload or "excessBlobGas" in payload) and not warned_cancun:
                logger.warning(
                    "Block %d is Cancun+ (has blobGasUsed/excessBlobGas): blob "
                    "versioned-hashes and parentBeaconBlockRoot CANNOT be recovered "
                    "from eth_getBlockByNumber. The written records omit them, so "
                    "engine_newPayloadV3/V4 replay will be rejected. Use a true "
                    "CL-recorded source for Cancun+ replay.",
                    n,
                )
                warned_cancun = True
            record = {"payload": payload, "fork": None}
            out.write(json.dumps(record) + "\n")
            written += 1
    logger.info("Recorded %d blocks to %s", written, out_path)
    return written


def _eth_get_block(rpc_url: str, number: int, timeout: int = 30) -> Optional[Dict[str, Any]]:
    """eth_getBlockByNumber(number, full_txs=True); returns the block or None."""
    body = {
        "jsonrpc": "2.0",
        "method": "eth_getBlockByNumber",
        "params": [hex(number), True],
        "id": 1,
    }
    try:
        resp = requests.post(rpc_url, json=body, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("eth_getBlockByNumber(%d) HTTP %d", number, resp.status_code)
            return None
        return resp.json().get("result")
    except (requests.exceptions.RequestException, ValueError) as exc:
        logger.warning("eth_getBlockByNumber(%d) failed: %s", number, exc)
        return None
