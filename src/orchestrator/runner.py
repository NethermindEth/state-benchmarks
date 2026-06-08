import argparse
import json
import logging
import os
import shlex
import subprocess
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

def detect_compose_cmd() -> List[str]:
    """Prefer 'docker compose' (v2 plugin); fall back to legacy 'docker-compose'."""
    for candidate in (["docker", "compose"], ["docker-compose"]):
        try:
            subprocess.run(candidate + ["version"], capture_output=True, check=True)
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

def start_infrastructure(client: str):
    if not MANAGE_INFRA:
        logger.info("Infrastructure management is disabled in config. Skipping start.")
        return

    logger.info("Starting monitoring stack...")
    run_cmd(COMPOSE_CMD + ["-f", MONITORING_COMPOSE, "up", "-d"])

    logger.info(f"Starting {client} node...")
    run_cmd(COMPOSE_CMD + ["-f", CLIENTS_COMPOSE, "up", "-d", client])

def stop_infrastructure():
    if not MANAGE_INFRA:
        logger.info("Infrastructure management is disabled in config. Skipping stop.")
        return

    logger.info("Stopping all containers...")
    try:
        run_cmd(COMPOSE_CMD + ["-f", CLIENTS_COMPOSE, "down", "-v"])
    except subprocess.CalledProcessError:
        pass
    try:
        run_cmd(COMPOSE_CMD + ["-f", MONITORING_COMPOSE, "down", "-v"])
    except subprocess.CalledProcessError:
        pass

def wait_for_rpc(timeout: int = 60):
    start_time = time.time()
    payload = {"jsonrpc": "2.0", "method": "web3_clientVersion", "params": [], "id": 1}
    while time.time() - start_time < timeout:
        try:
            response = requests.post(RPC_URL, json=payload, timeout=2)
            if response.status_code == 200:
                logger.info(f"RPC server is up: {response.json().get('result')}")
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(2)
    logger.error("RPC server failed to start within timeout.")
    return False

def check_sync_status() -> bool:
    payload = {"jsonrpc": "2.0", "method": "eth_syncing", "params": [], "id": 1}
    try:
        response = requests.post(RPC_URL, json=payload)
        result = response.json().get("result")
        return result is False
    except requests.exceptions.RequestException:
        return False

def measure_sync_time() -> float:
    logger.info("Waiting for sync to complete. This may take a long time...")
    start_time = time.time()
    while not check_sync_status():
        time.sleep(10)
        logger.info(f"Still syncing... elapsed: {int(time.time() - start_time)}s")
    sync_time = time.time() - start_time
    logger.info(f"Sync completed in {sync_time:.2f} seconds.")
    return sync_time

def measure_db_size(client: str) -> int:
    logger.info(f"Measuring DB size for {client}...")
    container_name = f"benchmark_{client}"
    dir_path = DATA_DIRS.get(client)

    if not dir_path:
        logger.warning(f"No data directory configured for {client}")
        return 0

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
        return 0

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

def run_locust_load_test(config_path: str, milestone_dir: str, client: str):
    load_cfg = CONFIG.get("load_test", {})
    users = int(load_cfg.get("users", 20))
    spawn_rate = int(load_cfg.get("spawn_rate", 10))
    run_time = str(load_cfg.get("run_time", "5m"))

    client_dir = os.path.join(milestone_dir, client)
    os.makedirs(client_dir, exist_ok=True)
    csv_prefix = os.path.join(client_dir, "locust_stats")

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
    ], check=True, env=os.environ | {
        "LOCUST_CONFIG": config_path,
        "LOCUST_PROOF_SIZES_CSV": os.path.join(client_dir, "proof_sizes.csv"),
    })
    logger.info("Locust load test completed.")

def run_benchmark_for_client(client: str, milestone: str, skip_sync: bool, config_path: str, stop_monitoring: bool):
    logger.info(f"=== Starting benchmark for {client} ===")
    milestone_dir = os.path.join("benchmarks", milestone)
    os.makedirs(milestone_dir, exist_ok=True)

    try:
        start_infrastructure(client)
        if not wait_for_rpc():
            logger.error(f"Skipping {client} because RPC failed to come up.")
            return

        sync_time: Optional[float] = None
        db_size: Optional[int] = None
        if not skip_sync:
            sync_time = measure_sync_time()
            db_size = measure_db_size(client)
        else:
            # Honor an existing snapshot if --skip-sync is set and we previously captured one.
            existing = os.path.join(milestone_dir, f"sync_metrics_{client}.json")
            if os.path.exists(existing):
                with open(existing) as f:
                    prev = json.load(f)
                sync_time = prev.get("sync_time_sec")
                db_size = prev.get("db_size_bytes")
                logger.info(f"Reusing existing sync metrics: sync_time={sync_time}, db_size={db_size}")

        write_sync_metrics(milestone_dir, client, sync_time, db_size)

        run_locust_load_test(config_path, milestone_dir, client)

        logger.info("Invoking metrics aggregator...")
        subprocess.run([
            "uv", "run", "python", "src/metrics/aggregator.py",
            "--config", config_path,
            "--client", client,
            "--milestone", milestone,
        ], check=True)
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

    args = parser.parse_args()

    global CONFIG, RPC_URL, SSH_CMD, MANAGE_INFRA, MONITORING_COMPOSE, CLIENTS_COMPOSE, DATA_DIRS, COMPOSE_CMD
    CONFIG = load_config(args.config)
    RPC_URL = CONFIG.get("nodes", {}).get("rpc_url", "http://localhost:8545")
    SSH_CMD = CONFIG.get("infrastructure", {}).get("ssh_command", "")
    MANAGE_INFRA = CONFIG.get("infrastructure", {}).get("manage", True)
    MONITORING_COMPOSE = CONFIG.get("infrastructure", {}).get("compose_paths", {}).get("monitoring", "docker/docker-compose.monitoring.yml")
    CLIENTS_COMPOSE = CONFIG.get("infrastructure", {}).get("compose_paths", {}).get("clients", "docker/docker-compose.clients.yml")
    DATA_DIRS = CONFIG.get("nodes", {}).get("data_dirs", {})
    COMPOSE_CMD = detect_compose_cmd() if MANAGE_INFRA else ["docker-compose"]

    clients_to_run = [args.client] if args.client else CONFIG.get("nodes", {}).get("clients", [])

    if not clients_to_run:
        logger.error("No clients specified in args or config.yml.")
        return

    for client in clients_to_run:
        run_benchmark_for_client(client, args.milestone, args.skip_sync, args.config, args.stop_monitoring)

if __name__ == "__main__":
    main()
