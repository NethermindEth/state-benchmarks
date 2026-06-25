#!/usr/bin/env python3
"""
Convert a binary RLP ExecutionPayload stream into the JSONL the mock CL's
replay driver reads (src/mock_cl/payloads.py::load_payloads).

Input format: a stream of length-prefixed records, each a 4-byte big-endian
length followed by the RLP encoding of an ExecutionPayloadV3 (the 17-field
list: parentHash, feeRecipient, stateRoot, receiptsRoot, logsBloom, prevRandao,
blockNumber, gasLimit, gasUsed, timestamp, extraData, baseFeePerGas, blockHash,
transactions, withdrawals, blobGasUsed, excessBlobGas).

The Engine API extras that an ExecutionPayload does NOT carry (parentBeaconBlockRoot,
versioned hashes, execution requests) and the desired engine_newPayload version are
constant for our bloat datasets and supplied via CLI flags so they can change for
future datasets. Defaults match the EF 3.5x bloat blocks: a zero (NOT null)
parentBeaconBlockRoot, empty versioned_hashes / execution_requests, newPayloadV4.

Each output line:

    {"payload": {...}, "versioned_hashes": [], "parent_beacon_block_root": "0x00..00",
     "execution_requests": [], "newpayload_version": 4, "fork": "prague"}
"""
import argparse
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rlp_to_mocks")

ZERO_BEACON_ROOT = "0x" + "00" * 32


# ---------- minimal RLP decoder (no external deps) ----------

def _decode(b, o=0):
    """Decode one RLP item at offset `o`; return (item, next_offset).

    Byte strings decode to `bytes`; lists decode to `list`.
    """
    prefix = b[o]
    if prefix < 0x80:                       # single byte, value is itself
        return b[o:o + 1], o + 1
    if prefix < 0xb8:                       # short string (0..55 bytes)
        length = prefix - 0x80
        start = o + 1
        return b[start:start + length], start + length
    if prefix < 0xc0:                       # long string
        len_len = prefix - 0xb7
        length = int.from_bytes(b[o + 1:o + 1 + len_len], "big")
        start = o + 1 + len_len
        return b[start:start + length], start + length
    if prefix < 0xf8:                       # short list (0..55 bytes payload)
        length = prefix - 0xc0
        end = o + 1 + length
        items, cursor = [], o + 1
        while cursor < end:
            item, cursor = _decode(b, cursor)
            items.append(item)
        return items, end
    # long list
    len_len = prefix - 0xf7
    length = int.from_bytes(b[o + 1:o + 1 + len_len], "big")
    start = o + 1 + len_len
    end = start + length
    items, cursor = [], start
    while cursor < end:
        item, cursor = _decode(b, cursor)
        items.append(item)
    return items, end


def iter_records(data):
    """Yield each RLP body from the 4-byte-length-prefixed stream `data`."""
    off = 0
    n = len(data)
    while off < n:
        if off + 4 > n:
            raise ValueError(f"truncated length prefix at byte {off}")
        length = int.from_bytes(data[off:off + 4], "big")
        off += 4
        if off + length > n:
            raise ValueError(f"record at byte {off} claims {length} bytes, only {n - off} left")
        yield data[off:off + length]
        off += length


# ---------- hex encoding (Engine-API DATA vs QUANTITY) ----------

def _to_data(b):
    """Bytes -> 0x-prefixed hex DATA (fixed/variable length, leading zeros kept)."""
    return "0x" + b.hex()


def _to_qty(b):
    """Bytes -> 0x-prefixed minimal hex QUANTITY (no leading zeros, 0x0 for zero)."""
    value = int.from_bytes(b, "big") if b else 0
    return hex(value)


# ExecutionPayloadV3 field order -> (key, encoder). transactions/withdrawals handled separately.
_FIELDS = [
    ("parentHash", _to_data),
    ("feeRecipient", _to_data),
    ("stateRoot", _to_data),
    ("receiptsRoot", _to_data),
    ("logsBloom", _to_data),
    ("prevRandao", _to_data),
    ("blockNumber", _to_qty),
    ("gasLimit", _to_qty),
    ("gasUsed", _to_qty),
    ("timestamp", _to_qty),
    ("extraData", _to_data),
    ("baseFeePerGas", _to_qty),
    ("blockHash", _to_data),
    # 13: transactions, 14: withdrawals
    ("blobGasUsed", _to_qty),
    ("excessBlobGas", _to_qty),
]


