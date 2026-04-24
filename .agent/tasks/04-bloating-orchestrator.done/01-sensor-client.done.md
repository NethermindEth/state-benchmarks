# Subtask 01: Sensor client

**Business Value**: Orchestrator reads fresh state composition from the Nethermind plugin in ~5–10 ms, bounded by a 5-second deadline, and caches the post-commit read for reuse as next batch's pre-read.

**Dependencies**: None

---

## Implementation Plan

### Files to Create

| File | Purpose |
|------|---------|
| `src/orchestrator/sensor.py` | `SensorClient` class and `StateObservation` dataclass |
| `tests/test_sensor.py` | Unit tests with a fake JSON-RPC server |

### Steps

1. Define `StateObservation` dataclass: `block_number: int`, `account_bytes: int`, `storage_bytes: int`, `code_bytes: int`, `raw: dict` (for journaling).
2. Implement `SensorClient(rpc_url: str, jwt_path: str | None = None)`. HTTP JSON-RPC client using `httpx` (sync). Reads JWT from `jwt_path` if supplied (for the `Engine` port; for the `Eth` port it's optional).
3. Method `read(expected_block: int | None = None, timeout_s: float = 5.0) → StateObservation`:
   - Loop: call `statecomp_get`, extract `blockNumber` and `TrieStats.{AccountTrieBytes, StorageTrieBytes, CodeBytesTotal}`.
   - If `expected_block is None` or `blockNumber >= expected_block`: return.
   - Else `time.sleep(0.1)`; repeat until `time.monotonic() - start > timeout_s`; then raise `SensorWaitTimeout`.
4. Expose the full raw JSON response alongside the three extracted counters — journal records the full snapshot (`statecomp_snapshot`).

### Patterns & Hints

Nethermind JSON-RPC response shape (on master, verified in plugin audit):

```json
{
  "result": {
    "trieStats": { "accountTrieBytes": 18000000000, "storageTrieBytes": 72000000000, "codeBytesTotal": 12500000000, ... },
    "blockNumber": 19012346,
    ...
  }
}
```

Note the camelCase (JSON convention) vs PascalCase in the C# source. Ignore `trieDistribution`, `diffsSinceBaseline`, `lastScanMetadata` for the controller — they travel to the journal via `raw`.

```python
class SensorClient:
    def read(self, expected_block=None, timeout_s=5.0):
        deadline = time.monotonic() + timeout_s
        while True:
            resp = self._rpc("statecomp_get")
            bn = resp["blockNumber"]
            if expected_block is None or bn >= expected_block:
                ts = resp["trieStats"]
                return StateObservation(
                    block_number=bn,
                    account_bytes=ts["accountTrieBytes"],
                    storage_bytes=ts["storageTrieBytes"],
                    code_bytes=ts["codeBytesTotal"],
                    raw=resp,
                )
            if time.monotonic() > deadline:
                raise SensorWaitTimeout(expected_block, bn)
            time.sleep(0.1)
```

---

## Testing

**Test File**: `tests/test_sensor.py`

| Test Name | Description | Expected |
|-----------|-------------|----------|
| `test_returns_immediately_when_block_caught_up` | Fake RPC returns `blockNumber=100`; request with `expected_block=100` | Returns in one call, no sleep |
| `test_polls_until_block_catches_up` | Fake RPC returns 99 twice then 100; request with `expected_block=100` | Returns after 3 calls |
| `test_timeout_raises` | Fake RPC always returns 99; request `expected_block=100, timeout_s=0.3` | `SensorWaitTimeout` after ~300 ms |
| `test_extracts_three_counters` | Fake RPC returns known `trieStats` | `StateObservation` fields match |
| `test_raw_preserves_full_response` | As above | `observation.raw == fake_response` |

**Faking Strategy**: use `pytest-httpx` (or a tiny `respx` mock) to intercept `httpx` POST calls. No real Nethermind needed.

---

## Acceptance Criteria

- [ ] `SensorClient.read()` polls `statecomp_get` and returns a `StateObservation` once `blockNumber >= expected_block`
- [ ] Timeout raises `SensorWaitTimeout` with the expected vs last-seen block numbers
- [ ] `StateObservation` exposes three byte counters and the full raw response
- [ ] Polling interval is 100 ms; deadline is 5 s by default
- [ ] Tests cover catch-up, timeout, and counter extraction paths
- [ ] No JWT required for `statecomp_get` (it lives on the public RPC port)
