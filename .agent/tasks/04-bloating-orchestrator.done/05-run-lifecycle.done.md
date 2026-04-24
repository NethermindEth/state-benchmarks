# Subtask 05: Run lifecycle — auto-detect startup, main loop, manifest writer, CLI

**Business Value**: One `orchestrator` command runs the full feedback loop, automatically detecting whether to probe-and-start-fresh or resume from an existing journal, and writes a signed manifest at shutdown.

**Dependencies**: 01 (sensor), 02 (facade), 03 (journal + payload writers), 04 (controller)

---

## Implementation Plan

### Files to Create

| File | Purpose |
|------|---------|
| `src/orchestrator/lifecycle.py` | `run()` function, main feedback loop |
| `src/orchestrator/manifest.py` | `Manifest` dataclass + `compute_composition_hash()` |
| `src/orchestrator/cli.py` | `orchestrator` console entry point (Typer or argparse) |
| `src/orchestrator/__main__.py` | `python -m orchestrator` shim |
| `target.yaml.example` | Default config scaffold |
| `tests/test_lifecycle.py` | End-to-end smoke test with stubs |

### Steps

1. **`compute_composition_hash(target: TargetConfig, env: EnvInfo) → str`**: SHA-256 over `sorted(composition + genesis + bloat_config + plugin_git_sha + nethermind_commit_sha + dotnet_runtime_major + cpu_arch)`. Records all inputs.
2. **Auto-detect startup** (`lifecycle.resolve_startup_mode(state_dir: Path) → StartupMode`):
   - No journal at `state/orchestrator.journal.jsonl` → `FRESH`.
   - Journal exists → read tail; verify: Nethermind head ∈ `{L.block_number, L.block_number + 1}`; `composition_hash` matches current env; chain-hash re-verifies. All pass → `RESUME`. Any fail → raise `ResumeRefused(reason)`.
3. **`run(target: TargetConfig, state_dir: Path, rpc_url: str)`**:
   - Resolve mode.
   - `FRESH`: construct `SensorClient`, call `probe_sequence(...)` to seed controller, init `ControllerState`.
   - `RESUME`: read journal tail; reconstruct `F, sigma, alpha` from last record's `observability`.
   - Open `JournalWriter` + `PayloadStreamWriter`.
   - Main loop until convergence or external signal:
     - `plan = controller.pick_next_batch(cached_r, target)`
     - `txs = facade.dispatch(plan.verb, plan.deadline_bytes, context)`
     - `block_hash = testing_commit_block_v1(txs)` via RPC
     - `payload_writer.append(built_payload)` (fetch block via `eth_getBlockByHash(block_hash, full_tx=True)` and assemble `ExecutionPayloadV3`)
     - `r2 = sensor.read(expected_block=cached_r.block_number + 1)`
     - `new_state = controller.apply_observation(r2, plan)`
     - `record = build_record(plan, r2, controller_state_before, new_state)`
     - `journal.append(record)`
     - `cached_r = r2`
4. **Signal handling**: `SIGINT`/`SIGTERM` → finish current batch + journal write, then exit cleanly. Don't leave partial state.
5. **Manifest writer**: on shutdown, compute `journal_sha256` over all `replay_core` bytes concatenated; write `run-manifest.json` with `sessions[]`, `composition_hash`, `reference_f_version`, `final_state_root`, `journal_sha256`, `manifest_signature=null`.
6. **CLI**: `orchestrator` → `run(...)`; `orchestrator --replay <path>` → hand off to subtask 06. Use Typer (or argparse) for clean `--help`. Common flags: `--state-dir`, `--rpc-url`, `--target-yaml`.

### Patterns & Hints

Signal-safe main loop:

```python
import signal

class StopRequested(Exception):
    pass

def run(target, state_dir, rpc_url):
    stop = False
    def _handle(*_): nonlocal stop; stop = True
    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    # ... setup ...
    while not stop and not converged(cached_r, target):
        do_one_batch(...)
    write_manifest(...)
```

The `testing_commit_block_v1` call returns a block hash; fetching the full block for payload serialization is one additional RPC call — acceptable latency.

Resume verification order matters: check `composition_hash` first (fast), then chain-hash (medium), then RPC-check head (requires RPC).

---

## Testing

**Test File**: `tests/test_lifecycle.py`

| Test Name | Description | Expected |
|-----------|-------------|----------|
| `test_resolve_fresh_when_no_journal` | Empty state dir | `StartupMode.FRESH` |
| `test_resolve_resume_when_valid` | Valid journal + matching env | `StartupMode.RESUME` |
| `test_refuse_when_composition_hash_mismatch` | Journal exists; manifest says hash X; current env computes Y | `ResumeRefused` |
| `test_refuse_when_head_drifted` | Nethermind head is `last.block_number + 5` | `ResumeRefused` |
| `test_head_plus_one_repair_path` | Nethermind head is `last.block_number + 1` | Resume proceeds; next batch id continues |
| `test_run_10_batches_fresh` | Stubs for sensor + RPC + facade; run 10 batches | Journal has 10 records; session_id=1 |
| `test_run_resume_bumps_session_id` | Run 5 fresh; kill; resume; run 5 more | 10 total records; second-half `session_id=2`; `resumed_from_batch=4` |
| `test_sigint_finishes_current_batch` | Send SIGINT mid-batch | Last journal record is complete; clean exit |
| `test_manifest_has_expected_fields` | Run 1 batch; read manifest | All design §7 fields present |

**Faking Strategy**: stub `SensorClient`, `testing_commit_block_v1` call, `facade.dispatch`. Focus on control flow and journal state transitions, not actual bloating.

---

## Acceptance Criteria

- [ ] `orchestrator` with no flags performs auto-detect startup
- [ ] Fresh start runs probe, seeds F, proceeds to main loop
- [ ] Resume verifies head, composition_hash, and chain-hash before continuing
- [ ] Main loop: sensor-read → plan → facade-dispatch → commit → payload-append → sensor-read → journal-append → fsync
- [ ] SIGINT/SIGTERM finish the current batch cleanly and write the manifest
- [ ] Manifest records `sessions[]`, `composition_hash`, `reference_f_version`, `journal_sha256`, `final_state_root`
- [ ] Session ID bumps across resumes; `resumed_from_batch` points at the last pre-resume batch
- [ ] CLI works: `orchestrator --help`, `orchestrator --state-dir path --rpc-url http://...`
