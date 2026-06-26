# Ethereum Node Benchmarking Framework

Benchmark Ethereum execution clients - Nethermind, Geth, Besu, Reth, Erigon - under a realistic, repeatable workload

The framework drives a node through a full cycle (mount snapshot, start node, wait untill RPC is avaible, start test, stop the node) and produces a comparable report at the end (json/csv reports and grafana).

Everything is configuration-driven and runs locally with Docker. A pre-provisioned Grafana dashboard shows every metric live while the benchmark runs.

## How it works

The benchmark is split into four cooperating pieces:

**Orchestrator** (`src/orchestrator/runner.py`) - the entry point and conductor. It manages the docker-compose lifecycle (auto-detecting Compose v1 vs the v2 plugin), waits for a *genuine* sync completion, snapshots sync-phase metrics, measures the on-disk DB size, then kicks off the load test and the report.

**Load generator** (`src/load_tests/locustfile.py`) — a `FastHttpUser` workload that exercises the read-heavy RPC surface: `eth_getBalance`, `eth_getStorageAt`, `eth_call` (several call shapes), `eth_getCode`, and `eth_getProof`. An optional gas-paying `eth_sendTransaction` task exists but is off by default. To stay realistic it samples random recent blocks instead of hammering the same tip on every call. It also exposes its own Prometheus metrics (requests, latency, active users, failures) on port 9646, scraped live during the run.

**Metrics aggregator** (`src/metrics/aggregator.py`) — queries Prometheus for peak and sustained CPU, RSS and RSS growth, network RX, disk IOPS and throughput, plus per-client block-processing p50/p95/p99 and gas/s. It folds in the Locust stats and proof-size data, writes a JSON and a flat CSV per client, and compares the result against the most recent prior run for the same client — flipping the regression check for metrics where higher is better.

**Monitoring stack** (`docker/`) — Prometheus, cAdvisor, node_exporter, and Grafana. Grafana runs with anonymous viewing at `http://localhost:3000` and auto-loads the **Benchmark** dashboard (Resources / Client / Host / Locust rows).

For the full phase-by-phase walkthrough (sync → execution → load → reporting), see [`docs/architecture.md`](docs/architecture.md).

## Prerequisites

