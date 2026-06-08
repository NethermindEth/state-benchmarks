import csv
import json
import logging
import os
import random
import threading
import time
from typing import List

import requests
import yaml
from locust import FastHttpUser, between, events, task
from locust.runners import MasterRunner, WorkerRunner
from prometheus_client import Counter, Gauge, Histogram, start_http_server

_logger = logging.getLogger("locust.benchmark")

LOCUST_PROMETHEUS_PORT = int(os.environ.get("LOCUST_PROMETHEUS_PORT", "9646"))

# Buckets in milliseconds — JSON-RPC eth_* calls typically land in 1–100ms;
# extending to 5s covers slow sync / sluggish-client outliers.
_LATENCY_BUCKETS = (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)

LOCUST_REQUESTS = Counter(
    "locust_requests_total",
    "Total Locust requests by method, name, and result (success|failure).",
    ["method", "name", "result"],
)
LOCUST_LATENCY = Histogram(
    "locust_request_latency_ms",
    "Locust request latency in milliseconds.",
    ["method", "name"],
    buckets=_LATENCY_BUCKETS,
)
LOCUST_RESPONSE_BYTES = Counter(
    "locust_response_bytes_total",
    "Total response bytes received by Locust, by method/name.",
    ["method", "name"],
)
LOCUST_USERS = Gauge("locust_users", "Currently spawned Locust users.")
LOCUST_TEST_RUNNING = Gauge(
    "locust_test_running", "1 while a Locust test is running, 0 otherwise."
)


def load_config(path: str = "config.yml"):
    with open(path, 'r') as f:
        return yaml.safe_load(f)


CONFIG = load_config(os.environ.get("LOCUST_CONFIG", "config.yml"))
LOAD_CFG = CONFIG.get("load_test", {})
ADDRESSES: List[str] = LOAD_CFG.get("addresses", ["0x0000000000000000000000000000000000000000"])
SLOTS: List[str] = LOAD_CFG.get("slots", ["0x0"])
ETH_CALL_SHAPES: List[str] = LOAD_CFG.get("eth_call_shapes", ["0x18160ddd"])
RECENT_BLOCK_WINDOW: int = int(LOAD_CFG.get("recent_block_window", 1000))

# Populated in on_test_start. Defaults keep the harness usable even if the
# RPC bootstrap fails (e.g., dry runs against a stub).
RECENT_BLOCKS: List[str] = ["latest"]

PROOF_SIZES_CSV = os.environ.get("LOCUST_PROOF_SIZES_CSV")
_proof_lock = threading.Lock()
_proof_file = None
_proof_writer = None


def _random_block_tag() -> str:
    return random.choice(RECENT_BLOCKS)


@events.init.add_listener
def on_locust_init(environment, **kwargs):
    # Expose Prometheus metrics from the master (or a single-process local run);
    # workers report into the master, so only one HTTP server is needed.
    if isinstance(environment.runner, WorkerRunner):
        return
    try:
        start_http_server(LOCUST_PROMETHEUS_PORT)
        _logger.info(
            f"Locust Prometheus exporter listening on :{LOCUST_PROMETHEUS_PORT}/metrics"
        )
    except OSError as e:
        # Port already bound — likely a previous run leaked it; keep going so
        # the load test still produces CSVs even without live metrics.
        _logger.warning(f"Locust Prometheus exporter failed to bind: {e}")


@events.request.add_listener
def on_request(
    request_type, name, response_time, response_length, exception, **kwargs
):
    result = "failure" if exception else "success"
    LOCUST_REQUESTS.labels(method=request_type, name=name, result=result).inc()
    LOCUST_LATENCY.labels(method=request_type, name=name).observe(response_time)
    if response_length:
        LOCUST_RESPONSE_BYTES.labels(method=request_type, name=name).inc(
            response_length
        )


def _user_count_poller(environment):
    while getattr(environment.runner, "state", None) in (
        "spawning",
        "running",
        "ready",
    ):
        try:
            LOCUST_USERS.set(environment.runner.user_count)
        except Exception:
            pass
        time.sleep(1)
    LOCUST_USERS.set(0)


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Fetch head block and pre-compute a window of recent block tags."""
    global RECENT_BLOCKS, _proof_file, _proof_writer

    host = environment.host or "http://localhost:8545"
    try:
        resp = requests.post(
            host,
            json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
            timeout=5,
        )
        head_hex = resp.json().get("result")
        head = int(head_hex, 16)
        window = max(1, min(RECENT_BLOCK_WINDOW, head))
        RECENT_BLOCKS = [hex(head - i) for i in range(window)]
        _logger.info(f"Initialized recent-block window: head={head}, window={window}")
    except Exception as e:
        # Fall back to "latest" so tasks still run; flagged in the log so the
        # final report makes the cache-skew risk obvious.
        _logger.warning(f"Failed to fetch head block from {host}: {e}. Falling back to 'latest'.")
        RECENT_BLOCKS = ["latest"]

    if PROOF_SIZES_CSV:
        os.makedirs(os.path.dirname(PROOF_SIZES_CSV) or ".", exist_ok=True)
        _proof_file = open(PROOF_SIZES_CSV, 'w', newline='')
        _proof_writer = csv.writer(_proof_file)
        _proof_writer.writerow(["timestamp_ms", "address", "block", "response_bytes"])

    LOCUST_TEST_RUNNING.set(1)
    if not isinstance(environment.runner, WorkerRunner):
        t = threading.Thread(
            target=_user_count_poller, args=(environment,), daemon=True
        )
        t.start()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    global _proof_file
    if _proof_file:
        _proof_file.close()
        _proof_file = None
    LOCUST_TEST_RUNNING.set(0)
    LOCUST_USERS.set(0)


class EthereumRPCUser(FastHttpUser):
    wait_time = between(0.1, 0.5)

    def rpc_call(self, method, params, name=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        name = name or method
        with self.client.post("/", json=payload, name=name, catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return None
            try:
                resp_json = response.json()
            except json.JSONDecodeError:
                response.failure("Failed to parse JSON")
                return None
            if "error" in resp_json:
                response.failure(f"RPC Error: {resp_json['error']}")
                return None
            response.success()
            return response

    @task(3)
    def eth_get_balance(self):
        self.rpc_call("eth_getBalance", [random.choice(ADDRESSES), _random_block_tag()])

    @task(3)
    def eth_get_storage_at(self):
        self.rpc_call(
            "eth_getStorageAt",
            [random.choice(ADDRESSES), random.choice(SLOTS), _random_block_tag()],
        )

    @task(2)
    def eth_get_code(self):
        self.rpc_call("eth_getCode", [random.choice(ADDRESSES), _random_block_tag()])

    @task(2)
    def eth_call(self):
        address = random.choice(ADDRESSES)
        data = random.choice(ETH_CALL_SHAPES)
        self.rpc_call("eth_call", [{"to": address, "data": data}, _random_block_tag()])

    @task(1)
    def eth_get_proof(self):
        address = random.choice(ADDRESSES)
        block = _random_block_tag()
        slots = [random.choice(SLOTS)]
        response = self.rpc_call("eth_getProof", [address, slots, block], name="eth_getProof")
        if response is not None and _proof_writer is not None:
            size = len(response.content)
            # Locust runs in greenlets but multiple users share the writer; lock.
            with _proof_lock:
                _proof_writer.writerow([int(time.time() * 1000), address, block, size])
