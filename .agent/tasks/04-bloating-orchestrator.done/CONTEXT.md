# Task 04: Bloating Feedback-Loop Orchestrator

## Source References

| Source | Path | Purpose |
|--------|------|---------|
| Final design | `.omc/co-design/simplify-20260422/final-design-v3.md` | Normative spec for every subtask |
| Math explained | `.omc/co-design/simplify-20260422/math-explained.md` | Controller intuition + worked examples |
| Overview | `.omc/co-design/simplify-20260422/overview.md` | 3-min system primer |
| Slack agreement | (chat excerpt, 2026-04-23) | Orchestrator lives in `NethermindEth/eth-perf-research/orchestrator/`; Dagu wrappers are thin; Docker packaging required |
| EELS scenarios | `NethermindEth/execution-specs` branch `feat/spamoor-to-est` | 12 ported Spamoor scenarios the facade dispatches to |
| Merged plugin | `nethermind-arbitrum/src/Nethermind/src/Nethermind/Nethermind.StateComposition/` (on master) | Sensor source; `statecomp_get` consumed as-is |

## Objective

Python package `orchestrator` that drives a Nethermind node's state composition toward a mainnet-faithful target via closed-loop feedback. Reads the merged plugin's `statecomp_get`, commits blocks via `testing_commitBlockV1`, writes two artifacts per run (`orchestrator.journal.jsonl` + `payloads.rlp`), and supports `--replay` for grant-certification reproducibility.

Target repo: `NethermindEth/eth-perf-research/orchestrator/`. Package ships as a Docker image that `docker-compose` wires alongside Nethermind; Dagu DAG (in `eth-perf-research/dagu/config/`) is a thin wrapper that calls the `orchestrator` CLI — out of scope here.

The plugin on master requires **zero changes** for v1 bloating. The orchestrator consumes the existing `StateCompositionReport` shape (`TrieStats.AccountTrieBytes`, `StorageTrieBytes`, `CodeBytesTotal`, `BlockNumber`); opt-in topology/contract-dictionary flags on the plugin are deferred until measured overhead demands them.

## Architecture

### Mental Model

One Python process, one feedback loop, two artifacts. Per batch: the controller picks a scenario mix from a 9-of-12 simplex, the facade dispatches the corresponding EELS scenario, `testing_commitBlockV1` commits the resulting block, the plugin applies its incremental diff, the orchestrator polls `statecomp_get` until `blockNumber` catches up, then updates its EWMA coefficient with Huber-saturated innovation and writes a journal record. Crash-resilient via fsync-per-batch and auto-detect resume.

The 12 scenarios live in the EELS port as pytest test modules; the facade is a thin registry that invokes each scenario's `build_*_transactions(deadline_bytes, context)` with a context carrying `base_address + revision`-derived cursors and the current genesis hash.

### Component Responsibilities

| Component | Role | Subtask |
|-----------|------|---------|
| `sensor.SensorClient` | Polls `statecomp_get`, extracts 3 byte counters, carries forward `r2 → r` | 01 |
| `facade/__init__.py` + 12 verb modules | Wraps each EELS scenario; returns signed txs for a deadline | 02 |
| `journal.JournalWriter` + `journal.PayloadStreamWriter` | JSONL + `payloads.rlp` with chain-hash and fsync | 03 |
| `controller.Controller` | Adaptive α + Huber + Michelot + REFERENCE_F + probe | 04 |
| `lifecycle.run()` + CLI | Auto-detect startup, main loop, manifest writer | 05 |
| `replay.replay()` + CLI entry | Read-only `--replay <path>` path | 06 |
| `Dockerfile` + compose snippet + `gen-jwt.sh` | Packaging + JWT discipline | 07 |

### Core Use Cases

1. **Fresh run**: operator runs `orchestrator` with no journal present; probe seeds F; controller loop drives state to target; writes journal + payloads.
2. **Resume**: orchestrator finds existing journal; verifies `composition_hash` + chain integrity + Nethermind head; reconstructs `F, σ, α` from tail; continues bloating.
3. **Replay**: reviewer runs `orchestrator --replay <journal>`; facade dispatches each recorded verb at the recorded `deadline_bytes`; final state root must match manifest.
4. **Cross-client reproduction** (out-of-band): other EL teams feed `payloads.rlp` to `geth import` / `besu blocks import` / `erigon import` to reach the bloated state.

### Data Flow

