# Subtask 03: Journal + payload-stream writers

**Business Value**: Every batch produces two durable artifacts — a compact JSONL journal for replay verification and an `ExecutionPayloadV3` stream for cross-client reproduction. Crash-resilient via fsync per append.

**Dependencies**: None

---

## Implementation Plan

### Files to Create

| File | Purpose |
|------|---------|
| `src/orchestrator/journal.py` | `JournalWriter`, `JournalReader`, `Record`, `ReplayCore`, `Observability` dataclasses |
| `src/orchestrator/payloads.py` | `PayloadStreamWriter` appending RLP-encoded `ExecutionPayloadV3` |
| `src/orchestrator/schemas/journal_v1.json` | JSON Schema for validation |
| `tests/test_journal.py`, `tests/test_payloads.py` | Unit tests |

### Steps

1. **Record dataclass** matching design §6:
   - `ReplayCore`: `verb`, `deadline_bytes`, `start_address`, `end_address`, `status`, `block_hash`, `block_number`, `chain_hash`.
   - `Observability`: `observed_flat_bytes`, `coeffs_before`, `coeffs_after`, `sigma_innov`, `alpha_current`, `innovation_ratio`, `residual_norm`, `statecomp_snapshot` (raw sensor JSON).
   - `Record`: `schema=1`, `session_id`, `resumed_from_batch`, `ts_iso`, `batch_id`, `replay_core`, `observability`.
2. **`JournalWriter(path: Path)`** — opens append-only; exposes `append(record: Record)`:
   - Serialize `replay_core` deterministically (sorted keys, no trailing whitespace) → bytes.
   - Compute `chain_hash = sha256(prev_chain_hash + replay_core_bytes).hexdigest()`; write into `record.replay_core.chain_hash`.
   - Serialize full record as JSON line; write; `flush()`; `os.fsync(fd)`.
   - Track `prev_chain_hash` in memory; on construction, initialize from last record if file exists.
3. **`JournalReader(path: Path)`** — iterator over `Record`s from disk; `tail()` returns the last record; `verify_chain()` re-hashes and asserts.
4. **`PayloadStreamWriter(path: Path)`** — append-only binary; `append(payload: ExecutionPayloadV3)`:
   - RLP-encode payload (standard layout; use `eth_account` or `rlp` lib).
   - Write 4-byte big-endian length prefix + payload bytes; `flush()`; `os.fsync(fd)`.
5. **JSON Schema** (`journal_v1.json`): describes the Record shape. Used by CI gate to validate round-trip serialization.
6. **Never omit `statecomp_snapshot`** — on `sensor_wait_timeout`, write it as JSON `null` (not absent) to preserve chain-hash determinism.

### Patterns & Hints

Deterministic serialization of `replay_core`:

```python
import json

def serialize_replay_core(rc: ReplayCore) -> bytes:
    return json.dumps(
        rc.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
```

For payload RLP: use `rlp` library or write a small helper matching EIP-4895 ExecutionPayloadV3 layout. Cross-check by running the same bytes through `geth import` in a test.

```python
# journal.py — chain-hash lineage
class JournalWriter:
    def __init__(self, path: Path):
        self.path = path
        self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        self.prev_chain_hash = self._read_last_chain_hash()

    def append(self, record: Record) -> None:
        rc_bytes = serialize_replay_core(record.replay_core)
        record.replay_core.chain_hash = hashlib.sha256(
            self.prev_chain_hash.encode() + rc_bytes
        ).hexdigest()
        line = json.dumps(record.to_dict()) + "\n"
        os.write(self.fd, line.encode())
        os.fsync(self.fd)
        self.prev_chain_hash = record.replay_core.chain_hash
```

---

## Testing

**Test File**: `tests/test_journal.py`, `tests/test_payloads.py`

| Test Name | Description | Expected |
|-----------|-------------|----------|
| `test_chain_hash_covers_replay_core_only` | Append 3 records; hash the 3rd's replay_core manually | Matches |
| `test_chain_hash_breaks_on_tamper` | Append 3 records; modify one byte; verify | `ChainHashMismatch` |
| `test_observability_excluded_from_chain_hash` | Append; mutate `observability`; recompute chain | Unchanged |
| `test_fsync_called_per_append` | Monkeypatch `os.fsync`; append 5 times | Called 5 times |
| `test_reader_tail_returns_last_record` | Append 10; `reader.tail()` | Record 10 |
| `test_resume_continues_chain` | Close writer; open new writer on same file; append | `prev_chain_hash` carried |
| `test_schema_round_trip` | Serialize → deserialize → validate against schema | Passes |
| `test_statecomp_snapshot_null_preserves_hash` | Append with `statecomp_snapshot=None` | Chain-hash still computable |
| `test_payload_stream_round_trip` | Write 5 `ExecutionPayloadV3` via `PayloadStreamWriter`; read back via iterator | 5 matching payloads |

**Faking Strategy**: tests use a temp directory (`tmp_path` pytest fixture). No RPC, no Nethermind. Fake `ExecutionPayloadV3` built from real tx RLP bytes (synthesized).

---

## Acceptance Criteria

- [ ] `JournalWriter.append` computes `chain_hash` over `replay_core` bytes only
- [ ] `fsync` is called on every append (journal and payload stream)
- [ ] `observability` sub-object is excluded from `chain_hash`
- [ ] `JournalReader.verify_chain` re-hashes and raises on mismatch
- [ ] `statecomp_snapshot` is serialized as `null` (not omitted) when missing — round-trip preserves `chain_hash`
- [ ] Payload stream uses 4-byte-length-prefixed binary records
- [ ] JSON Schema validates a round-tripped record
- [ ] Resuming a writer on an existing journal continues the chain correctly
