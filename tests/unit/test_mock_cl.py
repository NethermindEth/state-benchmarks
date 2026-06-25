"""Unit tests for the mock consensus-layer Engine-API driver (src/mock_cl)."""
import base64
import csv
import hashlib
import hmac
import json
import threading

import pytest

import drivers
import engine as engine_mod
import jwt_auth
import payloads as payloads_mod
import runner
from drivers import PivotDriver, ReplayDriver
from engine import EngineClient, newpayload_version_for_fork


# ---------- jwt_auth ----------

def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def test_make_jwt_structure_and_signature():
    secret = b"\x01" * 32
    token = jwt_auth.make_jwt(secret, iat=1700000000)
    header_b64, payload_b64, sig_b64 = token.split(".")

    assert json.loads(_b64url_decode(header_b64)) == {"alg": "HS256", "typ": "JWT"}
    payload = json.loads(_b64url_decode(payload_b64))
    assert isinstance(payload["iat"], int)
    assert payload["iat"] == 1700000000

    expected_sig = hmac.new(
        secret, f"{header_b64}.{payload_b64}".encode("ascii"), hashlib.sha256
    ).digest()
    assert _b64url_decode(sig_b64) == expected_sig


def test_make_jwt_defaults_iat_to_now():
    token = jwt_auth.make_jwt(b"\x02" * 32)
    payload = json.loads(_b64url_decode(token.split(".")[1]))
    assert isinstance(payload["iat"], int) and payload["iat"] > 0


def test_load_secret_accepts_hex_string():
    secret = jwt_auth.load_secret("0x" + "ab" * 32)
    assert secret == bytes.fromhex("ab" * 32)
    # without 0x prefix too
    assert jwt_auth.load_secret("cd" * 32) == bytes.fromhex("cd" * 32)


def test_load_secret_accepts_file(tmp_path):
    p = tmp_path / "jwt.hex"
    p.write_text("0x" + "ef" * 32 + "\n")
    assert jwt_auth.load_secret(str(p)) == bytes.fromhex("ef" * 32)


def test_load_secret_rejects_garbage():
    with pytest.raises(ValueError):
        jwt_auth.load_secret("not-hex-zz")


# ---------- engine: version selection ----------

def test_newpayload_version_for_fork_mapping():
    assert newpayload_version_for_fork("paris") == 1
    assert newpayload_version_for_fork("Shanghai") == 2
    assert newpayload_version_for_fork("cancun") == 3
    assert newpayload_version_for_fork("PRAGUE") == 4
    assert newpayload_version_for_fork(None) is None
    assert newpayload_version_for_fork("unknown") is None


def _capture_rpc(client):
    """Monkeypatch client._rpc to record (method, params) and return canned status."""
    calls = []

    def _fake(method, params):
        calls.append((method, params))
        return {"status": "VALID"}

    client._rpc = _fake
    return calls


def test_new_payload_auto_version_v1_plain():
    client = EngineClient("http://x", b"\x00" * 32)
    calls = _capture_rpc(client)
    client.new_payload({"blockHash": "0xaa", "blockNumber": "0x1"})
    assert calls[0][0] == "engine_newPayloadV1"
    assert calls[0][1] == [{"blockHash": "0xaa", "blockNumber": "0x1"}]


def test_new_payload_auto_version_v2_withdrawals():
    client = EngineClient("http://x", b"\x00" * 32)
    calls = _capture_rpc(client)
    client.new_payload({"blockHash": "0xaa", "withdrawals": []})
    assert calls[0][0] == "engine_newPayloadV2"


def test_new_payload_auto_version_v3_blobgas():
    client = EngineClient("http://x", b"\x00" * 32)
    calls = _capture_rpc(client)
    client.new_payload(
        {"blockHash": "0xaa", "blobGasUsed": "0x0", "excessBlobGas": "0x0"},
        versioned_hashes=["0xvh"],
        parent_beacon_block_root="0xbeacon",
    )
    method, params = calls[0]
    assert method == "engine_newPayloadV3"
    assert params[1] == ["0xvh"]
    assert params[2] == "0xbeacon"


def test_new_payload_v4_when_execution_requests():
    client = EngineClient("http://x", b"\x00" * 32)
    calls = _capture_rpc(client)
    client.new_payload({"blockHash": "0xaa"}, execution_requests=[])
    method, params = calls[0]
    assert method == "engine_newPayloadV4"
    assert params[3] == []


# ---------- engine: forkchoice_updated param shape ----------

