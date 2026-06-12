"""Unit tests for src/load_tests/locustfile.py — gas-task gating."""
from unittest.mock import MagicMock, patch

import requests
from locust import tag

import locustfile


# ---------- filter_gas_tasks ----------

def _untagged_task(self):
    pass


@tag("gas")
def _gas_task(self):
    pass


def test_filter_gas_tasks_drops_gas_tagged_when_disabled():
    tasks = {_untagged_task: 3, _gas_task: 1}
    assert locustfile.filter_gas_tasks(tasks, gas_enabled=False) == {_untagged_task: 3}


def test_filter_gas_tasks_keeps_all_when_enabled():
    tasks = {_untagged_task: 3, _gas_task: 1}
    assert locustfile.filter_gas_tasks(tasks, gas_enabled=True) == tasks


def test_eth_send_transaction_is_gas_tagged():
    assert "gas" in locustfile.EthereumRPCUser.eth_send_transaction.locust_tag_set


def test_default_config_excludes_gas_task():
    """Guard: the repo-root config.yml (mainnet) must keep gas tests disabled,
    so the gas task never enters the task set under the default config."""
    assert locustfile.GAS_TESTS_ENABLED is False
    assert locustfile.EthereumRPCUser.eth_send_transaction not in locustfile.EthereumRPCUser.tasks


# ---------- _fetch_gas_sender ----------

def _response_with(result):
    resp = MagicMock()
    resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": result}
    return resp


def test_fetch_gas_sender_returns_first_account():
    with patch("locustfile.requests.post", return_value=_response_with(["0xaaa", "0xbbb"])):
        assert locustfile._fetch_gas_sender("http://node:8545") == "0xaaa"


def test_fetch_gas_sender_no_accounts():
    with patch("locustfile.requests.post", return_value=_response_with([])):
        assert locustfile._fetch_gas_sender("http://node:8545") is None


def test_fetch_gas_sender_null_result():
    with patch("locustfile.requests.post", return_value=_response_with(None)):
        assert locustfile._fetch_gas_sender("http://node:8545") is None


def test_fetch_gas_sender_connection_error():
    with patch("locustfile.requests.post", side_effect=requests.exceptions.ConnectionError("boom")):
        assert locustfile._fetch_gas_sender("http://node:8545") is None


# ---------- state-depth probing ----------

def _fake_node(head: int, state_depth: int):
    """requests.post side effect for a node with state for the last
    `state_depth` blocks (head - state_depth + 1 .. head)."""
    def post(host, json=None, timeout=None):
        resp = MagicMock()
        if json["method"] == "eth_blockNumber":
            resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": hex(head)}
        elif json["method"] == "eth_getBalance":
            block = int(json["params"][1], 16)
            if head - block < state_depth:
                resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": "0x0"}
            else:
                resp.json.return_value = {
                    "jsonrpc": "2.0", "id": 1,
                    "error": {"code": -32002, "message": f"No state available for block {block}"},
                }
        else:
            resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": None}
        return resp
    return post


def test_state_available_true_on_result():
    with patch("locustfile.requests.post", side_effect=_fake_node(head=100, state_depth=100)):
        assert locustfile._state_available("http://node:8545", hex(50)) is True


def test_state_available_false_on_rpc_error():
    with patch("locustfile.requests.post", side_effect=_fake_node(head=100, state_depth=10)):
        assert locustfile._state_available("http://node:8545", hex(50)) is False


def test_state_available_false_on_connection_error():
    with patch("locustfile.requests.post", side_effect=requests.exceptions.ConnectionError("boom")):
        assert locustfile._state_available("http://node:8545", "0x1") is False


def test_probe_state_depth_archive_node_single_probe():
    """Full-depth state: the first probe succeeds and no bisect runs."""
    with patch("locustfile.requests.post", side_effect=_fake_node(head=10_000, state_depth=10_000)) as post:
        assert locustfile._probe_state_depth("http://node:8545", 10_000, 1000) == 999
    assert post.call_count == 1


def test_probe_state_depth_finds_retention_edge():
    """State only for the last 128 blocks: bisect lands on depth 127."""
    with patch("locustfile.requests.post", side_effect=_fake_node(head=10_000, state_depth=128)):
        assert locustfile._probe_state_depth("http://node:8545", 10_000, 1000) == 127


def test_probe_state_depth_short_chain():
    """Window larger than the chain: capped at head, no probe past genesis."""
    with patch("locustfile.requests.post", side_effect=_fake_node(head=5, state_depth=100)):
        assert locustfile._probe_state_depth("http://node:8545", 5, 1000) == 5


def test_on_test_start_clamps_window_on_pruned_node():
    env = MagicMock()
    env.host = "http://node:8545"
    env.parsed_options.run_time = "5m"
    with patch("locustfile.requests.post", side_effect=_fake_node(head=10_000, state_depth=128)), \
         patch.object(locustfile, "GAS_TESTS_ENABLED", False), \
         patch.object(locustfile, "PROOF_SIZES_CSV", None):
        locustfile.on_test_start(env)
    # depth 127 -> band of 128, minus margin (5m/12s + 8 = 33) -> 95 blocks
    assert len(locustfile.RECENT_BLOCKS) == 95
    assert locustfile.RECENT_BLOCKS[0] == hex(10_000)
    assert locustfile.RECENT_BLOCKS[-1] == hex(10_000 - 94)


def test_on_test_start_keeps_full_window_on_archive_node():
    env = MagicMock()
    env.host = "http://node:8545"
    env.parsed_options.run_time = "5m"
    with patch("locustfile.requests.post", side_effect=_fake_node(head=10_000, state_depth=10_000)), \
         patch.object(locustfile, "GAS_TESTS_ENABLED", False), \
         patch.object(locustfile, "PROOF_SIZES_CSV", None):
        locustfile.on_test_start(env)
    assert len(locustfile.RECENT_BLOCKS) == locustfile.RECENT_BLOCK_WINDOW


# ---------- eth_send_transaction task ----------

class _DummyUser:
    """Stands in for EthereumRPCUser; records rpc_call invocations."""

    def __init__(self):
        self.calls = []

    def rpc_call(self, method, params, name=None):
        self.calls.append((method, params))


def test_eth_send_transaction_noops_without_sender():
    user = _DummyUser()
    with patch.object(locustfile, "GAS_SENDER", None):
        locustfile.EthereumRPCUser.eth_send_transaction(user)
    assert user.calls == []


def test_eth_send_transaction_sends_value_transfer():
    user = _DummyUser()
    with patch.object(locustfile, "GAS_SENDER", "0xdev"):
        locustfile.EthereumRPCUser.eth_send_transaction(user)

    assert len(user.calls) == 1
    method, params = user.calls[0]
    assert method == "eth_sendTransaction"
    tx = params[0]
    assert tx["from"] == "0xdev"
    assert tx["to"] in locustfile.ADDRESSES
    assert tx["value"] == "0x1"
    assert tx["gas"] == "0x5208"
