# Ethereum Node Benchmarking Framework

A comprehensive, configuration-driven benchmarking framework designed to evaluate the performance of Ethereum execution clients (Nethermind, Geth, Besu, Reth, Erigon). It orchestrates node synchronization, measures system resource utilization, and executes heavy JSON-RPC load tests to determine client capabilities under stress.

## Architecture Overview

The framework adopts a **Control Plane + Load Engine** architecture to isolate the testing harness from the target environment.

*   **Orchestrator (`src/orchestrator/runner.py`)**: The central control plane. It manages the lifecycle of Docker containers, monitors the initial blockchain sync phase (capturing wall time and DB size), and coordinates the load testing and metrics aggregation phases.
*   **Load Generator (`src/load_tests/locustfile.py`)**: Powered by Locust, this engine utilizes `FastHttpUser` to simulate high-concurrency JSON-RPC traffic (`eth_getBalance`, `eth_call`, `eth_getProof`, etc.) against the synchronized node.
*   **Metrics Aggregator (`src/metrics/aggregator.py`)**: Post-test, this module queries Prometheus to gather peak and sustained system metrics (CPU, RSS, IOPS) and ingests Locust's latency percentiles. It automatically compares current runs against previous milestones to flag regressions exceeding 25%.
*   **Monitoring Stack (`docker/`)**: A pre-configured Docker Compose environment encompassing Prometheus, cAdvisor, and node_exporter.

## Prerequisites

*   [**uv**](https://github.com/astral-sh/uv): The fast Python package installer and resolver.
*   [**Docker**](https://docs.docker.com/get-docker/) and [**Docker Compose**](https://docs.docker.com/compose/install/): Required for running the clients and the monitoring stack.

## Setup Instructions

1.  Clone the repository.
2.  Initialize the project and install dependencies using `uv`:

```bash
# This will automatically read pyproject.toml and set up the .venv
uv sync
```

## Configuration Guide

The framework is highly flexible, driven entirely by `config.yml`. This allows you to easily switch between testing local Docker containers and orchestrating remote bare-metal servers.

### `config.yml` Structure

```yaml
infrastructure:
  manage: true  # Set to false if testing against an already running remote node
  ssh_command: "" # E.g., "ssh user@192.168.1.100". If provided, Docker commands execute remotely.
  compose_paths:
    monitoring: "docker/docker-compose.monitoring.yml"
    clients: "docker/docker-compose.clients.yml"

nodes:
  rpc_url: "http://localhost:8545" # The endpoint Locust will target
  clients:
    - "geth"
    - "nethermind"
  data_dirs:
    # Used to measure final DB size via `du -sb`. Must match container mounts.
    nethermind: "/nethermind/data"
    geth: "/root/.ethereum"

metrics:
  prometheus_url: "http://localhost:9090"
  queries:
    cpu_percent: 'rate(container_cpu_usage_seconds_total{name="benchmark_{client}"}[5m]) * 100'
    rss_bytes: 'container_memory_rss{name="benchmark_{client}"}'

load_test:
  # The inputs Locust will use for its RPC calls
  addresses:
    - "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
  slots:
    - "0x0"
```

## Usage Examples

Run the orchestrator using `uv run`. The orchestrator will automatically read `config.yml` unless an override is provided.

**Run a benchmark for a specific client:**
```bash
uv run python src/orchestrator/runner.py --client nethermind --milestone v1.0.0
```

**Skip the sync phase (if the node is already synced and running):**
```bash
uv run python src/orchestrator/runner.py --client geth --milestone v1.0.0 --skip-sync
```

**Run using a custom configuration file (e.g., targeting a remote production node):**
```bash
uv run python src/orchestrator/runner.py --config remote-config.yml --client besu --milestone v1.1.0 --skip-sync
```

**Run benchmarks for all clients defined in your config:**
```bash
# Omitting the --client flag iterates through the config.yml `clients` list
uv run python src/orchestrator/runner.py --milestone all-clients-baseline
```

## Output Structure

The orchestrator dumps its final aggregated results into the `benchmarks/` directory, organized by the `--milestone` label you provided.

```text
benchmarks/
└── v1.0.0/
    ├── metrics_nethermind.json
    └── metrics_geth.json
```

A sample output JSON file looks like this:

```json
{
    "cpu_percent": 145.2,
    "rss_bytes": 12058624000.0,
    "rpc_p50_latency": 12.0,
    "rpc_p95_latency": 45.0,
    "rpc_p99_latency": 110.0,
    "rpc_requests_sec": 1250.5
}
```

During execution, if the aggregator detects that a metric has degraded by more than 25% compared to a previous milestone JSON in the same directory, it will log a `[WARNING] REGRESSION DETECTED!` in the console.
