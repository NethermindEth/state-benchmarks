# Ethereum Node Benchmarking Framework

A configuration-driven harness for benchmarking Ethereum execution clients (Nethermind, Geth, Besu, Reth, Erigon). It orchestrates node sync, captures sync-phase and execution-phase system metrics from Prometheus, runs a high-concurrency JSON-RPC load test with Locust, and emits per-milestone CSV/JSON reports with cross-milestone regression detection. A pre-provisioned Grafana dashboard surfaces every metric live during the run.

## Architecture Overview

*   **Orchestrator (`src/orchestrator/runner.py`)**: Manages docker-compose lifecycle (auto-detects v1 vs v2 plugin), measures sync wall time (requires `eth_syncing` to be false *and* a fresh chain head over several consecutive polls — clients report "not syncing" before sync starts, so a bare `eth_syncing` poll lies), snapshots sync-phase Prometheus metrics right after sync, measures on-disk DB size, then triggers the load test and aggregator. A failing client doesn't abort the rest of the client list. Leaves the stack running after the benchmark by default; pass `--stop-monitoring` to tear it down.
*   **Load Generator (`src/load_tests/locustfile.py`)**: `FastHttpUser` workload covering `eth_getBalance`, `eth_getStorageAt`, `eth_call` (multiple shapes), `eth_getCode`, `eth_getProof`. Samples random recent blocks (configurable window) to avoid hitting the same tip block on every call. Captures `eth_getProof` response sizes to a per-run CSV. Exposes a Prometheus `/metrics` endpoint on port 9646 (requests, latency histograms, active users, failures) that Prometheus scrapes live while the load test runs.
*   **Metrics Aggregator (`src/metrics/aggregator.py`)**: Queries Prometheus for peak / sustained CPU, RSS, RSS growth, network RX, disk IOPS, disk throughput, plus per-client block-processing p50/p95/p99 and gas/s. Parses Locust stats and proof-size CSV. Emits JSON + flat CSV per client. Compares against the most recent prior milestone for the same client, with a per-metric direction map (higher-is-better metrics flip the regression check).
*   **Monitoring Stack (`docker/`)**: Prometheus + cAdvisor + node_exporter + Grafana. Grafana is anonymous-viewer enabled at `http://localhost:3000` and auto-loads the `Benchmark` dashboard (Resources / Client / Host / Locust rows) — no login or manual import.

See [`docs/architecture.md`](docs/architecture.md) for the phase-by-phase workflow (sync → execution → load → reporting).

## Prerequisites

