# Ethereum Node Benchmarking Framework

## Architecture Overview
The framework consists of a **Control Plane** (Python Orchestrator), a **Load Engine** (Locust), and a **Visualization Layer** (Grafana over Prometheus).

### 1. Components
* **Orchestrator (`src/orchestrator/`)**: Python application that manages docker-compose lifecycle for one Ethereum client at a time, plus the consensus side selected by `nodes.consensus_mode`: `"lighthouse"` starts the matching `lighthouse-<client>` beacon node (post-merge, nothing syncs mainnet without a CL driving the engine API), `"mock"` starts the EL alone and launches the mock-CL **pivot** driver (`src/mock_cl`) in the background so a frozen snapshot target snap-syncs, and `"none"` starts the EL only (pre-synced or externally driven; the legacy `nodes.consensus` boolean still maps to lighthouse/none). Measures sync wall time with a guarded check: sync counts as complete only after several consecutive polls where `eth_syncing` is false *and* the chain head is fresh (non-zero, recent timestamp) — a bare `eth_syncing` poll reports `false` before sync even starts. If the node never reported syncing, `sync_time_sec` is recorded as `null` rather than a bogus near-zero value. Captures final DB size via `du -sb` (always, even with `--skip-sync`), snapshots sync-phase Prometheus metrics immediately after sync (`aggregator.py --phase sync`), then invokes the load test and the final aggregation. A failing client is logged and skipped; the rest of the client matrix still runs (non-zero exit at the end). Detects whether the Docker installation that will run compose (the SSH remote when `ssh_command` is set) provides `docker compose` (v2 plugin) or `docker-compose` (v1) and picks the right command; compose invocations pass `--env-file .env` when a repo-root `.env` exists (compose's default `.env` lookup is next to the compose file, not the cwd). By default leaves the stack running after the benchmark so Grafana/Prometheus remain reachable for inspection; pass `--stop-monitoring` to tear it down.
* **Load Engine (`src/load_tests/`)**: Locust `FastHttpUser` workload over `eth_getBalance`, `eth_getStorageAt`, `eth_call` (multiple ERC-20 calldata shapes), `eth_getCode`, `eth_getProof`. At test start it fetches the current head block and constructs a configurable window of recent block tags; tasks sample uniformly from that window instead of always hitting `"latest"`. `eth_getProof` response sizes are written to a per-run CSV. Also exposes a Prometheus `/metrics` endpoint (in-process, port 9646 by default) with request counters, latency histograms, an active-user gauge, and a test-running indicator.
* **Metrics Aggregator (`src/metrics/`)**: Queries Prometheus for cross-client container/host metrics (CPU peak+sustained, RSS peak+sustained, RSS growth rate, network RX, disk IOPS, disk throughput) and per-client client-exporter metrics (block-processing duration histogram percentiles, gas/s). Parses Locust stats and proof-size CSV, merges in the runner-captured sync time + DB size, and emits per-client JSON + flat CSV under `benchmarks/<milestone>/`.
* **Monitoring Stack (`docker/`)**: Prometheus + node_exporter + cAdvisor + Grafana. Grafana is provisioned from disk (`docker/grafana/`): a Prometheus datasource is registered at first boot, and the `Benchmark` dashboard (Resources / Client / Host / Locust rows) auto-loads as the anonymous-viewer home page at `http://localhost:3000`.
* **Overlay snapshot isolation (`src/orchestrator/overlay.py`)**: Optional Linux `overlayfs` wrapper around a pristine, snapshot-backed client DB. A benchmark that advances the head via mock-CL replay writes new blocks straight into the bind-mounted snapshot, mutating it permanently — so the next client can no longer start from the *exact same* block. When `overlay.enabled` is set in the YAML, an overlay is mounted with the snapshot as a read-only `lowerdir` and a scratch `upperdir`/`workdir`; the **merged** dir is bound into the container, so all writes land in scratch and the snapshot underneath stays untouched. **Restore happens before each run** (`up` unmounts any stale overlay and wipes scratch, then mounts fresh — idempotent, and self-healing after a crashed run) and again on teardown. The same module/config serves both lifecycles: `runner.py` drives it (SSH-aware) when `infrastructure.manage` is true, and the template `start_infra.sh` scripts drive it for the `manage: false` bloatnet/mainnet snapshot flow, repointing the client's DB bind var (`NETHERMIND_DB_PATH` / `GETH_DB_PATH`) at the merged dir. Requires Linux, mount privileges (`sudo`), and a `scratch_dir` on an xattr-capable filesystem (ext4/xfs) with room for the run's writes — keep it on the big disk beside the snapshot. Disabled by default; the raw snapshot is bind-mounted exactly as before.

### 2. Workflow
1. **Sync Phase**:
   - Orchestrator starts the client via docker-compose, plus the consensus driver per `nodes.consensus_mode` (`lighthouse` beacon node, background mock-CL pivot, or none).
   - Waits for sync: requires `eth_syncing == false` **and** a fresh head block across several consecutive polls; captures wall time (`null` if the node was already synced).
   - Immediately snapshots sync-phase system metrics from Prometheus over `sync_window` (`aggregator.py --phase sync` → `sync_phase_metrics_<client>.json`; merged into the final JSON with a `sync_` prefix).
   - `du -sb` inside the client container measures on-disk DB size.
   - Writes `benchmarks/<milestone>/sync_metrics_<client>.json` so `--skip-sync` runs can reuse the sync time and the aggregator can merge it into the final JSON.
2. **Execution Phase (Live Head)**:
   - Aggregator queries per-client block-processing p50/p95/p99 over the `load_window` — Prometheus histograms (`*_block_processing_*_bucket`) for most clients, go-metrics summary quantiles (`chain_inserts{quantile="…"}`) for Geth — plus a rate query for gas/s.
   - Container-level CPU/RSS/IO metrics also use windowed `max_over_time` / `avg_over_time` rather than instant samples, distinguishing peak from sustained.
3. **RPC Load Testing Phase**:
   - Orchestrator runs Locust against the synchronized node for `load_test.run_time` (default 5 min).
   - Locust output CSVs land in `benchmarks/<milestone>/<client>/`.
   - Random recent-block sampling prevents pure tip-block cache hits.
   - `eth_getProof` response bytes are logged per call to `proof_sizes.csv`; the aggregator computes p50/p95/p99.
4. **Reporting Phase**:
   - Per-client JSON (`metrics_<client>.json`) with the full schema and a flat CSV (`metrics_<client>.csv`) under `benchmarks/<milestone>/`. Unavailable metrics are omitted (never recorded as `0.0`).
   - Cross-milestone regression check: walks sibling milestone directories, picks the most recent prior `metrics_<client>.json` by `timestamp` field, and compares each metric with a per-metric direction map (`metrics.higher_is_better`). Flags >25% degradations in the bad direction, persists them in the JSON's `regressions` array, and exits non-zero under `--fail-on-regression` (CI gate).

## Locust Details
- `FastHttpUser` for high-throughput HTTP.
- `@events.test_start` listener fetches `eth_blockNumber`, probes how deep the node's *servable* state actually goes (bisecting with `eth_getBalance`), and pins a recent-block window clamped to that band (`load_test.recent_block_window`). A background thread re-pins the window every `block_window_refresh_sec` so pinned blocks never age out of the served band while a mock-CL replay advances the head, falling back to the `latest` tag if the band shrinks below `min_pinned_window`.
- Address / slot / `eth_call` calldata pools are config-driven so realistic read patterns can be tuned without code changes.
- Custom CSV sink for `eth_getProof` response sizes, controlled by `LOCUST_PROOF_SIZES_CSV` env var (set by the runner).
- **Prometheus exporter** (`prometheus_client`) starts in `@events.init` and serves `/metrics` on `LOCUST_PROMETHEUS_PORT` (default 9646). `@events.request` hooks update a request counter, latency histogram, and response-bytes counter per call; `@events.test_start/stop` toggle a `locust_test_running` gauge and start a 1s `locust_users` poller. Prometheus is configured with two scrape targets — `host.docker.internal:9646` (Docker Desktop) and `host.orb.internal:9646` (OrbStack rootless) — exactly one is reachable per environment.
- **Single-process only**: distributed `--master`/`--worker` mode is rejected at init — the Prometheus counters and proof-size CSV are process-local, so workers would record into state that is never exported.
- Extra target addresses (e.g. the seeded test contract) can be injected at runtime via the `LOCUST_EXTRA_ADDRESSES` env var (comma-separated); the runner sets it from `benchmarks/.seed-state.json` when present.

## Metrics Source
- **cAdvisor**: container CPU, RSS, network, filesystem. Compose ships `cgroup: host` so cAdvisor sees sibling docker scope cgroups (needed on rootless Docker, harmless on Docker Desktop). On rootless Docker the docker factory is brittle (storage-driver layer-DB mismatch); set `DOCKER_SOCK=/dev/null` to fall back to the systemd factory and get `id`-labeled series.
- **node_exporter**: host-level filesystem / process metrics, including the disk I/O panels shown in Grafana.
- **Client `/metrics` endpoints**: block-processing histograms, gas counters. Metric names vary per client and are kept in `metrics.client_queries` keyed by client name.
- **Locust exporter**: in-process Prometheus endpoint on port 9646; powers the `Locust (load test)` dashboard row.

## Out of Scope (tracked separately)
- Cross-client delta panels in the existing Grafana dashboard (side-by-side Nethermind / Geth / Besu / Reth / Erigon at the same milestone).
- Statistical repeatability harness (`--repeat N`, ±5% bounds).
- Distributed (multi-machine) Locust load generation.