Orchestrator loop: cached `r` (from prior batch or bootstrap) → controller → facade → signed txs → `testing_commitBlockV1` → append `ExecutionPayloadV3` to `payloads.rlp` → `sensor_read(expected_block=H+1)` → controller updates F via `F += α · tanh((obs - F) / σ) · σ` → journal append + fsync → cache `r2` as next `r`.

## Subtasks

### Dependency Graph

```
[01, 02, 03] → 04 → 05 → 07
                    ↑
                    06 (parallel with 05; only needs 02, 03)
```

- **01, 02, 03** are foundational, no deps; can be developed in parallel.
- **04** needs the sensor (01), facade (02), and journal writer (03).
- **05** ties everything together; needs 04.
- **06** needs only 02 and 03 (replay doesn't construct the controller).
- **07** Docker packaging needs the CLI wired (so after 05).

### Index

| # | Subtask | Status | Business Value | File |
|---|---------|--------|----------------|------|
| 01 | Sensor client (`statecomp_get` + poll-and-carry) | done | Orchestrator reads fresh state in ~5–10 ms with a 5-second deadline | [01-sensor-client.done.md](01-sensor-client.done.md) |
| 02 | Facade dispatch (12 verbs over EELS scenarios) | done | Controller can ask for a byte-budgeted batch of any scenario by name | [02-facade-scenarios.done.md](02-facade-scenarios.done.md) |
| 03 | Journal + payload-stream writers | done | Every batch produces two durable artifacts (replay journal + cross-client payloads) | [03-journal-payload-writers.done.md](03-journal-payload-writers.done.md) |
| 04 | Controller (adaptive α + Michelot + probe) | done | Controller picks the next batch's scenario mix from observation + target | [04-controller.done.md](04-controller.done.md) |
| 05 | Run lifecycle (auto-detect + CLI + manifest) | done | One `orchestrator` command runs the full loop, handling fresh vs resume | [05-run-lifecycle.done.md](05-run-lifecycle.done.md) |
| 06 | Replay mode (`--replay <path>`) | done | Reviewer can reproduce final state root from any journal | [06-replay-mode.done.md](06-replay-mode.done.md) |
| 07 | Docker package + compose + JWT | done | `docker compose up` starts orchestrator + Nethermind with fresh JWT | [07-docker-package.done.md](07-docker-package.done.md) |

## Verification

1. **Fresh-run end-to-end**: `docker compose up` → orchestrator probes, runs 100 batches → journal has 100 records, payloads.rlp has 100 blocks, manifest hashes match.
2. **Resume**: kill orchestrator mid-run; restart; session_id bumps, `resumed_from_batch` matches last pre-crash batch, continues.
3. **Replay**: `orchestrator --replay state/orchestrator.journal.jsonl` → final `state_root` matches manifest's `final_state_root`.
4. **Cross-client**: `geth import state/payloads.rlp` against the same genesis → Geth's state root matches Nethermind's final state root.
5. **Composition convergence**: on a 1 TB target, after ~10 k batches, measured `(accounts, storage, code)` fractions are within ±1.5/±2.0/±1.5 pp of target.

## Constraints & Gotchas

- **Plugin on master is the sensor; no plugin changes required.** The orchestrator reads `StateCompositionReport.TrieStats.{AccountTrieBytes, StorageTrieBytes, CodeBytesTotal}` + `BlockNumber`. Other fields are ignored.
- **fsync per batch is mandatory** — required for crash-resume correctness. Narrow race window (Nethermind commits but fsync not done) handled by the resume path allowing `head == last + 1` and re-deriving the lost record.
- **Chain-hash over `replay_core` bytes only.** `observability` sub-object is non-deterministic and excluded from replay identity.
- **Sequential prestate** — addresses `base_address + revision · stride + i`; no HKDF.
- **r/r2 carry-forward**: one sensor read per batch (post-commit); cache invalidated only on `sensor_wait_timeout`.
- **Package layout**: code lives under `src/orchestrator/`; `pyproject.toml` at the orchestrator root. Do not nest under `dagu/config/scripts/` — Dagu calls the installed CLI.
- **JWT lifecycle**: ephemeral per compose-up, tmpfs volume, shredded on compose-down. Never persisted.
- **EELS port import**: scenarios are pytest test modules in `NethermindEth/execution-specs feat/spamoor-to-est`. Install as an editable path dep during development; pin to a tag for releases.