*   [**uv**](https://github.com/astral-sh/uv) — fast Python package installer.
*   **Docker** + **Docker Compose** (v1 or v2 plugin).

### Consensus client (required for mainnet sync)

Post-merge, no execution client syncs mainnet on its own — a consensus client must drive
its engine API. `docker/docker-compose.clients.yml` ships a `lighthouse-<client>` beacon
node per EL client (checkpoint-synced, shared `docker/jwtsecret` for engine-API auth);
the orchestrator starts the matching one automatically when `nodes.consensus: true`
(the default in `config.yml`). Set it to `false` for pre-synced or externally-driven
nodes. `docker/jwtsecret` is a committed local-only secret — regenerate with
`openssl rand -hex 32 > docker/jwtsecret` if you expose any of these ports.

## Setup

```bash
uv sync
```

## Repository Layout

| Path                                   | What it does                                                       |
|----------------------------------------|--------------------------------------------------------------------|
| `src/orchestrator/runner.py`           | Top-level CLI; manages docker, sync, load test, aggregator.        |
| `src/load_tests/locustfile.py`         | Locust workload (random recent blocks, proof-size capture).        |
| `src/metrics/aggregator.py`            | Prometheus + Locust → per-milestone JSON/CSV + regression check.   |
| `config.yml` / `remote-config.yml`     | Production configs (real clients / remote node).                   |
| `test-config.yml`                      | Config for the local Geth `--dev` smoke stack.                     |
| `docker/docker-compose.clients.yml`    | Real-client compose (Nethermind/Geth/Besu/Reth/Erigon).            |
| `docker/docker-compose.monitoring.yml` | Prometheus + cAdvisor + node_exporter + Grafana (production stack).|
| `docker/docker-compose.test.yml`       | Self-contained Geth-dev test stack (single shared network).        |
| `docker/grafana/`                      | Auto-provisioned Prometheus datasource + `Benchmark` dashboard.    |
| `scripts/seed_test_node.py`            | Idempotent state seeder for the test stack.                        |
| `tests/`                               | `unit/` (no Docker) and `integration/` (uses the test stack).      |
| `docs/architecture.md`                 | Phase-by-phase architecture detail.                                |
| `benchmarks/<milestone>/`              | Output of every run; the comparator scans siblings under here.     |

## Configuration

Everything is driven by `config.yml`. Key sections:

```yaml
metrics:
  prometheus_url: "http://localhost:9090"
  sync_window: "1h"     # range used for peak/sustained during sync phase
  load_window: "5m"     # range used during load-test phase
  queries:              # container/host queries with {client} + {window} tokens
    cpu_percent_peak: 'max_over_time((rate(...))[{window}:1m])'
    rss_peak_bytes:   'max_over_time(container_memory_rss{name="benchmark_{client}"}[{window}])'
    # ...etc: rss_sustained, rss_growth, net_rx_bytes/s, disk_iops, disk_throughput
  client_queries:       # per-client block-proc + gas/s
    nethermind:
      block_proc_p50: 'histogram_quantile(0.5, sum(rate(nethermind_block_processing_microseconds_bucket[{window}])) by (le))'
      gas_per_sec:    'rate(nethermind_blocks_gas_used[{window}])'
    geth: { ... }
    # ...
  higher_is_better:     # regression check flips sign for these metrics
    - rpc_requests_sec
    - gas_per_sec
    - disk_throughput_peak_bytes_per_sec

load_test:
  run_time: "5m"
  users: 50
  spawn_rate: 10
  recent_block_window: 1000   # how many recent blocks to sample from
  addresses: [...]
  slots: [...]
  eth_call_shapes:
    - "0x18160ddd"  # totalSupply()
    - "0x313ce567"  # decimals()
    # ...
```

**Note:** Per-client `client_queries` metric names should be verified against each client's `/metrics` endpoint — they differ across clients and versions. Missing metrics return `0.0` (logged, not fatal) so the harness keeps running.

### Ports & images: `.env`

Host port mappings, image tags, and Grafana admin credentials live in `.env.example`. Copy it once and edit only the values you need to override:

```bash
cp .env.example .env
# IMPORTANT: --env-file is required. Compose resolves the default .env next to
# the compose file (docker/), so a repo-root .env would be silently ignored.
docker compose --env-file .env -f docker/docker-compose.monitoring.yml up -d
```

The orchestrator adds `--env-file .env` automatically whenever a repo-root `.env` exists, so `runner.py` always honors it. (Don't use `--project-directory .` for this — it would also re-root the compose files' relative volume mounts away from `docker/`.)

Three `.env` variables go beyond ports/images:

* `ETH_NETWORK` — network for the whole client stack (EL chain flags **and** Lighthouse), e.g. `sepolia`. Defaults to `mainnet` in the compose file; `.env.example` ships `sepolia` for cheap real-sync runs.
* `CHECKPOINT_SYNC_URL` — Lighthouse checkpoint-sync endpoint; must match `ETH_NETWORK` (e.g. `https://sepolia.beaconstate.info` for sepolia).
* `CONSENSUS` — read by `runner.py` (not compose): overrides `nodes.consensus` from the config, i.e. whether the matching `lighthouse-<client>` beacon node is started alongside the EL client. Set `CONSENSUS=false` for pre-synced or externally-driven nodes.

Every variable uses `${VAR:-default}` in the compose files, so an unset value keeps today's defaults. **All published ports bind to `127.0.0.1` by default** — set `BIND_ADDR=0.0.0.0` in `.env` to expose JSON-RPC / Prometheus / Grafana beyond the host (don't do this on shared or public machines: anonymous Grafana, default admin credentials, and an open execution-client RPC are not safe to expose). Container names (`benchmark_*`), volume names, chain selection, and Grafana auth/theme flags stay hardcoded — edit the compose files directly if you need to change them.

**Coupling caveat:** Changing `LOCUST_PROMETHEUS_PORT` requires editing `docker/prometheus.yml` and `docker/prometheus.test.yml` in lock-step — Prometheus YAML does not support env interpolation.

### Notes on metric sources

* **Geth** exposes metrics in go-metrics summary format with `{quantile="…"}` labels — *not* Prometheus histograms (`*_bucket`). Query the quantile directly: `chain_execution{quantile="0.95"} / 1e9` (the raw value is in nanoseconds). `histogram_quantile()` will return empty against Geth. See `test-config.yml` for a working example.
* **Locust exporter** runs in-process inside `locustfile.py` and binds `0.0.0.0:9646` on the host (override via `LOCUST_PROMETHEUS_PORT`). The target is naturally DOWN whenever no benchmark is running.
* **cAdvisor on rootless Docker** (OrbStack and similar) fails to register containers via its docker factory because the storage driver lacks the layer DB cAdvisor expects, which suppresses `name`-enriched container metrics. The compose ships `cgroup: host` on the `cadvisor` service so its systemd factory still sees docker scope cgroups by `id`; setting `DOCKER_SOCK=/dev/null` disables the broken docker factory. On Docker Desktop / rootful Linux, leave `DOCKER_SOCK` unset and `name=…` queries work as usual.

