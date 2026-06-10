import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

import requests
import yaml

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def load_config(path: str = "config.yml") -> Dict[str, Any]:
    with open(path, 'r') as f:
        return yaml.safe_load(f)

CONFIG: Dict[str, Any] = {}

RPC_URL = "http://localhost:8545"
SSH_CMD = ""
MANAGE_INFRA = True
MONITORING_COMPOSE = "docker/docker-compose.monitoring.yml"
CLIENTS_COMPOSE = "docker/docker-compose.clients.yml"
DATA_DIRS: Dict[str, str] = {}
COMPOSE_CMD: List[str] = ["docker-compose"]
CONSENSUS = False
RPC_STARTUP_TIMEOUT = 300
SYNC_HEAD_FRESHNESS_SEC = 120
SEED_STATE_FILE = os.path.join("benchmarks", ".seed-state.json")

def detect_compose_cmd() -> List[str]:
    """Prefer 'docker compose' (v2 plugin); fall back to legacy 'docker-compose'.

    Probes through run_cmd so the check happens on the machine that will run
    compose (the remote host when ssh_command is configured, local otherwise).
    """
    for candidate in (["docker", "compose"], ["docker-compose"]):
        try:
            run_cmd(candidate + ["version"], capture_output=True, check=True)
            return candidate
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    logger.warning("Neither 'docker compose' nor 'docker-compose' is available; using 'docker-compose' anyway.")
    return ["docker-compose"]

