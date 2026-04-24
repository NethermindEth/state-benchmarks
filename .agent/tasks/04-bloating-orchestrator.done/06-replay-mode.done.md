# Subtask 06: Replay mode (`--replay <path>`)

**Business Value**: Any reviewer can verify a bloating run's methodology by replaying the journal against a fresh Nethermind — if the recorded verbs + deadline_bytes produce the same final state root, the run is reproducible.

**Dependencies**: 02 (facade dispatch), 03 (journal reader)

---

## Implementation Plan

### Files to Create

| File | Purpose |
|------|---------|
| `src/orchestrator/replay.py` | `replay(journal_path, rpc_url, manifest_path=None)` function |
| `tests/test_replay.py` | Round-trip test with a live journal |

### Steps

1. **Entry**: `orchestrator --replay <journal_path>` in CLI routes to `replay.replay(...)`.
2. **No controller**: replay does NOT construct `Controller`, `SensorClient` (except to read final state root), or any facade context that depends on the controller. It reads the journal verbatim and re-invokes the facade.
3. **Implementation**:
   - Open `JournalReader(journal_path)`.
   - Verify chain-hash end-to-end first (`reader.verify_chain()`). Abort on mismatch.
   - For each record:
     - Extract `replay_core.verb`, `replay_core.deadline_bytes`, `replay_core.start_address`, `replay_core.end_address`, `replay_core.block_hash`.
     - Reconstruct `FacadeContext` with cursors set from `start_address`.
     - Call `facade.dispatch(verb, deadline_bytes, context)`.
     - Assert the returned txs produce a batch whose end address matches `end_address`.
     - Call `testing_commit_block_v1(txs)`; assert returned block hash matches `replay_core.block_hash`.
   - After all records: fetch `eth_getBlockByNumber(head)`; compare `state_root` to `manifest.final_state_root`.
4. **Exit codes**: 0 on full success; 1 on chain-hash mismatch; 2 on block-hash mismatch; 3 on state-root mismatch; 4 on facade dispatch error.
5. **Observability NOT read** — `observability` fields (coeffs, alpha, residual) are ignored. Replay only depends on `replay_core` + `schema` + `batch_id` metadata.

### Patterns & Hints

```python
def replay(journal_path: Path, rpc_url: str, manifest_path: Path | None = None) -> int:
    reader = JournalReader(journal_path)
    reader.verify_chain()  # raises on mismatch → exit 1

    context = FacadeContext(...)  # reconstructed from first record
    rpc = RpcClient(rpc_url)

    for record in reader:
        rc = record.replay_core
        ctx_before = dataclasses.replace(context, address_cursor=int.from_bytes(rc.start_address, "big"))
        txs = facade.dispatch(rc.verb, rc.deadline_bytes, ctx_before)
        # sanity: cursor advanced to end_address?
        assert context.address_cursor.to_bytes(20, "big") == rc.end_address, \
            f"batch {record.batch_id}: cursor drift"
        committed_hash = rpc.testing_commit_block_v1(txs)
        assert committed_hash == rc.block_hash, \
            f"batch {record.batch_id}: block hash mismatch"

    if manifest_path:
        manifest = json.loads(manifest_path.read_text())
        final_root = rpc.eth_get_block_by_number("latest")["stateRoot"]
        assert final_root == manifest["final_state_root"]

    return 0
```

Replay has no `SensorClient` dependency — it trusts the journal and re-derives everything from it. That's the whole point: **the journal is self-contained evidence of the run**.

---

## Testing

**Test File**: `tests/test_replay.py`

| Test Name | Description | Expected |
|-----------|-------------|----------|
| `test_replay_chain_hash_mismatch_exits_1` | Tamper one byte in record 5; replay | Exit code 1 |
| `test_replay_block_hash_mismatch_exits_2` | Journal claims block hash X; RPC returns Y | Exit code 2 |
| `test_replay_state_root_mismatch_exits_3` | Full run journal; pass a manifest with wrong `final_state_root` | Exit code 3 |
| `test_replay_happy_path` | Run 10 fresh batches; replay against fresh Nethermind; check final root | Exit 0; root matches |
| `test_replay_ignores_observability` | Zero out every `observability` field in the journal before replay | Replay succeeds (exit 0) — `observability` not consulted |
| `test_replay_unknown_verb_exits_4` | Journal has `verb="garbage"` | Exit code 4 |
| `test_replay_facade_cursor_drift_detected` | Mutate record's `end_address` to a value the adapter would not produce | Assertion fires |

**Faking Strategy**: use a stub RPC that returns a deterministic block hash computed from the tx list (`keccak(rlp(txs))`) and a final state root from a tracked counter. Full round-trip with real Nethermind deferred to integration tests (runs in compose).

---

## Acceptance Criteria

- [ ] `orchestrator --replay <path>` exits 0 on a valid journal
- [ ] Chain-hash is verified end-to-end before any RPC call
- [ ] Each recorded block hash is asserted against RPC-committed block hash
- [ ] Final state root matches manifest's `final_state_root` (when manifest path given)
- [ ] Replay does not read `observability` — zeroing those fields changes nothing
- [ ] Exit codes: 0 success, 1 chain-hash, 2 block-hash, 3 state-root, 4 facade error
- [ ] Replay is independent of controller/sensor — stubbing the controller in tests doesn't break replay
