# Ethereum Node Benchmarking Framework

A configuration-driven harness for benchmarking Ethereum execution clients (Nethermind, Geth, Besu, Reth, Erigon). It orchestrates node sync, captures sync-phase and execution-phase system metrics from Prometheus, runs a high-concurrency JSON-RPC load test with Locust, and emits per-milestone CSV/JSON reports with cross-milestone regression detection.

## Architecture Overview

*   **Orchestrator (`src/orchestrator/runner.py`)**: Manages docker-compose lifecycle (auto-detects v1 vs v2 plugin), polls `eth_syncing` to capture sync wall time, measures final on-disk DB size, then triggers the load test and aggregator.
*   **Load Generator (`src/load_tests/locustfile.py`)**: `FastHttpUser` workload covering `eth_getBalance`, `eth_getStorageAt`, `eth_call` (multiple shapes), `eth_getCode`, `eth_getProof`. Samples random recent blocks (configurable window) to avoid hitting the same tip block on every call. Captures `eth_getProof` response sizes to a per-run CSV.
*   **Metrics Aggregator (`src/metrics/aggregator.py`)**: Queries Prometheus for peak / sustained CPU, RSS, RSS growth, network RX, disk IOPS, disk throughput, plus per-client block-processing p50/p95/p99 and gas/s. Parses Locust stats and proof-size CSV. Emits JSON + flat CSV per client. Compares against the most recent prior milestone for the same client, with a per-metric direction map (higher-is-better metrics flip the regression check).
*   **Monitoring Stack (`docker/`)**: Prometheus + cAdvisor + node_exporter.

See [`docs/architecture.md`](docs/architecture.md) for the phase-by-phase workflow (sync → execution → load → reporting).

## Prerequisites

*   [**uv**](https://github.com/astral-sh/uv) — fast Python package installer.
*   **Docker** + **Docker Compose** (v1 or v2 plugin).

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
| `docker/docker-compose.monitoring.yml` | Prometheus + cAdvisor + node_exporter (production stack).          |
| `docker/docker-compose.test.yml`       | Self-contained Geth-dev test stack (single shared network).        |
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

### Notes on metric sources

* **Geth** exposes metrics in go-metrics summary format with `{quantile="…"}` labels — *not* Prometheus histograms (`*_bucket`). Query the quantile directly: `chain_execution{quantile="0.95"} / 1e9` (the raw value is in nanoseconds). `histogram_quantile()` will return empty against Geth. See `test-config.yml` for a working example.
* **cAdvisor in nested container environments** (OrbStack, some LXC hosts) may only see the root cgroup and emit no `name="benchmark_…"` labels. On these hosts all `container_*{name=…}` queries return 0; on a real Docker host they return real values. Not a code bug — a portability gotcha worth knowing when investigating zero metrics.
* Missing metrics are not fatal — `query_prometheus` returns `0.0` and logs the failure so the rest of the run completes.

## Usage

Three configs ship in the repo: `config.yml` (local docker-managed real clients), `remote-config.yml` (point at an already-running remote node), and `test-config.yml` (the Geth `--dev` smoke stack — see [Quick Local Test](#quick-local-test)). Use `--config <path>` to select one; defaults to `config.yml`.

```bash
# One client, one milestone
uv run python src/orchestrator/runner.py --client nethermind --milestone v1.0.0

# Skip sync (reuses sync_metrics_<client>.json from a previous run if present)
uv run python src/orchestrator/runner.py --client geth --milestone v1.0.0 --skip-sync

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
  "proof_sizes": { "p50_bytes": ..., "sample_count": 1234 }
}
```

## Regression Detection

The aggregator walks sibling directories under `benchmarks/`, finds the most recent prior `metrics_<client>.json` for the same client (by `timestamp` field), and compares each metric. Higher-is-better metrics (configured via `metrics.higher_is_better`) flip the direction check. When a metric degrades by more than 25% in the bad direction, a `[WARNING] REGRESSION DETECTED!` is logged.

## Quick Local Test

A self-contained docker-compose stack runs Geth in `--dev` mode (1 block/sec) alongside the full monitoring trio on a shared network. Boots in seconds; lets you exercise every code path in the harness without doing a real mainnet sync.

```bash
# 1. Bring up the test stack (Geth --dev + Prometheus + cAdvisor + node_exporter).
docker compose -f docker/docker-compose.test.yml up -d

# 2. Seed state: fund test addresses, deploy a tiny contract, send extra txs.
uv run python scripts/seed_test_node.py

# 3. Run a 30-second benchmark against the test client.
uv run python src/orchestrator/runner.py --config test-config.yml --milestone smoke --skip-sync

# Inspect the results.
cat benchmarks/smoke/metrics_test-client.json
```

`--skip-sync` is used because Geth `--dev` reports `eth_syncing` as `false` from the start. The `db_size` measurement still runs and gets persisted.

**Note on re-seeding**: each `docker compose down -v` wipes the dev chain volume. The next seed deploys the test contract from nonce 0 of a fresh dev account, so the contract address changes. The seeder log prints the new address — paste it into `test-config.yml`'s `load_test.addresses` if you want `eth_call(totalSupply())` and `eth_getStorageAt` to hit the initialized contract instead of returning `0x` for an empty account.

Tear-down: `docker compose -f docker/docker-compose.test.yml down -v`.

## Testing

The suite uses **pytest** (declared in `pyproject.toml` under `[dependency-groups].dev`). The `integration` marker is registered in `[tool.pytest.ini_options]` and excluded by default via `addopts`, so a casual `uv run pytest` is fast and Docker-free.

```bash
uv run pytest                   # unit tests only (default; ~0.2 s, no Docker)
uv run pytest -m integration    # adds the docker-stack integration test (~80 s, needs Docker)
uv run pytest -m ""             # everything
```

Unit tests (`tests/unit/`) cover the aggregator's parser/comparator helpers (`render_query`, `parse_locust_stats`, `parse_proof_sizes`, `find_previous_milestone`, `compare_against_previous`, `write_csv_summary`, `query_prometheus` error handling) and the runner's small utilities (`detect_compose_cmd`, `measure_db_size`, `write_sync_metrics`). The integration test (`tests/integration/`) brings up `docker/docker-compose.test.yml`, runs the seeder, drives the orchestrator end-to-end via subprocess, and asserts the produced JSON has non-zero metrics across the spec's key categories (`block_proc_p95`, `proof_p50_bytes`, `rpc_p95_latency_ms`, `rpc_requests_sec`). A second integration test drives two consecutive milestones to verify the cross-milestone regression comparator picks up the prior run.

## Out of Scope (follow-up)

- Grafana cross-client delta dashboards — recommended next step now that the JSON schema is stable.
- Repeatability harness (`--repeat N`, ±5% statistical bound).
- JWT / engine-API wiring for end-to-end snap-sync-from-peer runs.
