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
from locust import FastHttpUser, between, events
from locust.runners import MasterRunner, WorkerRunner
from prometheus_client import Counter, Gauge, Histogram, start_http_server

_logger = logging.getLogger("locust.benchmark")

LOCUST_PROMETHEUS_PORT = int(os.environ.get("LOCUST_PROMETHEUS_PORT", "9646"))

# Defaults in milliseconds — JSON-RPC eth_* calls typically land in 1–100ms;
# extending to 5s covers slow sync / sluggish-client outliers.
_DEFAULT_LATENCY_BUCKETS = (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)

LOCUST_REQUESTS = Counter(
    "locust_requests_total",
    "Total Locust requests by method, name, and result (success|failure).",
    ["method", "name", "result"],
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
    try:
        with open(path, 'r') as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise SystemExit(
            f"Locust benchmark config not found at {path!r}. "
            "Set the LOCUST_CONFIG environment variable to a valid config file "
            "(e.g. config.yml or test-config.yml), or run from the repo root."
        )


CONFIG = load_config(os.environ.get("LOCUST_CONFIG", "config.yml"))
LOAD_CFG = CONFIG.get("load_test", {})
ADDRESSES: List[str] = LOAD_CFG.get("addresses", ["0x0000000000000000000000000000000000000000"])
# Extra runtime-discovered targets (e.g. the contract deployed by
# scripts/seed_test_node.py, injected by the runner) — comma-separated.
_extra_addresses = [
    a.strip() for a in os.environ.get("LOCUST_EXTRA_ADDRESSES", "").split(",") if a.strip()
]
for _addr in _extra_addresses:
    if _addr.lower() not in (a.lower() for a in ADDRESSES):
        ADDRESSES.append(_addr)
SLOTS: List[str] = LOAD_CFG.get("slots", ["0x0"])
ETH_CALL_SHAPES: List[str] = LOAD_CFG.get("eth_call_shapes", ["0x18160ddd"])
RECENT_BLOCK_WINDOW: int = int(LOAD_CFG.get("recent_block_window", 1000))
LATENCY_BUCKETS = tuple(LOAD_CFG.get("latency_buckets", _DEFAULT_LATENCY_BUCKETS))
_WAIT_CFG = LOAD_CFG.get("wait_time", {"min": 0.1, "max": 0.5})
WAIT_MIN: float = float(_WAIT_CFG.get("min", 0.1))
WAIT_MAX: float = float(_WAIT_CFG.get("max", 0.5))
TASK_WEIGHTS = LOAD_CFG.get("task_weights", {
    "eth_getBalance": 3,
    "eth_getStorageAt": 3,
    "eth_call": 2,
    "eth_getCode": 2,
    "eth_getProof": 1,
})

LOCUST_LATENCY = Histogram(
    "locust_request_latency_ms",
    "Locust request latency in milliseconds.",
    ["method", "name"],
    buckets=LATENCY_BUCKETS,
)

# Populated in on_test_start. Defaults keep the harness usable even if the
# RPC bootstrap fails (e.g., dry runs against a stub).
RECENT_BLOCKS: List[str] = ["latest"]

PROOF_SIZES_CSV = os.environ.get("LOCUST_PROOF_SIZES_CSV")
_PROOF_FLUSH_EVERY = 100  # rows between flushes, so a crash loses at most this many samples
_proof_lock = threading.Lock()
_proof_file = None
_proof_writer = None
_proof_rows_since_flush = 0


def _random_block_tag() -> str:
    return random.choice(RECENT_BLOCKS)


@events.init.add_listener
def on_locust_init(environment, **kwargs):
    # Distributed master/worker mode is unsupported: the Prometheus counters and
    # the proof-size CSV are process-local, so workers would record into state
    # that is never exported (master serves /metrics with all-zero counters) and
    # all workers would interleave writes into the same CSV file.
    if isinstance(environment.runner, (MasterRunner, WorkerRunner)):
        raise RuntimeError(
            "locustfile.py does not support distributed master/worker mode: "
            "Prometheus metrics and the proof-size CSV are process-local. "
            "Run single-process (--headless without --master/--worker)."
        )
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
        with _proof_lock:
            _proof_file = open(PROOF_SIZES_CSV, 'w', newline='')
            _proof_writer = csv.writer(_proof_file)
            _proof_writer.writerow(["timestamp_ms", "address", "block", "response_bytes"])

    LOCUST_TEST_RUNNING.set(1)
    t = threading.Thread(
        target=_user_count_poller, args=(environment,), daemon=True
    )
    t.start()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    global _proof_file, _proof_writer
    # Clear the writer under the lock BEFORE closing the file so a straggling
    # eth_getProof greenlet can't write to a closed file.
    with _proof_lock:
        _proof_writer = None
        if _proof_file:
            _proof_file.close()
            _proof_file = None
    LOCUST_TEST_RUNNING.set(0)
    LOCUST_USERS.set(0)


class EthereumRPCUser(FastHttpUser):
    wait_time = between(WAIT_MIN, WAIT_MAX)

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

    def eth_get_balance(self):
        self.rpc_call("eth_getBalance", [random.choice(ADDRESSES), _random_block_tag()])

    def eth_get_storage_at(self):
        self.rpc_call(
            "eth_getStorageAt",
            [random.choice(ADDRESSES), random.choice(SLOTS), _random_block_tag()],
        )

    def eth_get_code(self):
        self.rpc_call("eth_getCode", [random.choice(ADDRESSES), _random_block_tag()])

    def eth_call(self):
        address = random.choice(ADDRESSES)
        data = random.choice(ETH_CALL_SHAPES)
        self.rpc_call("eth_call", [{"to": address, "data": data}, _random_block_tag()])

    def eth_get_proof(self):
        global _proof_rows_since_flush
        address = random.choice(ADDRESSES)
        block = _random_block_tag()
        slots = [random.choice(SLOTS)]
        response = self.rpc_call("eth_getProof", [address, slots, block], name="eth_getProof")
        if response is not None:
            size = len(response.content)
            # Multiple users share the writer, and test_stop may clear it
            # concurrently — re-check under the lock.
            with _proof_lock:
                if _proof_writer is None:
                    return
                _proof_writer.writerow([int(time.time() * 1000), address, block, size])
                _proof_rows_since_flush += 1
                if _proof_rows_since_flush >= _PROOF_FLUSH_EVERY:
                    _proof_file.flush()
                    _proof_rows_since_flush = 0

    # Weights driven by load_test.task_weights in the YAML config.
    tasks = {
        eth_get_balance:    TASK_WEIGHTS.get("eth_getBalance", 3),
        eth_get_storage_at: TASK_WEIGHTS.get("eth_getStorageAt", 3),
        eth_get_code:       TASK_WEIGHTS.get("eth_getCode", 2),
        eth_call:           TASK_WEIGHTS.get("eth_call", 2),
        eth_get_proof:      TASK_WEIGHTS.get("eth_getProof", 1),
    }
