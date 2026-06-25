import csv
import json
import logging
import os
import random
import threading
import time
from typing import Dict, List, Optional

import requests
import yaml
from locust import FastHttpUser, between, events, tag
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
LOCUST_INITIAL_BLOCK = Gauge(
    "locust_initial_block_number",
    "Head block number observed when the Locust test started.",
)
LOCUST_CURRENT_BLOCK = Gauge(
    "locust_current_block_number",
    "Most recent head block number observed during the Locust test.",
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
# Below this many usable pinned blocks, fall back to the "latest" tag: pinned
# block numbers go stale as the node's retained-state band slides forward.
MIN_PINNED_WINDOW: int = int(LOAD_CFG.get("min_pinned_window", 8))
BLOCK_WINDOW_REFRESH_SEC: float = float(LOAD_CFG.get("block_window_refresh_sec", 30))
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
    "eth_sendTransaction": 1,
})
# Gas-paying tasks (tagged "gas") spend real funds outside dev networks, so
# they are off unless the config opts in.
GAS_TESTS_ENABLED = bool(LOAD_CFG.get("gas_tests_enabled", False))

LOCUST_LATENCY = Histogram(
    "locust_request_latency_ms",
    "Locust request latency in milliseconds.",
    ["method", "name"],
    buckets=LATENCY_BUCKETS,
)

# Populated in on_test_start. Defaults keep the harness usable even if the
# RPC bootstrap fails (e.g., dry runs against a stub).
RECENT_BLOCKS: List[str] = ["latest"]

# Block numbers observed across the run — surfaced in metrics and the final
# summary so every report ties back to a specific point on the chain.
INITIAL_HEAD_BLOCK: Optional[int] = None
LATEST_HEAD_BLOCK: Optional[int] = None

PROOF_SIZES_CSV = os.environ.get("LOCUST_PROOF_SIZES_CSV")
_PROOF_FLUSH_EVERY = 100  # rows between flushes, so a crash loses at most this many samples
_proof_lock = threading.Lock()
_proof_file = None
_proof_writer = None
_proof_rows_since_flush = 0


# Unlocked account used by gas-paying tasks; resolved in on_test_start.
GAS_SENDER: Optional[str] = None


def _random_block_tag() -> str:
    return random.choice(RECENT_BLOCKS)


def _state_available(host: str, block_hex: str) -> bool:
    """True if the node can serve state (via eth_getBalance) at the given block."""
    try:
        resp = requests.post(
            host,
            json={
                "jsonrpc": "2.0",
                "method": "eth_getBalance",
                "params": [ADDRESSES[0], block_hex],
                "id": 1,
            },
            timeout=5,
        )
        return "error" not in resp.json()
    except Exception:
        return False


def _probe_state_depth(host: str, head: int, max_depth: int) -> int:
    """Deepest depth d (block = head - d) with state available, capped at max_depth - 1.

    Pruned nodes (e.g. Nethermind with FlatDb) retain state only for a band of
    recent blocks; older blocks fail with -32002 "No state available". Bisect
    for the retention edge so the block window never reaches past it. Archive
    and dev nodes pass the first probe and skip the bisect entirely.
    """
    hi = max(0, min(max_depth - 1, head))
    if hi == 0 or _state_available(host, hex(head - hi)):
        return hi
    lo = 0  # head itself — if even head state is missing, the run is doomed anyway
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _state_available(host, hex(head - mid)):
            lo = mid
        else:
            hi = mid
    return lo


def _fetch_head(host: str) -> int:
    resp = requests.post(
        host,
        json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
        timeout=5,
    )
    return int(resp.json()["result"], 16)