def _withdrawal_to_obj(w):
    """RLP [index, validatorIndex, address, amount] -> Engine-API withdrawal object."""
    index, validator_index, address, amount = w
    return {
        "index": _to_qty(index),
        "validatorIndex": _to_qty(validator_index),
        "address": _to_data(address),
        "amount": _to_qty(amount),
    }


def payload_from_rlp(fields):
    """17-field decoded RLP list -> ExecutionPayload dict with 0x-hex values."""
    if not isinstance(fields, list) or len(fields) < 17:
        raise ValueError(f"expected a 17-field ExecutionPayloadV3 list, got {type(fields).__name__} "
                         f"with {len(fields) if isinstance(fields, list) else '?'} fields")
    payload = {}
    # Indices 0..12 then 15..16 are scalar/data fields; map via _FIELDS positions.
    scalar_indices = list(range(0, 13)) + [15, 16]
    for (key, enc), idx in zip(_FIELDS, scalar_indices):
        payload[key] = enc(fields[idx])
    payload["transactions"] = [_to_data(tx) for tx in fields[13]]
    payload["withdrawals"] = [_withdrawal_to_obj(w) for w in fields[14]]
    return payload


def _csv_list(raw):
    """Comma-separated CLI value -> list of trimmed non-empty strings (empty -> [])."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def convert(in_path, out_path, *, newpayload_version, parent_beacon_block_root,
            versioned_hashes, execution_requests, fork):
    """Read the RLP stream at `in_path`, write JSONL records to `out_path`.

    Returns (count, first_block, last_block).
    """
    with open(in_path, "rb") as f:
        data = f.read()

    count = 0
    first_block = last_block = None
    with open(out_path, "w") as out:
        for body in iter_records(data):
            decoded, _ = _decode(body)
            payload = payload_from_rlp(decoded)
            record = {
                "payload": payload,
                "versioned_hashes": versioned_hashes,
                "parent_beacon_block_root": parent_beacon_block_root,
                "execution_requests": execution_requests,
                "newpayload_version": newpayload_version,
                "fork": fork,
            }
            out.write(json.dumps(record) + "\n")
            block_number = int(payload["blockNumber"], 16)
            first_block = block_number if first_block is None else first_block
            last_block = block_number
            count += 1
    return count, first_block, last_block


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="rlp_to_mocks",
        description="Convert a length-prefixed RLP ExecutionPayload stream to mock-CL JSONL",
    )
    parser.add_argument("input", help="Binary RLP stream (4-byte length + RLP body per record)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output JSONL path (default: <input>.jsonl)")
    parser.add_argument("--newpayload-version", type=int, default=4,
                        help="engine_newPayload version to stamp into each record (default: 4)")
    parser.add_argument("--parent-beacon-block-root", default=ZERO_BEACON_ROOT,
                        help="parentBeaconBlockRoot for every record (default: zero hash, NOT null)")
    parser.add_argument("--versioned-hashes", default="",
                        help="Comma-separated versioned hashes for every record (default: none)")
    parser.add_argument("--execution-requests", default="",
                        help="Comma-separated execution requests for every record (default: none)")
    parser.add_argument("--fork", default="prague",
                        help="Fork name written to each record for readability (default: prague)")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    out_path = args.output or (args.input + ".jsonl")

    count, first_block, last_block = convert(
        args.input,
        out_path,
        newpayload_version=args.newpayload_version,
        parent_beacon_block_root=args.parent_beacon_block_root,
        versioned_hashes=_csv_list(args.versioned_hashes),
        execution_requests=_csv_list(args.execution_requests),
        fork=args.fork,
    )

    log.info("Wrote %d records to %s", count, out_path)
    if count:
        log.info("Block range: %d -> %d (newPayloadV%d, beacon root %s)",
                 first_block, last_block, args.newpayload_version, args.parent_beacon_block_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