def test_forkchoice_updated_defaults_and_shape():
    client = EngineClient("http://x", b"\x00" * 32)
    calls = []

    def _fake(method, params):
        calls.append((method, params))
        return {"payloadStatus": {"status": "VALID"}}

    client._rpc = _fake
    client.forkchoice_updated(head="0xhead")

    method, params = calls[0]
    assert method == "engine_forkchoiceUpdatedV3"
    state, attrs = params
    assert state == {
        "headBlockHash": "0xhead",
        "safeBlockHash": "0xhead",
        "finalizedBlockHash": "0xhead",
    }
    assert attrs is None


# ---------- payloads: load_payloads ----------

def test_load_payloads_parses_jsonl(tmp_path):
    p = tmp_path / "payloads.jsonl"
    lines = [
        {"payload": {"blockHash": "0xaaa", "blockNumber": "0x10", "withdrawals": []}},
        {
            "payload": {"blockHash": "0xbbb", "blockNumber": "0x11", "blobGasUsed": "0x0"},
            "versioned_hashes": ["0xvh"],
            "parent_beacon_block_root": "0xbeacon",
            "fork": "cancun",
        },
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")

    records = list(payloads_mod.load_payloads(str(p)))
    assert len(records) == 2
    assert records[0].block_hash == "0xaaa"
    assert records[0].block_number == 16
    assert records[1].block_hash == "0xbbb"
    assert records[1].block_number == 17
    assert records[1].versioned_hashes == ["0xvh"]
    assert records[1].parent_beacon_block_root == "0xbeacon"
    assert records[1].fork == "cancun"


def test_load_payloads_stamps_zero_beacon_root_and_reads_v4_fields(tmp_path):
    p = tmp_path / "payloads.jsonl"
    lines = [
        # No parent_beacon_block_root -> stamped with the zero hash (not null).
        {"payload": {"blockHash": "0xaaa", "blockNumber": "0x10", "blobGasUsed": "0x0"},
         "versioned_hashes": [], "execution_requests": [], "newpayload_version": 4},
        # Explicit null -> also stamped.
        {"payload": {"blockHash": "0xbbb", "blockNumber": "0x11"},
         "parent_beacon_block_root": None},
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")

    records = list(payloads_mod.load_payloads(str(p)))
    assert records[0].parent_beacon_block_root == payloads_mod.ZERO_BEACON_ROOT
    assert records[0].execution_requests == []
    assert records[0].newpayload_version == 4
    assert records[1].parent_beacon_block_root == payloads_mod.ZERO_BEACON_ROOT


# ---------- drivers: ReplayDriver ----------

class _FakeEngine:
    """Records new_payload / forkchoice_updated calls; returns canned statuses."""

    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.calls = []
        self.new_payload_kwargs = []

    def new_payload(self, payload, versioned_hashes=None, parent_beacon_block_root=None,
                    version=None, execution_requests=None):
        self.calls.append(("new_payload", payload.get("blockHash")))
        self.new_payload_kwargs.append({
            "version": version,
            "execution_requests": execution_requests,
            "parent_beacon_block_root": parent_beacon_block_root,
        })
        return {"status": self._statuses.pop(0)}

    def forkchoice_updated(self, head, **kwargs):
        self.calls.append(("forkchoice_updated", head))
        return {"payloadStatus": {"status": "VALID"}}


def _record(block_hash, number, newpayload_version=None, execution_requests=None):
    return payloads_mod.PayloadRecord(
        payload={"blockHash": block_hash, "blockNumber": hex(number)},
        block_hash=block_hash,
        block_number=number,
        versioned_hashes=None,
        parent_beacon_block_root=None,
        fork=None,
        execution_requests=execution_requests,
        newpayload_version=newpayload_version,
    )


def test_replay_driver_three_valid_in_order(tmp_path):
    engine = _FakeEngine(["VALID", "VALID", "VALID"])
    records = [_record("0x1", 1), _record("0x2", 2), _record("0x3", 3)]
    csv_path = tmp_path / "lat.csv"

    summary = ReplayDriver(engine, records, latency_csv=str(csv_path)).run()

    assert summary["blocks"] == 3
    # 3 newPayload + 3 FCU, interleaved in order.
    assert engine.calls == [
        ("new_payload", "0x1"), ("forkchoice_updated", "0x1"),
        ("new_payload", "0x2"), ("forkchoice_updated", "0x2"),
        ("new_payload", "0x3"), ("forkchoice_updated", "0x3"),
    ]
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    assert rows[0]["block_number"] == "1"


def test_replay_driver_invalid_raises_and_stops():
    engine = _FakeEngine(["VALID", "INVALID", "VALID"])
    records = [_record("0x1", 1), _record("0x2", 2), _record("0x3", 3)]
    with pytest.raises(RuntimeError, match="INVALID"):
        ReplayDriver(engine, records).run()
    # Stopped at the INVALID block: 1 valid newPayload+FCU, then the INVALID newPayload.
    assert engine.calls == [
        ("new_payload", "0x1"), ("forkchoice_updated", "0x1"),
        ("new_payload", "0x2"),
    ]


def test_replay_driver_no_advance_head():
    engine = _FakeEngine(["VALID", "VALID"])
    records = [_record("0x1", 1), _record("0x2", 2)]
    ReplayDriver(engine, records, advance_head=False).run()
    assert all(call[0] == "new_payload" for call in engine.calls)


def test_replay_driver_forces_newpayload_version_over_per_record():
    engine = _FakeEngine(["VALID", "VALID"])
    # Record carries V3; the driver override forces V4 for all.
    records = [_record("0x1", 1, newpayload_version=3), _record("0x2", 2)]
    ReplayDriver(engine, records, advance_head=False, newpayload_version=4).run()
    assert [kw["version"] for kw in engine.new_payload_kwargs] == [4, 4]


def test_replay_driver_uses_per_record_version_without_override():
    engine = _FakeEngine(["VALID", "VALID"])
    records = [_record("0x1", 1, newpayload_version=4, execution_requests=[]),
               _record("0x2", 2)]
    ReplayDriver(engine, records, advance_head=False).run()
    assert engine.new_payload_kwargs[0]["version"] == 4
    assert engine.new_payload_kwargs[0]["execution_requests"] == []
    assert engine.new_payload_kwargs[1]["version"] is None


# ---------- drivers: PivotDriver ----------

class _FakePivotEngine:
    def __init__(self):
        self.calls = []

    def forkchoice_updated(self, head, safe=None, finalized=None, payload_attributes=None, version=3):
        self.calls.append({"head": head, "version": version})
        return {"payloadStatus": {"status": "SYNCING"}}


def test_pivot_driver_tick_sends_pivot_as_head_safe_finalized(monkeypatch):
    engine = _FakePivotEngine()
    stop = threading.Event()
    stop.set()  # stop immediately after the first tick
    monkeypatch.setattr(drivers.time, "sleep", lambda *_: None)

    driver = PivotDriver(engine, pivot_hash="0xpivot", interval=0)
    result = driver.run(stop_event=stop)

    # stop_event pre-set: loop checks before the first iteration and exits with 0 ticks.
    # Drive one tick explicitly to assert the FCU shape.
    driver.tick()
    assert engine.calls[-1]["head"] == "0xpivot"
    assert result["ticks"] == 0


def test_pivot_driver_runs_one_tick_then_stops(monkeypatch):
    """With stop_event set after the first tick, exactly one FCU is sent."""
    engine = _FakePivotEngine()
    monkeypatch.setattr(drivers.time, "sleep", lambda *_: None)
    stop = threading.Event()

    real_tick_count = {"n": 0}
    driver = PivotDriver(engine, pivot_hash="0xpivot", interval=0)
    orig_tick = driver.tick

    def _counting_tick():
        real_tick_count["n"] += 1
        out = orig_tick()
        stop.set()  # ask to stop after this tick
        return out

    driver.tick = _counting_tick
    result = driver.run(stop_event=stop)

    assert real_tick_count["n"] == 1
    assert result["ticks"] == 1
    assert engine.calls[0]["head"] == "0xpivot"


# ---------- runner: build_mock_cl_command ----------

def test_build_mock_cl_command_pivot():
    cfg = {
        "engine_url": "http://localhost:8551",
        "jwt": "/etc/jwt.hex",
        "pivot": {"hash": "0xpivot", "number": 24546596, "interval": 12},
    }
    cmd = runner.build_mock_cl_command("pivot", cfg)

    assert cmd[:5] == ["uv", "run", "python", "-m", "mock_cl"]
    assert "pivot" in cmd
    assert "--engine-url" in cmd and "http://localhost:8551" in cmd
    assert "--jwt" in cmd and "/etc/jwt.hex" in cmd
    assert "--pivot-hash" in cmd and "0xpivot" in cmd
    assert "--pivot-number" in cmd and "24546596" in cmd
    assert "--interval" in cmd and "12" in cmd
