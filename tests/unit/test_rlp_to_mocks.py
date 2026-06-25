"""Unit tests for scripts/rlp_to_mocks.py (RLP ExecutionPayload stream -> JSONL)."""
import json
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import rlp_to_mocks as r2m  # noqa: E402


# ---------- minimal RLP encoder (test-only, mirrors the decoder) ----------

def _enc_len(length, offset):
    if length < 56:
        return bytes([offset + length])
    len_bytes = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([offset + 55 + len(len_bytes)]) + len_bytes


def _enc_bytes(b):
    if len(b) == 1 and b[0] < 0x80:
        return b
    return _enc_len(len(b), 0x80) + b


def _enc_list(items):
    body = b"".join(items)
    return _enc_len(len(body), 0xc0) + body


def _qty(n):
    """Minimal big-endian bytes of an integer (zero -> empty, RLP convention)."""
    if n == 0:
        return b""
    return n.to_bytes((n.bit_length() + 7) // 8, "big")


def _make_payload_record():
    """Build one length-prefixed RLP ExecutionPayloadV3 body for tests."""
    fields = [
        _enc_bytes(b"\x11" * 32),   # 0 parentHash
        _enc_bytes(b"\x22" * 20),   # 1 feeRecipient
        _enc_bytes(b"\x33" * 32),   # 2 stateRoot
        _enc_bytes(b"\x44" * 32),   # 3 receiptsRoot
        _enc_bytes(b"\x00" * 256),  # 4 logsBloom
        _enc_bytes(b"\x55" * 32),   # 5 prevRandao
        _enc_bytes(_qty(0x173ac71)),  # 6 blockNumber = 24358001
        _enc_bytes(_qty(0x6a19e001)),  # 7 gasLimit
        _enc_bytes(_qty(0x5a5da)),  # 8 gasUsed = 370138
        _enc_bytes(_qty(1780080641)),  # 9 timestamp
        _enc_bytes(b""),            # 10 extraData (empty)
        _enc_bytes(_qty(0x07)),     # 11 baseFeePerGas
        _enc_bytes(b"\x66" * 32),   # 12 blockHash
        _enc_list([_enc_bytes(b"\x02\xab\xcd"), _enc_bytes(b"\x03\x12")]),  # 13 transactions (typed)
        _enc_list([]),              # 14 withdrawals (empty)
        _enc_bytes(_qty(0)),        # 15 blobGasUsed = 0
        _enc_bytes(_qty(0)),        # 16 excessBlobGas = 0
    ]
    body = _enc_list(fields)
    return len(body).to_bytes(4, "big") + body


def test_convert_maps_fields_and_stamps_extras(tmp_path):
    stream = _make_payload_record() + _make_payload_record()
    in_path = tmp_path / "payloads.bin"
    out_path = tmp_path / "payloads.jsonl"
    in_path.write_bytes(stream)

    count, first_block, last_block = r2m.convert(
        str(in_path), str(out_path),
        newpayload_version=4,
        parent_beacon_block_root=r2m.ZERO_BEACON_ROOT,
        versioned_hashes=[],
        execution_requests=[],
        fork="prague",
    )

    assert count == 2
    assert first_block == last_block == 24358001

    records = [json.loads(line) for line in out_path.read_text().splitlines()]
    assert len(records) == 2
    rec = records[0]

    # Stamped extras travel with each record.
    assert rec["parent_beacon_block_root"] == r2m.ZERO_BEACON_ROOT
    assert rec["versioned_hashes"] == []
    assert rec["execution_requests"] == []
    assert rec["newpayload_version"] == 4
    assert rec["fork"] == "prague"

    p = rec["payload"]
    # DATA fields keep full-width hex (leading zeros preserved).
    assert p["parentHash"] == "0x" + "11" * 32
    assert p["feeRecipient"] == "0x" + "22" * 20
    assert p["logsBloom"] == "0x" + "00" * 256
    assert p["blockHash"] == "0x" + "66" * 32
    assert p["extraData"] == "0x"
    # QUANTITY fields are minimal hex.
    assert p["blockNumber"] == "0x173ac71"
    assert p["gasUsed"] == "0x5a5da"
    assert p["baseFeePerGas"] == "0x7"
    assert p["blobGasUsed"] == "0x0"
    assert p["excessBlobGas"] == "0x0"
    # Transactions are opaque 0x-hex blobs; withdrawals empty.
    assert p["transactions"] == ["0x02abcd", "0x0312"]
    assert p["withdrawals"] == []


def test_to_qty_and_to_data_encodings():
    assert r2m._to_qty(b"") == "0x0"
    assert r2m._to_qty(b"\x00") == "0x0"
    assert r2m._to_qty(b"\x01\x00") == "0x100"
    assert r2m._to_data(b"") == "0x"
    assert r2m._to_data(b"\x00\xab") == "0x00ab"


def test_iter_records_rejects_truncated_stream():
    body = _make_payload_record()
    truncated = body[:-5]  # claim more bytes than present
    import pytest
    with pytest.raises(ValueError):
        list(r2m.iter_records(truncated))