## Usage

Three configs ship in the repo: `config.yml` (local docker-managed real clients) and `test-config.yml` (the Geth `--dev` smoke stack — see [Quick Local Test](#quick-local-test)). Use `--config <path>` to select one; defaults to `config.yml`.

```bash
# One client, one milestone
uv run python src/orchestrator/runner.py --client nethermind --milestone v1.0.0

# Skip sync (reuses sync_time from a previous sync_metrics_<client>.json if present;
# DB size is always measured fresh)
uv run python src/orchestrator/runner.py --client geth --milestone v1.0.0 --skip-sync

# CI mode: exit non-zero when a metric regresses >25% vs the previous milestone
uv run python src/orchestrator/runner.py --client geth --milestone v1.1.0 --skip-sync --fail-on-regression

# Tear down the stack at the end of the run (default is to leave it up so
# Grafana / Prometheus stay reachable for post-mortem inspection)
uv run python src/orchestrator/runner.py --client geth --milestone v1.0.0 --skip-sync --stop-monitoring

# Remote node, no infrastructure management
uv run python src/orchestrator/runner.py --config remote-config.yml --client besu --milestone v1.1.0 --skip-sync

# All clients listed in config.yml
uv run python src/orchestrator/runner.py --milestone all-clients-baseline
```

## Output Structure

```
benchmarks/
└── v1.0.0/
    ├── sync_metrics_nethermind.json      # sync_time_sec + db_size_bytes
    ├── sync_phase_metrics_nethermind.json # Prometheus snapshot over sync_window (taken right after sync)
    ├── metrics_nethermind.json           # full structured output
    ├── metrics_nethermind.csv            # flat per-metric CSV
    └── nethermind/
        ├── locust_stats_stats.csv        # Locust per-method + Aggregated
        ├── locust_stats_stats_history.csv
        ├── locust_stats_failures.csv
        ├── locust_stats_exceptions.csv
        └── proof_sizes.csv               # response bytes per eth_getProof call
```

`metrics_<client>.json` schema:

```json
{
  "client": "nethermind",
  "milestone": "v1.0.0",
  "timestamp": "2026-06-05T12:34:56.000000+00:00",
  "state_size_bytes": 612000000000,
  "metrics": {
    "sync_time_sec": 28734.5,
    "sync_cpu_percent_peak": 412.7,
    "sync_rss_peak_bytes": 21000000000,
    "cpu_percent_peak": 380.2,
    "cpu_percent_sustained": 145.2,
    "rss_peak_bytes": 18450000000,
    "rss_sustained_bytes": 12058624000,
    "rss_growth_bytes_per_sec": 1543.2,
    "net_rx_bytes_per_sec_peak": 50000000,
    "disk_iops_peak": 12000,
    "disk_throughput_peak_bytes_per_sec": 480000000,
    "block_proc_p50": 0.012,
    "block_proc_p95": 0.045,
    "block_proc_p99": 0.110,
    "gas_per_sec": 145000000,
    "rpc_p50_latency_ms": 12.0,
    "rpc_p95_latency_ms": 45.0,
    "rpc_p99_latency_ms": 110.0,
    "rpc_requests_sec": 1250.5,
    "proof_p50_bytes": 4096,
    "proof_p95_bytes": 12800,
    "proof_p99_bytes": 24000
  },
  "rpc": { "per_method": { "eth_getBalance": { "p50_ms": ..., ... }, ... } },
  "proof_sizes": { "p50_bytes": ..., "sample_count": 1234 },
  "regressions": [
    { "metric": "rpc_p95_latency_ms", "previous_milestone": "v0.9.0",
      "previous": 45.0, "current": 61.0, "delta_pct": 35.56 }
  ]
}
```

Metrics that can't be measured (Prometheus down, query returns no data, `histogram_quantile` yields NaN) are **omitted**, never recorded as `0.0` — a fabricated zero would poison baselines and trip false regressions. `sync_time_sec` is `null` when the node never actually reported syncing (already synced when the measurement started).

## Regression Detection

The aggregator walks sibling directories under `benchmarks/`, finds the most recent prior `metrics_<client>.json` for the same client (by `timestamp` field), and compares each metric. Higher-is-better metrics (configured via `metrics.higher_is_better`) flip the direction check. When a metric degrades by more than 25% in the bad direction, a `[WARNING] REGRESSION DETECTED!` is logged and the regression is recorded in the output JSON's `regressions` array. Pass `--fail-on-regression` (runner or aggregator) to exit non-zero in that case — useful as a CI gate.

## Quick Local Test

A self-contained docker-compose stack runs Geth in `--dev` mode (1 block/sec) alongside Prometheus + cAdvisor + node_exporter + Grafana on a shared network. Boots in seconds; lets you exercise every code path in the harness without doing a real mainnet sync, and gives you a live Grafana dashboard.

```bash
# 0. If a real-client benchmark ran before, tear its stack down first — the runner
#    leaves it up by default and the client still holds port 8545:
#      docker compose -f docker/docker-compose.clients.yml down
#      docker compose -f docker/docker-compose.monitoring.yml down
#    (Leftovers from versions before the compose projects were renamed don't
#    respond to compose down; remove them once with:
#      docker ps -aq --filter name=benchmark_ | xargs docker rm -f)

# 1. Bring up the test stack (Geth --dev + Prometheus + cAdvisor + node_exporter + Grafana).
docker compose -f docker/docker-compose.test.yml up -d

# 2. Seed state: fund test addresses, deploy a tiny contract, send extra txs.
uv run python scripts/seed_test_node.py

# 3. Run a 30-second benchmark against the test client. Leaves the stack up afterward.
uv run python src/orchestrator/runner.py --config test-config.yml --milestone smoke --skip-sync

# 4. Open the dashboard. Anonymous viewer is enabled — no login required.
#    http://localhost:3000  →  Benchmark dashboard (auto-loaded as home).

# Inspect the JSON results when ready.
cat benchmarks/smoke/metrics_test-client.json
```

`--skip-sync` is used because Geth `--dev` was never out of sync — without it the runner would (correctly) record `sync_time_sec: null` after its confirmation window. The `db_size` measurement always runs and gets persisted regardless of `--skip-sync`.

The Grafana dashboard updates live during the run with four rows:
* **Resources (cAdvisor)** — per-container CPU %, RSS, network RX, disk IOPS/throughput, filtered to the `$client` selector (defaults to `test-client`).
* **Client (e.g. geth go-metrics)** — block-processing p50/p95/p99 from `chain_execution`, gas/s from `chain_mgasps`.
* **Host (node_exporter)** — host CPU %, memory %, disk bytes/sec, disk IOPS.
* **Locust (load test)** — request rate by RPC method, aggregate p50/p95/p99 latency, per-method p95, active users, test-running indicator, failures/sec.

**Rootless Docker / OrbStack note**: cAdvisor's docker factory cannot resolve OrbStack's custom storage driver layer DB, which drops `name`-enriched container series. Set `DOCKER_SOCK=/dev/null` before `docker compose up -d` so cAdvisor falls back to the systemd factory and the `id`-based series still flow. (On Docker Desktop / rootful Linux: leave `DOCKER_SOCK` unset.)

**Note on re-seeding**: each `docker compose down -v` wipes the dev chain volume, so the test contract lands at a different address on the next seed. No manual action needed: the seeder records the deployed address in `benchmarks/.seed-state.json`, and the runner injects it into the load test automatically (via the `LOCUST_EXTRA_ADDRESSES` env var), so `eth_call` / `eth_getStorageAt` / `eth_getProof` always hit the initialized contract.

Tear-down: `docker compose -f docker/docker-compose.test.yml down -v` (or rerun the orchestrator with `--stop-monitoring`).

## Testing

The suite uses **pytest** (declared in `pyproject.toml` under `[dependency-groups].dev`). The `integration` marker is registered in `[tool.pytest.ini_options]` and excluded by default via `addopts`, so a casual `uv run pytest` is fast and Docker-free.

```bash
uv run pytest                   # unit tests only (default; ~0.2 s, no Docker)
uv run pytest -m integration    # adds the docker-stack integration test (~80 s, needs Docker)
uv run pytest -m ""             # everything
```

Unit tests (`tests/unit/`) cover the aggregator's parser/comparator helpers (`render_query`, `parse_locust_stats`, `parse_proof_sizes`, `find_previous_milestone`, `compare_against_previous`, `write_csv_summary`, `query_prometheus` error handling) and the runner's small utilities (`detect_compose_cmd`, `measure_db_size`, `write_sync_metrics`). The integration test (`tests/integration/`) brings up `docker/docker-compose.test.yml`, runs the seeder, drives the orchestrator end-to-end via subprocess, and asserts the produced JSON has non-zero metrics across the spec's key categories (`block_proc_p95`, `proof_p50_bytes`, `rpc_p95_latency_ms`, `rpc_requests_sec`). A second integration test drives two consecutive milestones to verify the cross-milestone regression comparator picks up the prior run.

## Out of Scope (follow-up)

- Cross-client delta dashboards on top of the existing Grafana provisioning (e.g. side-by-side panels for `chain_execution` across Nethermind / Geth / Besu / Reth / Erigon at the same milestone).
- Repeatability harness (`--repeat N`, ±5% statistical bound).
- Distributed (master/worker) Locust: the in-process Prometheus exporter and proof-size CSV are process-local, so the locustfile rejects `--master`/`--worker` at init. Single-process `FastHttpUser` saturates a single-node RPC endpoint comfortably; revisit if a multi-machine load source is ever needed.