# Blocks to keep clear of the retention edge: covers head advancing between
# window refreshes (~1 block / 12s) plus slack for probe/refresh latency.
def _safety_margin() -> int:
    return 8 + int(BLOCK_WINDOW_REFRESH_SEC // 12)


def _compute_block_window(host: str, head: int):
    """Probe the node's retained-state band and derive the usable window.

    Returns (window, depth, edge_found): `window` block tags ending at head are
    safe to pin, the node served state `depth` blocks behind head, and
    `edge_found` is True when a retention edge sits inside the requested
    window (pruned node) — in that case `window` excludes a safety margin off
    the edge, since the band slides forward as the node processes blocks.
    """
    requested = max(1, min(RECENT_BLOCK_WINDOW, head))
    depth = _probe_state_depth(host, head, requested)
    edge_found = depth < requested - 1
    if not edge_found:
        return requested, depth, False
    return max(1, depth + 1 - _safety_margin()), depth, True


def _block_window_refresher(environment, host: str):
    """Re-pin RECENT_BLOCKS to the current head every BLOCK_WINDOW_REFRESH_SEC.

    The retained-state band slides forward as the node processes blocks, so a
    window pinned once at test start decays into "No state available" errors
    over the run. If the band shrinks below MIN_PINNED_WINDOW mid-run, fall
    back to the "latest" tag for the remainder.
    """
    global RECENT_BLOCKS, LATEST_HEAD_BLOCK
    while getattr(environment.runner, "state", None) in ("spawning", "running", "ready"):
        time.sleep(BLOCK_WINDOW_REFRESH_SEC)
        try:
            head = _fetch_head(host)
            LATEST_HEAD_BLOCK = head
            LOCUST_CURRENT_BLOCK.set(head)
            window, depth, edge_found = _compute_block_window(host, head)
            if edge_found and window < MIN_PINNED_WINDOW:
                _logger.warning(
                    f"Retained-state band shrank to {depth + 1} blocks mid-run; "
                    "switching to the 'latest' tag for the remainder."
                )
                RECENT_BLOCKS = ["latest"]
                return
            RECENT_BLOCKS = [hex(head - i) for i in range(window)]
        except Exception as e:
            _logger.warning(f"Block window refresh failed (keeping previous window): {e}")


def _fetch_gas_sender(host: str) -> Optional[str]:
    """First unlocked account on the node (eth_accounts), or None."""
    try:
        resp = requests.post(
            host,
            json={"jsonrpc": "2.0", "method": "eth_accounts", "params": [], "id": 1},
            timeout=5,
        )
        accounts = resp.json().get("result") or []
        return accounts[0] if accounts else None
    except Exception as e:
        _logger.warning(f"Failed to fetch accounts from {host}: {e}")
        return None


def filter_gas_tasks(tasks: Dict, gas_enabled: bool) -> Dict:
    """Drop tasks tagged 'gas' (via locust's @tag) unless gas tests are enabled."""
    if gas_enabled:
        return tasks
    return {
        fn: weight for fn, weight in tasks.items()
        if "gas" not in getattr(fn, "locust_tag_set", set())
    }


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
    global RECENT_BLOCKS, GAS_SENDER, _proof_file, _proof_writer
    global INITIAL_HEAD_BLOCK, LATEST_HEAD_BLOCK

    host = environment.host or "http://localhost:8545"

    if GAS_TESTS_ENABLED:
        GAS_SENDER = _fetch_gas_sender(host)
        if GAS_SENDER:
            _logger.info(f"Gas tests enabled; sending transactions from {GAS_SENDER}")
        else:
            _logger.warning(
                "Gas tests are enabled (load_test.gas_tests_enabled) but the node "
                "exposes no unlocked accounts — eth_sendTransaction will no-op. "
                "Point the benchmark at a dev-mode node or disable gas tests."
            )
    try:
        head = _fetch_head(host)
    except Exception as e:
        # Fall back to "latest" so tasks still run; flagged in the log so the
        # final report makes the cache-skew risk obvious.
        _logger.warning(f"Failed to fetch head block from {host}: {e}. Falling back to 'latest'.")
        RECENT_BLOCKS = ["latest"]
    else:
        INITIAL_HEAD_BLOCK = head
        LATEST_HEAD_BLOCK = head
        LOCUST_INITIAL_BLOCK.set(head)
        LOCUST_CURRENT_BLOCK.set(head)
        if not _state_available(host, "latest"):
            # Not a config problem we can route around: the node cannot serve
            # state at all (mid-sync, flat-db not initialized, ...). Abort
            # instead of producing a report that is 100% errors.
            _logger.error(
                f"Node at {host} cannot serve state even at 'latest' "
                "(eth_getBalance failed). It is likely still syncing or its "
                "state database is not initialized — aborting the test."
            )
            environment.process_exit_code = 1
            runner = getattr(environment, "runner", None)
            if runner is not None:
                runner.quit()
            return
        if not _state_available(host, hex(head)):
            # The head fetched a moment ago is already unservable — the node's
            # retained band is shallower than its own processing cadence, so
            # pinned block numbers would go stale instantly.
            _logger.warning(
                f"State at head block {head} is already unavailable by number; "
                "using the 'latest' tag for all requests."
            )
            RECENT_BLOCKS = ["latest"]
        else:
            window, depth, edge_found = _compute_block_window(host, head)
            if edge_found and window < MIN_PINNED_WINDOW:
                _logger.warning(
                    f"Node only has state for the last {depth + 1} blocks — too "
                    f"shallow to pin block numbers (min {MIN_PINNED_WINDOW}); "
                    "using the 'latest' tag for all requests."
                )
                RECENT_BLOCKS = ["latest"]
            else:
                if edge_found:
                    _logger.warning(
                        f"Node only has state for the last {depth + 1} blocks; "
                        f"clamping window to {window}."
                    )
                RECENT_BLOCKS = [hex(head - i) for i in range(window)]
                _logger.info(
                    f"Initialized recent-block window: head={head}, window={window} "
                    f"(refreshing every {BLOCK_WINDOW_REFRESH_SEC:g}s)"
                )
                threading.Thread(
                    target=_block_window_refresher,
                    args=(environment, host),
                    daemon=True,
                ).start()

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
    # Try to refresh the head one last time so the final report reflects the
    # node's state at end-of-run, not the last 30s-old refresher tick.
    host = environment.host or "http://localhost:8545"
    final_head: Optional[int] = LATEST_HEAD_BLOCK
    try:
        final_head = _fetch_head(host)
        LOCUST_CURRENT_BLOCK.set(final_head)
    except Exception as e:
        _logger.warning(f"Failed to fetch final head block from {host}: {e}")
    if INITIAL_HEAD_BLOCK is None:
        _logger.info(
            "Block range for this run: unknown — head fetch failed at test start."
        )
    else:
        end = final_head if final_head is not None else INITIAL_HEAD_BLOCK
        advanced = end - INITIAL_HEAD_BLOCK
        _logger.info(
            f"Block range for this run: start={INITIAL_HEAD_BLOCK} "
            f"end={end} advanced={advanced}"
        )
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

    @tag("gas")
    def eth_send_transaction(self):
        # Submit-only (no receipt wait): we measure RPC submission latency,
        # not mining. The node assigns nonces for its unlocked account.
        if not GAS_SENDER:
            return
        tx = {
            "from": GAS_SENDER,
            "to": random.choice(ADDRESSES),
            "value": "0x1",
            "gas": "0x5208",
        }
        self.rpc_call("eth_sendTransaction", [tx])

    # Weights driven by load_test.task_weights in the YAML config; tasks tagged
    # "gas" are excluded unless load_test.gas_tests_enabled is true.
    tasks = filter_gas_tasks({
        eth_get_balance:      TASK_WEIGHTS.get("eth_getBalance", 3),
        eth_get_storage_at:   TASK_WEIGHTS.get("eth_getStorageAt", 3),
        eth_get_code:         TASK_WEIGHTS.get("eth_getCode", 2),
        eth_call:             TASK_WEIGHTS.get("eth_call", 2),
        eth_get_proof:        TASK_WEIGHTS.get("eth_getProof", 1),
        eth_send_transaction: TASK_WEIGHTS.get("eth_sendTransaction", 1),
    }, GAS_TESTS_ENABLED)