* [**uv**](https://github.com/astral-sh/uv) — fast Python package manager.
* **Docker** with **Docker Compose**.

### Consensus client (requires for sync test)

Post-merge, no execution client can sync mainnet on its own — a consensus client must drive its Engine API. `docker/docker-compose.clients.yml` ships a checkpoint-synced `lighthouse-<client>` beacon node for each EL client, sharing `docker/jwtsecret` for Engine-API auth. When `nodes.consensus: true` (the default), the orchestrator starts the matching beacon node automatically. Set it to `false` for nodes that are already synced or driven externally.

`docker/jwtsecret` is a committed local-only secret. If you ever expose these ports, regenerate it:

```bash
openssl rand -hex 32 > docker/jwtsecret
```

## Setup

```bash
uv sync
```

## Repository layout

| Path                                   | What it does                                                       |
|----------------------------------------|--------------------------------------------------------------------|
| `src/orchestrator/runner.py`           | Top-level CLI; manages docker, sync, load test, aggregator.        |
| `src/load_tests/locustfile.py`         | Locust workload (random recent blocks, proof-size capture).        |
| `src/metrics/aggregator.py`            | Prometheus + Locust → per-run JSON/CSV + regression check.         |
| `src/orchestrator/overlay.py`          | Optional overlayfs wrapper keeping a snapshot DB pristine per run.  |
| `config.yml` / `remote-config.yml`     | Production configs (local clients / remote node).                  |
| `test-config.yml`                      | Config for the local Geth `--dev` smoke stack.                     |
| `docker/docker-compose.clients.yml`    | Real-client compose (Nethermind/Geth/Besu/Reth/Erigon).           |
| `docker/docker-compose.monitoring.yml` | Prometheus + cAdvisor + node_exporter + Grafana (production stack).|
| `docker/docker-compose.test.yml`       | Self-contained Geth-dev test stack (single shared network).        |
| `docker/grafana/`                      | Auto-provisioned Prometheus datasource + `Benchmark` dashboard.    |
| `scripts/seed_test_node.py`            | Idempotent state seeder for the test stack.                        |
| `tests/`                               | `unit/` (no Docker) and `integration/` (uses the test stack).      |
| `docs/architecture.md`                 | Phase-by-phase architecture detail.                                |
| `benchmarks/<milestone>/`              | Output of every run; the comparator scans siblings under here.     |

## Configuration

Almost everything lives in `config.yml`. The key sections:

```yaml
metrics:
  prometheus_url: "http://localhost:9090"
  sync_window: "1h"     # range used for peak/sustained during sync phase
  load_window: "5m"     # range used during load-test phase
  queries:              # container/host queries with {client} + {window} tokens
    cpu_percent_peak: 'max_over_time((rate(...))[{window}:1m])'
    rss_peak_bytes:   'max_over_time(container_memory_rss{name="benchmark_{client}"}[{window}])'
    # ...plus rss_sustained, rss_growth, net_rx_bytes/s, disk_iops, disk_throughput
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
  gas_tests_enabled: false    # gas-paying tasks (eth_sendTransaction); see below
  recent_block_window: 1000   # how many recent blocks to sample from
  addresses: [...]
  slots: [...]
  eth_call_shapes:
    - "0x18160ddd"  # totalSupply()
    - "0x313ce567"  # decimals()
    # ...
```

**Gas-paying tasks (`load_test.gas_tests_enabled`)** — tasks tagged `gas` (currently `eth_sendTransaction`, a 1-wei transfer to a random configured address) **spend real gas** and need an unlocked account on the node. The locustfile grabs the first `eth_accounts` entry at startup and, if there is none, logs a warning and the tasks become no-ops. This is off by default.

> **Per-client metric names:** the `client_queries` differ across clients and versions, so verify them against each client's `/metrics` endpoint. Missing metrics return `0.0` (logged, not fatal), so the harness keeps running.

### Ports, images, and credentials: `.env`

Host port mappings, image tags, and Grafana admin credentials live in `.env.example`. Copy it once and override only what you need:

```bash
cp .env.example .env
# IMPORTANT: --env-file is required. Compose only auto-loads the .env next to the
# compose file (in docker/), so a repo-root .env would be silently ignored.
docker compose --env-file .env -f docker/docker-compose.monitoring.yml up -d
```

The orchestrator adds `--env-file .env` automatically whenever a repo-root `.env` exists, so `runner.py` always honors it. (Avoid `--project-directory .` here — it would also re-root the compose files' relative volume mounts away from `docker/`.)

Three `.env` variables do more than set ports/images:

* **`ETH_NETWORK`** — the network for the whole client stack (EL chain flags *and* Lighthouse), e.g. `sepolia`. Defaults to `mainnet` in the compose file.
* **`CHECKPOINT_SYNC_URL`** — Lighthouse checkpoint-sync endpoint; must match `ETH_NETWORK` (e.g. `https://sepolia.beaconstate.info` for sepolia).
* **`CONSENSUS`** — read by `runner.py` (not compose); overrides `nodes.consensus`, i.e. whether the matching beacon node starts alongside the EL client. Set `CONSENSUS=false` for pre-synced or externally-driven nodes.

Every variable uses `${VAR:-default}`, so leaving one unset keeps today's defaults. **All published ports bind to `127.0.0.1` by default** — set `BIND_ADDR=0.0.0.0` in `.env` to expose JSON-RPC / Prometheus / Grafana beyond the host. **Don't do this on shared or public machines:** anonymous Grafana, default admin credentials, and an open execution-client RPC are not safe to expose. Container names (`benchmark_*`), volume names, chain selection, and Grafana auth/theme flags stay hardcoded — edit the compose files directly to change them.

> **Coupling caveat:** changing `LOCUST_PROMETHEUS_PORT` means editing `docker/prometheus.yml` and `docker/prometheus.test.yml` in lock-step — Prometheus YAML doesn't support env interpolation.

### Notes on metric sources

* **Geth** exposes go-metrics summaries with `{quantile="…"}` labels — *not* Prometheus histograms (`*_bucket`). Query the quantile directly: `chain_execution{quantile="0.95"} / 1e9` (the raw value is nanoseconds). `histogram_quantile()` returns empty against Geth. See `test-config.yml` for a working example.
* **Locust exporter** runs in-process inside `locustfile.py` and binds `0.0.0.0:9646` on the host (override via `LOCUST_PROMETHEUS_PORT`). It is naturally DOWN whenever no benchmark is running.
* **cAdvisor on rootless Docker** (OrbStack and similar) can't register containers via its docker factory because the storage driver lacks the layer DB cAdvisor expects, which suppresses container metrics. The compose ships `cgroup: host` on the `cadvisor` service so its systemd factory still sees docker cgroups by `id`; setting `DOCKER_SOCK=/dev/null` disables the broken docker factory. On Docker Desktop / rootful Linux, leave `DOCKER_SOCK` unset and `name=…` queries work normally.

## Usage

```bash
# One client, one milestone
uv run python src/orchestrator/runner.py --client nethermind --milestone v1.0.0

# Skip sync (reuses sync_time from a previous sync_metrics_<client>.json if present;
# DB size is always measured fresh)
uv run python src/orchestrator/runner.py --client geth --milestone v1.0.0 --skip-sync

# CI mode: exit non-zero when a metric regresses >25% vs the previous milestone
uv run python src/orchestrator/runner.py --client geth --milestone v1.1.0 --skip-sync --fail-on-regression

# Tear down the stack at the end (default leaves it up so Grafana / Prometheus
# stay reachable for post-mortem inspection)
uv run python src/orchestrator/runner.py --client geth --milestone v1.0.0 --skip-sync --stop-monitoring

# Remote node, no infrastructure management
uv run python src/orchestrator/runner.py --config remote-config.yml --client besu --milestone v1.1.0 --skip-sync

# Every client listed in config.yml
uv run python src/orchestrator/runner.py --milestone all-clients-baseline
```

## Output

Each run writes into `benchmarks/<milestone>/`:

```
benchmarks/
└── v1.0.0/
    ├── sync_metrics_nethermind.json       # sync_time_sec + db_size_bytes
    ├── sync_phase_metrics_nethermind.json # Prometheus snapshot over sync_window (taken right after sync)
    ├── metrics_nethermind.json            # full structured output
    ├── metrics_nethermind.csv             # flat per-metric CSV
    └── nethermind/
        ├── locust_stats_stats.csv         # Locust per-method + Aggregated
        ├── locust_stats_stats_history.csv
        ├── locust_stats_failures.csv
        ├── locust_stats_exceptions.csv
        └── proof_sizes.csv                # response bytes per eth_getProof call
```

`metrics_<client>.json` looks like:

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

Metrics that genuinely can't be measured (Prometheus down, no data, `histogram_quantile` yields NaN) are **omitted**, never recorded as `0.0` — a fabricated zero would poison baselines and trip false regressions. `sync_time_sec` is `null` when the node never actually reported syncing (it was already synced when measurement started).

## Regression detection

When a run finishes, the aggregator looks back: it walks the sibling directories under `benchmarks/`, finds the most recent prior `metrics_<client>.json` for the same client (by `timestamp`), and compares each metric. Metrics where higher is better (listed in `metrics.higher_is_better`) flip the direction check. If a metric degrades by more than 25% in the bad direction, it logs `[WARNING] REGRESSION DETECTED!` and records the details in the output JSON's `regressions` array. Add `--fail-on-regression` to exit non-zero in that case — handy as a CI gate.

## Quick local test

```bash
# 0. If a real-client benchmark ran before, tear its stack down first — the runner
#    leaves it up by default and the client still holds port 8545:
#      docker compose -f docker/docker-compose.clients.yml down
#      docker compose -f docker/docker-compose.monitoring.yml down
#    (Containers from before the compose projects were renamed won't respond to
#    compose down; remove them once with:
#      docker ps -aq --filter name=benchmark_ | xargs docker rm -f)

# 1. Bring up the test stack.
docker compose -f docker/docker-compose.test.yml up -d

# 2. Seed state: fund test addresses, deploy a tiny contract, send extra txs.
uv run python scripts/seed_test_node.py

# 3. Run a 30-second benchmark against the test client (leaves the stack up).
uv run python src/orchestrator/runner.py --config test-config.yml --milestone smoke --skip-sync

# 4. Open the dashboard — anonymous viewer, no login:
#    http://localhost:3000  →  Benchmark dashboard (auto-loaded as home).

# 5. Inspect the results.
cat benchmarks/smoke/metrics_test-client.json
```

`--skip-sync` is used because Geth `--dev` was never out of sync — without it the runner would (correctly) record `sync_time_sec: null` after its confirmation window. The DB-size measurement always runs and is persisted regardless of `--skip-sync`.

The Grafana dashboard updates live during the run, with 3 rows:

* **Resources (cAdvisor)** — per-container CPU %, RSS, network RX, disk IOPS/throughput, filtered to the `$client` selector (defaults to `test-client`).
* **Host (node_exporter)** — host CPU %, memory %, disk bytes/sec, disk IOPS.
* **Locust (load test)** — request rate by RPC method, aggregate p50/p95/p99 latency, per-method p95, active users, test-running indicator, failures/sec.

> **Rootless Docker / OrbStack:** cAdvisor's docker factory can't resolve OrbStack's custom storage driver layer DB, which drops `name`-enriched container series. Set `DOCKER_SOCK=/dev/null` before `docker compose up -d` so cAdvisor falls back to the systemd factory and the `id`-based series still flow. (On Docker Desktop / rootful Linux, leave it unset.)

Tear-down: `docker compose -f docker/docker-compose.test.yml down -v` (or rerun the orchestrator with `--stop-monitoring`).

## Mock consensus layer

Post-merge EL clients only snap-sync or execute blocks when a consensus client drives their Engine API. `src/mock_cl` is a minimal Engine-API "mock CL" that supplies exactly the two functions [issue #21](https://github.com/marcindsobczak/state-benchmarks/issues/21) needs, with no extra dependencies (stdlib + `requests`; the HS256 JWT is hand-rolled).

It has two modes:

* **pivot** (snap-sync benchmarks) — repeatedly sends `engine_forkchoiceUpdatedV` with `headBlockHash = safeBlockHash = finalizedBlockHash` set to a fixed frozen pivot hash, every ~12s. That single repeated FCU points a post-merge EL (Geth/Besu/Reth/Erigon) at the frozen block so it snap-syncs to that state. Unlocks the #21 snap-sync metrics: **sync wall time, network bandwidth, disk IOPS, RSS, on-disk DB size**.
* **replay** (execution-under-head benchmarks) — for each recorded block, sends `engine_newPayloadV{1..4}` then `engine_forkchoiceUpdatedV{1..3}` to advance the head, measuring per-block processing latency. Unlocks the #21 execution metrics: **block-processing p50/p95/p99, gas/s, RSS growth**.

### CLI

```bash
# Snap-sync a frozen pivot (Ctrl-C to stop; --status-rpc lets it detect "synced"):
PYTHONPATH=src uv run python -m mock_cl \
  --engine-url http://localhost:8551 --jwt /path/to/jwt.hex \
  pivot --pivot-hash 0x7845cf57…e31f0ca --pivot-number 24546596 \
  --interval 12 --status-rpc http://localhost:8545

# Replay recorded payloads to drive execution under a live head:
PYTHONPATH=src uv run python -m mock_cl \
  --engine-url http://localhost:8551 --jwt /path/to/jwt.hex \
  replay --payloads payloads/x5.jsonl --latency-csv benchmarks/x5/replay_latency.csv

# Best-effort record blocks from a live EL into a JSONL payload file:
PYTHONPATH=src uv run python -m mock_cl --jwt /path/to/jwt.hex \
  record --source-rpc http://localhost:8545 --start 24358001 --count 1000 --out payloads/x5.jsonl
```

> **Cancun+ recording limitation:** `eth_getBlockByNumber` can't return blob versioned-hashes or `parentBeaconBlockRoot`, which `engine_newPayloadV3/V4` require. `record` logs a warning and omits them; for Cancun+ replay you need a CL-recorded payload source. Pre-Cancun (V1/V2) replay works from `record` output directly.

### RLP payload stream → JSONL (`scripts/rlp_to_mocks.py`)

The replay driver reads **JSONL** (one `{"payload": {...}, ...}` per line). Payload generators that emit a binary stream of length-prefixed RLP `ExecutionPayload` records (4-byte big-endian length + RLP body each) are converted with the stdlib-only converter — no `rlp` dependency:

```bash
uv run python scripts/rlp_to_mocks.py payloads_x3.5.bin -o payloads_x3.5.jsonl \
  --newpayload-version 4 \
  --parent-beacon-block-root 0x0000000000000000000000000000000000000000000000000000000000000000 \
  --versioned-hashes "" --execution-requests ""
```

It maps the 17 ExecutionPayloadV3 fields to the engine-API schema (respecting QUANTITY vs DATA hex encoding) and stamps the engine extras an `ExecutionPayload` doesn't carry. For **Prague** bloat blocks these are constant — zero (not `null`) `parent_beacon_block_root`, empty `versioned_hashes`/`execution_requests` — and the version is forced to **V4** (auto-pick lands on V3 because there are no blob/execution-request signals). All are CLI-overridable. Defensively, `load_payloads` also stamps a missing/`null` `parent_beacon_block_root` with the zero hash so `engine_newPayloadV3/V4` won't reject it, and `replay --newpayload-version N` overrides the per-record version for the whole run.

### Config + orchestrator integration

Set the consensus driver in `config.yml` under `nodes:`, and configure the driver under the top-level `mock_cl:` section:

```yaml
nodes:
  # "lighthouse" (real CL, default when consensus: true), "mock", or "none".
  consensus_mode: "mock"

mock_cl:
  engine_url: "http://localhost:8551"
  jwt: ""                 # path to jwt.hex OR a raw 0x/hex secret
  mode: "pivot"           # "pivot" | "replay"
  pivot:
    hash: ""              # frozen pivot block hash
    number: null
    interval: 12
  replay:
    payloads: ""
    count: null
    latency_csv: ""
```

Back-compat: if `consensus_mode` is unset, the legacy boolean applies (`consensus: true` → lighthouse, `false` → none). `CONSENSUS_MODE` in the environment or repo-root `.env` overrides the config. With `consensus_mode: "mock"`, the orchestrator starts the EL only (no Lighthouse) and launches the pivot driver in the background so the frozen target snap-syncs; it's terminated on tear-down.

## Snapshot isolation (overlayfs)

When a benchmark drives the mock-CL **replay** driver, the client writes new blocks straight into the on-disk snapshot it bind-mounts. That permanently mutates the snapshot — so the next client can no longer start from the *exact same* block, and re-extracting a multi-hundred-GB snapshot is slow or impossible on a space-constrained disk.

The optional overlay feature (`src/orchestrator/overlay.py`) solves this by stacking a Linux `overlayfs` mount on top of the pristine snapshot, so every run gets a private, throwaway copy-on-write layer while the snapshot underneath is never touched:

| Layer        | Path                  | Role                                  |
|--------------|-----------------------|---------------------------------------|
| `lowerdir`   | the snapshot          | read-only — never modified            |
| `upperdir`   | `<scratch>/upper`     | all writes land here                   |
| `workdir`    | `<scratch>/work`      | overlay bookkeeping                    |
| `merged`     | `<scratch>/merged`    | what the container actually mounts     |

The container reads and writes through `merged`; restoring the snapshot is just unmounting and deleting the scratch dirs. Crucially, **the overlay is restored before each run** — `up` first unmounts any stale overlay and wipes scratch, *then* mounts fresh, so every run starts from the pristine snapshot regardless of how the previous one ended (self-healing after a crash). Teardown unmounts and wipes again, leaving the snapshot pristine for the next client and freeing the scratch disk.

Both run flows use the same module and config:

* **Orchestrator-managed** (`infrastructure.manage: true`): `runner.py` mounts the overlay before `compose up` (SSH-aware — it runs wherever Docker runs), repoints the client's DB bind variable at the merged dir, and tears it down on exit.
* **Template-managed** (`infrastructure.manage: false`): the template `start_infra.sh` scripts own the lifecycle, mounting on start and unmounting on `down`.

Configure it under a top-level `overlay:` block. It is **disabled by default**, in which case the raw snapshot is bind-mounted exactly as before.

```yaml
overlay:
  enabled: false
  lowerdir: "/mnt/bigdata/snapshot_mainnet_neth/mainnet"  # pristine snapshot (matches the DB bind path)
  scratch_dir: "/mnt/bigdata/overlay/nethermind"          # holds upper/ work/ merged/ (beside lowerdir if omitted)
  db_path_env: "NETHERMIND_DB_PATH"                        # compose bind var repointed at <scratch_dir>/merged
  name: "mainnet-overlay"
  sudo: true
```

**Requirements:** Linux, mount privileges (`sudo`), and a `scratch_dir` on an xattr-capable filesystem (ext4/xfs) with room for the run's writes — keep it on the big disk beside the snapshot. (macOS has no `overlayfs`, so this is a remote/Linux-host feature.)

You can also drive it directly, which is what both flows call under the hood:

```bash
PYTHONPATH=src uv run python -m orchestrator.overlay up     --config config.yml   # restore + mount fresh
PYTHONPATH=src uv run python -m orchestrator.overlay status --config config.yml   # is it mounted?
PYTHONPATH=src uv run python -m orchestrator.overlay env    --config config.yml   # print `export <db_path_env>=<merged>`
PYTHONPATH=src uv run python -m orchestrator.overlay down   --config config.yml   # unmount + wipe scratch
```

## Per-client templates (`templates/`)

Each `templates/<client>/` is a **self-contained, script-managed stack** for benchmarking a client against a pre-synced snapshot on the host disk. `config_<client>.yml` sets `infrastructure.manage: false` so the orchestrator never touches Docker — the scripts own the lifecycle. To add a client, copy a directory wholesale: drop in the client compose, `config_<client>.yml`, and `.env.<client>`; the scripts derive the client name from the directory.

```bash
./templates/<client>/start_infra.sh            # up: monitoring + the client (idempotent)
./templates/<client>/run_benchmark.sh <ms>     # runner --skip-sync (+ background mock-CL replay)
./templates/<client>/start_infra.sh down [-v]  # tear down (-v wipes volumes)
```

`run_benchmark.sh` launches the mock-CL **replay** in the background (advancing the head under load) whenever `MOCK_CL_PAYLOADS` resolves to a file; otherwise replay is skipped with a warning.

* **`templates/geth/`** — Geth bloatnet snapshot (snap-synced; no `--syncmode`/`--gcmode` pinning).
* **`templates/neth/`** — Nethermind on the x3.5 bloatnet snapshot (mainnet shadowfork: chainId 1, p2p `--Init.NetworkId=12159`, FlatDb). The image **must** match the one that wrote the snapshot's FlatDb state, and the chainspec is mounted from the host. Ports avoid a co-located host-net Geth (RPC 8547 / engine 8552 / metrics 6060). Run `runner.py` with `--client nethermind` (the framework key; the directory is `neth` only for the path).

> **cAdvisor on the containerd image store:** where Docker uses the containerd snapshotter (`Storage Driver: overlayfs`), stock cAdvisor (≤ v0.52) emits no `name="benchmark_*"` series, so the Grafana **Client** variable is empty and every per-container CPU/RSS/disk/net query returns nothing. Build the patched image once (`docker build -t cadvisor-layerdb-fix:v0.49.1 docker/cadvisor`) and set `CADVISOR_IMAGE=cadvisor-layerdb-fix:v0.49.1` in `.env.<client>` — no dockerd restart needed.

> **Load-testing a replay-driven node:** a load test that pins *recent* blocks will hit `-32002 No state available` if the node keeps only a shallow state window and replay advances the head faster than `load_test.block_window_refresh_sec`. For **FlatDb** nodes the queryable window tracks `--FlatDb.MinReorgDepth` (default 128) up to `--FlatDb.MaxReorgDepth` (default 256) — raise both above `load_test.recent_block_window` (the neth template uses `1024`/`2048` for a 1000-block window; this costs memory, one state-snapshot bundle per retained block, and the deeper retention only applies to blocks processed *after* the change). As a client-agnostic fallback, set `load_test.min_pinned_window` above the node's servable band to query `latest` instead, and/or bound replay with `--count`.

## Testing

The suite uses **pytest** (declared in `pyproject.toml` under `[dependency-groups].dev`). The `integration` marker is registered in `[tool.pytest.ini_options]` and excluded by default via `addopts`, so a plain `uv run pytest` is fast and Docker-free.

```bash
uv run pytest                   # unit tests only (default; ~0.2 s, no Docker)
uv run pytest -m integration    # adds the docker-stack integration test (~80 s, needs Docker)
uv run pytest -m ""             # everything
```

Unit tests (`tests/unit/`) cover the aggregator's parser/comparator helpers (`render_query`, `parse_locust_stats`, `parse_proof_sizes`, `find_previous_milestone`, `compare_against_previous`, `write_csv_summary`, `query_prometheus` error handling) and the runner's small utilities (`detect_compose_cmd`, `measure_db_size`, `write_sync_metrics`). The integration test (`tests/integration/`) brings up `docker/docker-compose.test.yml`, runs the seeder, drives the orchestrator end-to-end via subprocess, and asserts the produced JSON has non-zero metrics across the spec's key categories (`block_proc_p95`, `proof_p50_bytes`, `rpc_p95_latency_ms`, `rpc_requests_sec`). A second integration test drives two consecutive milestones to verify the cross-milestone regression comparator picks up the prior run.

## Out of scope (follow-up)

* Cross-client delta dashboards on top of the existing Grafana provisioning (e.g. side-by-side panels for `chain_execution` across Nethermind / Geth / Besu / Reth / Erigon at the same milestone).
* Repeatability harness (`--repeat N`, ±5% statistical bound).
* Distributed (master/worker) Locust: the in-process Prometheus exporter and proof-size CSV are process-local, so the locustfile rejects `--master`/`--worker` at init. A single-process `FastHttpUser` saturates a single-node RPC endpoint comfortably; revisit if a multi-machine load source is ever needed.