def run_cmd(cmd: List[str], capture_output: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    """Wraps subprocess.run to prepend SSH command if configured."""
    final_cmd = cmd
    if SSH_CMD:
        ssh_prefix = shlex.split(SSH_CMD)
        cmd_str = " ".join(shlex.quote(c) for c in cmd)
        final_cmd = ssh_prefix + [cmd_str]

    logger.info(f"Running command: {final_cmd}")
    return subprocess.run(final_cmd, capture_output=capture_output, text=True if capture_output else False, check=check)

def read_env_file(path: str = ".env") -> Dict[str, str]:
    """Minimal KEY=VALUE parser for the repo-root .env (shared with compose)."""
    values: Dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values

def env_flag(name: str, default: bool, env_file_values: Dict[str, str]) -> bool:
    """Boolean from the process environment or .env (env wins); else default."""
    raw = os.environ.get(name, env_file_values.get(name))
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")

def compose_cmd(compose_file: str) -> List[str]:
    # Compose resolves the default .env next to the compose file (docker/), so a
    # repo-root .env would be silently ignored — point at it explicitly when it
    # exists. (Not --project-directory: that would also re-root the compose
    # files' relative volume paths away from docker/.) With ssh_command set this
    # checks the local .env but compose runs remotely; keep the remote cwd's
    # layout in sync.
    cmd = list(COMPOSE_CMD)
    if os.path.exists(".env"):
        cmd += ["--env-file", ".env"]
    return cmd + ["-f", compose_file]

def start_infrastructure(client: str):
    if not MANAGE_INFRA:
        logger.info("Infrastructure management is disabled in config. Skipping start.")
        return

    logger.info("Starting monitoring stack...")
    run_cmd(compose_cmd(MONITORING_COMPOSE) + ["up", "-d"])

    logger.info(f"Starting {client} node...")
    run_cmd(compose_cmd(CLIENTS_COMPOSE) + ["up", "-d", client])

    if CONSENSUS:
        # Each EL client has a matching lighthouse-<client> service in the
        # clients compose file driving its engine API (mainnet sync needs a CL).
        cl_service = f"lighthouse-{client}"
        logger.info(f"Starting consensus client {cl_service}...")
        try:
            run_cmd(compose_cmd(CLIENTS_COMPOSE) + ["up", "-d", cl_service])
        except subprocess.CalledProcessError:
            logger.warning(
                f"Could not start {cl_service}. Without a consensus client the node "
                "will not sync mainnet; set nodes.consensus: false to silence this."
            )

def stop_infrastructure():
    if not MANAGE_INFRA:
        logger.info("Infrastructure management is disabled in config. Skipping stop.")
        return

    logger.info("Stopping all containers...")
    try:
        run_cmd(compose_cmd(CLIENTS_COMPOSE) + ["down", "-v"])
    except subprocess.CalledProcessError:
        pass
    try:
        run_cmd(compose_cmd(MONITORING_COMPOSE) + ["down", "-v"])
    except subprocess.CalledProcessError:
        pass

def rpc_call(method: str, params: Optional[list] = None, timeout: int = 5) -> Any:
    """Single JSON-RPC call; returns the result, or None if unreachable/errored."""
    payload = {"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}
    try:
        response = requests.post(RPC_URL, json=payload, timeout=timeout)
        if response.status_code != 200:
            return None
        return response.json().get("result")
    except (requests.exceptions.RequestException, ValueError):
        return None

def wait_for_rpc(timeout: Optional[int] = None):
    timeout = timeout if timeout is not None else RPC_STARTUP_TIMEOUT
    start_time = time.time()
    while time.time() - start_time < timeout:
        result = rpc_call("web3_clientVersion", timeout=2)
        if result is not None:
            logger.info(f"RPC server is up: {result}")
            return True
        time.sleep(2)
    logger.error(f"RPC server failed to start within {timeout}s.")
    return False

def check_sync_status() -> Optional[bool]:
    """True = syncing, False = not syncing, None = node unreachable."""
    result = rpc_call("eth_syncing")
    if result is None:
        return None
    return result is not False

def head_is_fresh(freshness_sec: Optional[int] = None) -> bool:
    """True when the chain head is non-zero and its timestamp is recent.

    Guards against eth_syncing's biggest lie: every client reports `false`
    *before* sync starts (no peers / no consensus client driving it).
    """
    freshness_sec = freshness_sec if freshness_sec is not None else SYNC_HEAD_FRESHNESS_SEC
    block = rpc_call("eth_getBlockByNumber", ["latest", False])
    if not block:
        return False
    try:
        number = int(block["number"], 16)
        timestamp = int(block["timestamp"], 16)
    except (KeyError, TypeError, ValueError):
        return False
    return number > 0 and (time.time() - timestamp) < freshness_sec

def measure_sync_time(poll_interval: int = 10, confirmations: int = 3) -> Optional[float]:
    """Wall-clock the sync. Returns None if the node never actually synced.

    Sync is considered complete only after `confirmations` consecutive polls
    where eth_syncing is false AND the head block is fresh. If the node never
    reported syncing (it was already synced, or it can't sync at all because
    nothing drives its engine API), the measurement is not meaningful and we
    return None rather than a bogus near-zero duration.
    """
    logger.info("Waiting for sync to complete. This may take a long time...")
    start_time = time.time()
    saw_syncing = False
    consecutive_synced = 0
    stale_warned = False

    while True:
        syncing = check_sync_status()
        if syncing is True:
            saw_syncing = True
            consecutive_synced = 0
        elif syncing is False:
            if head_is_fresh():
                consecutive_synced += 1
                if consecutive_synced >= confirmations:
                    break
            else:
                consecutive_synced = 0
                if not saw_syncing and not stale_warned:
                    logger.warning(
                        "Node reports eth_syncing=false but the chain head is stale/zero. "
                        "It is most likely waiting for a consensus client (engine API) or peers "
                        "and will never sync in this configuration. Will keep waiting..."
                    )
                    stale_warned = True
        time.sleep(poll_interval)
        logger.info(f"Still syncing... elapsed: {int(time.time() - start_time)}s")

    if not saw_syncing:
        logger.warning(
            "Node never reported eth_syncing=true — it was already synced when the "
            "measurement started. Recording sync_time as null (use --skip-sync to reuse "
            "a previous measurement)."
        )
        return None

    sync_time = time.time() - start_time
    logger.info(f"Sync completed in {sync_time:.2f} seconds.")
    return sync_time

def measure_db_size(client: str) -> Optional[int]:
    """On-disk DB size in bytes, or None when it can't be measured (never a fake 0)."""
    logger.info(f"Measuring DB size for {client}...")
    container_name = f"benchmark_{client}"
    dir_path = DATA_DIRS.get(client)

    if not dir_path:
        logger.warning(f"No data directory configured for {client}")
        return None

    try:
        result = run_cmd(
            ["docker", "exec", container_name, "du", "-sb", dir_path],
            capture_output=True, check=True
        )
        size_bytes = int(result.stdout.split()[0])
        logger.info(f"DB size: {size_bytes} bytes")
        return size_bytes
    except (subprocess.CalledProcessError, ValueError, IndexError) as e:
        logger.error(f"Failed to measure DB size: {e}")
        return None

def write_sync_metrics(milestone_dir: str, client: str, sync_time: Optional[float], db_size: Optional[int]):
    """Persist what the runner measured directly so the aggregator can merge it."""
    path = os.path.join(milestone_dir, f"sync_metrics_{client}.json")
    payload = {
        "client": client,
        "sync_time_sec": sync_time,
        "db_size_bytes": db_size,
    }
    with open(path, 'w') as f:
        json.dump(payload, f, indent=2)
    logger.info(f"Wrote sync metrics to {path}")

def load_seeded_addresses() -> List[str]:
    """Extra target addresses recorded by scripts/seed_test_node.py, if any."""
    if not os.path.exists(SEED_STATE_FILE):
        return []
    try:
        with open(SEED_STATE_FILE) as f:
            state = json.load(f)
        contract = state.get("contract_address")
        return [contract] if contract else []
    except Exception as e:
        logger.warning(f"Could not read {SEED_STATE_FILE}: {e}")
        return []

def run_locust_load_test(config_path: str, milestone_dir: str, client: str):
    load_cfg = CONFIG.get("load_test", {})
    users = int(load_cfg.get("users", 20))
    spawn_rate = int(load_cfg.get("spawn_rate", 10))
    run_time = str(load_cfg.get("run_time", "5m"))

    client_dir = os.path.join(milestone_dir, client)
    os.makedirs(client_dir, exist_ok=True)
    csv_prefix = os.path.join(client_dir, "locust_stats")

    extra_env = {
        "LOCUST_CONFIG": config_path,
        "LOCUST_PROOF_SIZES_CSV": os.path.join(client_dir, "proof_sizes.csv"),
    }
    seeded = load_seeded_addresses()
    if seeded:
        extra_env["LOCUST_EXTRA_ADDRESSES"] = ",".join(seeded)
        logger.info(f"Including seeded addresses in the load test: {seeded}")

    logger.info(f"Running Locust load test (Users: {users}, Run time: {run_time}) -> {csv_prefix}")
    subprocess.run([
        "uv", "run", "locust",
        "-f", "src/load_tests/locustfile.py",
        "--headless",
        "-u", str(users),
        "-r", str(spawn_rate),
        "--run-time", run_time,
        "--host", RPC_URL,
        "--csv", csv_prefix,
    ], check=True, env=os.environ | extra_env)
    logger.info("Locust load test completed.")

def run_aggregator(config_path: str, client: str, milestone: str, phase: str, fail_on_regression: bool = False):
    cmd = [
        "uv", "run", "python", "src/metrics/aggregator.py",
        "--config", config_path,
        "--client", client,
        "--milestone", milestone,
        "--phase", phase,
    ]
    if fail_on_regression:
        cmd.append("--fail-on-regression")
    subprocess.run(cmd, check=True)

def run_benchmark_for_client(client: str, milestone: str, skip_sync: bool, config_path: str,
                             stop_monitoring: bool, fail_on_regression: bool = False):
    logger.info(f"=== Starting benchmark for {client} ===")
    milestone_dir = os.path.join("benchmarks", milestone)
    os.makedirs(milestone_dir, exist_ok=True)

    try:
        start_infrastructure(client)
        if not wait_for_rpc():
            raise RuntimeError(f"RPC for {client} failed to come up within {RPC_STARTUP_TIMEOUT}s.")

        sync_time: Optional[float] = None
        prev_db_size: Optional[int] = None
        if not skip_sync:
            sync_time = measure_sync_time()
            # Snapshot sync-phase system metrics now, while the Prometheus
            # sync_window still covers the sync. Non-fatal if Prometheus is down.
            logger.info("Capturing sync-phase Prometheus metrics...")
            try:
                run_aggregator(config_path, client, milestone, phase="sync")
            except subprocess.CalledProcessError as e:
                logger.warning(f"Sync-phase metrics capture failed: {e}")
        else:
            # Honor an existing snapshot if --skip-sync is set and we previously captured one.
            existing = os.path.join(milestone_dir, f"sync_metrics_{client}.json")
            if os.path.exists(existing):
                with open(existing) as f:
                    prev = json.load(f)
                sync_time = prev.get("sync_time_sec")
                prev_db_size = prev.get("db_size_bytes")
                logger.info(f"Reusing existing sync time: sync_time={sync_time}")

        # DB size doesn't depend on the sync phase having been observed; always measure.
        db_size = measure_db_size(client)
        if db_size is None and prev_db_size is not None:
            logger.info(f"Falling back to previously captured db_size: {prev_db_size}")
            db_size = prev_db_size

        write_sync_metrics(milestone_dir, client, sync_time, db_size)

        run_locust_load_test(config_path, milestone_dir, client)

        logger.info("Invoking metrics aggregator...")
        run_aggregator(config_path, client, milestone, phase="load", fail_on_regression=fail_on_regression)
    finally:
        if stop_monitoring:
            stop_infrastructure()
        else:
            logger.info("Leaving infrastructure running (pass --stop-monitoring to tear it down).")
    logger.info(f"=== Finished benchmark for {client} ===\n")

def main():
    parser = argparse.ArgumentParser(description="Ethereum Node Benchmarking Orchestrator")
    parser.add_argument("--config", type=str, default="config.yml", help="Path to config file")
    parser.add_argument("--client", type=str, help="Specific Ethereum client to benchmark (optional, defaults to config.yml list)")
    parser.add_argument("--milestone", type=str, required=True, help="Milestone label (e.g., v1.0.0)")
    parser.add_argument("--skip-sync", action="store_true", help="Skip the sync phase if already synced")
    parser.add_argument(
        "--stop-monitoring",
        action="store_true",
        help="Tear down the monitoring + clients stack after the run. "
             "Default is to leave it running so Grafana/Prometheus remain reachable for inspection.",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit non-zero if the aggregator detects a regression vs the previous milestone (for CI).",
    )

    args = parser.parse_args()

    global CONFIG, RPC_URL, SSH_CMD, MANAGE_INFRA, MONITORING_COMPOSE, CLIENTS_COMPOSE, DATA_DIRS, \
        COMPOSE_CMD, CONSENSUS, RPC_STARTUP_TIMEOUT, SYNC_HEAD_FRESHNESS_SEC
    CONFIG = load_config(args.config)
    nodes_cfg = CONFIG.get("nodes", {})
    RPC_URL = nodes_cfg.get("rpc_url", "http://localhost:8545")
    SSH_CMD = CONFIG.get("infrastructure", {}).get("ssh_command", "")
    MANAGE_INFRA = CONFIG.get("infrastructure", {}).get("manage", True)
    MONITORING_COMPOSE = CONFIG.get("infrastructure", {}).get("compose_paths", {}).get("monitoring", "docker/docker-compose.monitoring.yml")
    CLIENTS_COMPOSE = CONFIG.get("infrastructure", {}).get("compose_paths", {}).get("clients", "docker/docker-compose.clients.yml")
    DATA_DIRS = nodes_cfg.get("data_dirs", {})
    # CONSENSUS in the environment or repo-root .env overrides nodes.consensus,
    # so the same .env that configures compose can disable the lighthouse start.
    CONSENSUS = env_flag("CONSENSUS", bool(nodes_cfg.get("consensus", False)), read_env_file())
    RPC_STARTUP_TIMEOUT = int(nodes_cfg.get("rpc_startup_timeout_sec", 300))
    SYNC_HEAD_FRESHNESS_SEC = int(nodes_cfg.get("sync_head_freshness_sec", 120))
    COMPOSE_CMD = detect_compose_cmd() if MANAGE_INFRA else ["docker-compose"]

    clients_to_run = [args.client] if args.client else nodes_cfg.get("clients", [])

    if not clients_to_run:
        logger.error("No clients specified in args or config.yml.")
        sys.exit(1)

    # A failing client must not abort the rest of the matrix; collect and report.
    failed: List[str] = []
    for client in clients_to_run:
        try:
            run_benchmark_for_client(client, args.milestone, args.skip_sync, args.config,
                                     args.stop_monitoring, args.fail_on_regression)
        except Exception as e:
            logger.error(f"Benchmark for {client} failed: {e}")
            failed.append(client)

    if failed:
        logger.error(f"Finished with failures for: {', '.join(failed)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
