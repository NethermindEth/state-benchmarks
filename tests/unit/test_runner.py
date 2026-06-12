"""Unit tests for src/orchestrator/runner.py — utility functions."""
import json
import subprocess
import time
from unittest.mock import patch

import pytest

import runner


# ---------- detect_compose_cmd ----------

def test_detect_compose_cmd_prefers_v2():
    """When 'docker compose version' succeeds, return ['docker', 'compose']."""
    def _fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "compose"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")
        raise subprocess.CalledProcessError(1, cmd)

    with patch("runner.subprocess.run", side_effect=_fake_run):
        assert runner.detect_compose_cmd() == ["docker", "compose"]


def test_detect_compose_cmd_falls_back_to_v1():
    """When v2 fails but v1 succeeds, return ['docker-compose']."""
    def _fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "compose"]:
            raise subprocess.CalledProcessError(1, cmd)
        if cmd[:1] == ["docker-compose"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")
        raise FileNotFoundError(cmd[0])

    with patch("runner.subprocess.run", side_effect=_fake_run):
        assert runner.detect_compose_cmd() == ["docker-compose"]


def test_detect_compose_cmd_no_docker(caplog):
    """When both fail, return ['docker-compose'] as last-resort default + log a warning."""
    with patch("runner.subprocess.run", side_effect=FileNotFoundError("no docker")):
        result = runner.detect_compose_cmd()

    assert result == ["docker-compose"]
    assert any("Neither" in r.message for r in caplog.records if r.levelno >= 30)


def test_detect_compose_cmd_probes_through_ssh():
    """With ssh_command configured, the probe must run on the remote host."""
    seen = []

    def _fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    with patch("runner.subprocess.run", side_effect=_fake_run), \
         patch.object(runner, "SSH_CMD", "ssh user@host"):
        runner.detect_compose_cmd()

    assert seen[0][:2] == ["ssh", "user@host"]


# ---------- env overrides ----------

def test_read_env_file_parses_kv_and_skips_noise(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\n\nCONSENSUS=false\nETH_NETWORK = sepolia\nBROKEN_LINE\n")
    assert runner.read_env_file(str(p)) == {"CONSENSUS": "false", "ETH_NETWORK": "sepolia"}


def test_read_env_file_missing_returns_empty(tmp_path):
    assert runner.read_env_file(str(tmp_path / "nope")) == {}


def test_env_flag_precedence(monkeypatch):
    monkeypatch.delenv("CONSENSUS", raising=False)
    # default when absent everywhere
    assert runner.env_flag("CONSENSUS", True, {}) is True
    # .env value overrides the default
    assert runner.env_flag("CONSENSUS", True, {"CONSENSUS": "false"}) is False
    # process environment beats .env
    monkeypatch.setenv("CONSENSUS", "true")
    assert runner.env_flag("CONSENSUS", False, {"CONSENSUS": "false"}) is True


# ---------- sync detection ----------

def _patch_rpc(responses):
    """Patch runner.rpc_call with canned per-method responses (callable or value)."""
    def _fake_rpc(method, params=None, timeout=5):
        value = responses[method]
        return value() if callable(value) else value
    return patch("runner.rpc_call", side_effect=_fake_rpc)


def test_check_sync_status_states():
    with _patch_rpc({"eth_syncing": {"currentBlock": "0x1"}}):
        assert runner.check_sync_status() is True
    with _patch_rpc({"eth_syncing": False}):
        assert runner.check_sync_status() is False
    with _patch_rpc({"eth_syncing": None}):
        assert runner.check_sync_status() is None


def test_head_is_fresh_requires_recent_nonzero_head():
    now = int(time.time())
    fresh = {"number": "0x10", "timestamp": hex(now)}
    stale = {"number": "0x10", "timestamp": hex(now - 100000)}
    genesis = {"number": "0x0", "timestamp": hex(now)}

    with _patch_rpc({"eth_getBlockByNumber": fresh}):
        assert runner.head_is_fresh(freshness_sec=120) is True
    with _patch_rpc({"eth_getBlockByNumber": stale}):
        assert runner.head_is_fresh(freshness_sec=120) is False
    with _patch_rpc({"eth_getBlockByNumber": genesis}):
        assert runner.head_is_fresh(freshness_sec=120) is False
    with _patch_rpc({"eth_getBlockByNumber": None}):
        assert runner.head_is_fresh(freshness_sec=120) is False


def test_measure_sync_time_returns_duration_after_real_sync():
    """syncing -> synced+fresh head (confirmed) yields a positive duration."""
    sync_states = iter([True, True, False, False, False])
    with patch("runner.check_sync_status", side_effect=lambda: next(sync_states)), \
         patch("runner.head_is_fresh", return_value=True), \
         patch("runner.time.sleep"):
        result = runner.measure_sync_time(poll_interval=0, confirmations=3)
    assert result is not None and result >= 0


def test_measure_sync_time_returns_none_when_already_synced(caplog):
    """A node that never reports syncing must not produce a bogus near-zero time."""
    with patch("runner.check_sync_status", return_value=False), \
         patch("runner.head_is_fresh", return_value=True), \
         patch("runner.time.sleep"):
        result = runner.measure_sync_time(poll_interval=0, confirmations=2)
    assert result is None
    assert any("never reported" in r.message for r in caplog.records if r.levelno >= 30)


def test_measure_sync_time_does_not_finish_on_stale_head(caplog):
    """eth_syncing=false with a stale head (no CL driving it) must keep waiting."""
    fresh_states = iter([False, False, False, True, True])
    with patch("runner.check_sync_status", return_value=False), \
         patch("runner.head_is_fresh", side_effect=lambda: next(fresh_states)), \
         patch("runner.time.sleep"):
        result = runner.measure_sync_time(poll_interval=0, confirmations=2)
    # Finished only once the head became fresh, and warned about the stale phase.
    assert result is None  # never saw syncing=True
    assert any("stale" in r.message for r in caplog.records if r.levelno >= 30)


# ---------- measure_db_size ----------

def test_measure_db_size_parses_du_output():
    """du -sb output is '<bytes>\t<path>\n' — parse the first whitespace-separated field."""
    fake_proc = subprocess.CompletedProcess(
        args=["docker", "exec", "...", "du", "-sb", "/data"],
        returncode=0,
        stdout="612000000000\t/data\n",
    )
    runner.DATA_DIRS = {"geth": "/data"}

    with patch("runner.run_cmd", return_value=fake_proc):
        assert runner.measure_db_size("geth") == 612000000000


def test_measure_db_size_unknown_client_returns_none():
    """Unmeasurable sizes are None, never a fabricated 0."""
    runner.DATA_DIRS = {}
    assert runner.measure_db_size("ghost") is None


def test_measure_db_size_handles_docker_failure():
    runner.DATA_DIRS = {"geth": "/data"}
    with patch("runner.run_cmd", side_effect=subprocess.CalledProcessError(1, "docker")):
        assert runner.measure_db_size("geth") is None


def test_measure_db_size_handles_garbage_output():
    """du output without a leading integer (e.g., shell error mixed in) shouldn't crash."""
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="not-a-number\t/data\n",
    )
    runner.DATA_DIRS = {"geth": "/data"}
    with patch("runner.run_cmd", return_value=fake_proc):
        assert runner.measure_db_size("geth") is None


# ---------- export_db_size_metric ----------

def test_export_db_size_metric_writes_textfile_atomically():
    """The gauge goes through run_cmd (remote-safe) with a tmp+mv write."""
    seen = []

    def _fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    with patch("runner.run_cmd", side_effect=_fake_run):
        runner.export_db_size_metric("nethermind", 612000000000)

    assert len(seen) == 1
    assert seen[0][:2] == ["sh", "-c"]
    script = seen[0][2]
    assert 'benchmark_db_size_bytes{client="nethermind"} 612000000000' in script
    assert ".tmp" in script and " && mv " in script


def test_export_db_size_metric_failure_is_non_fatal(caplog):
    with patch("runner.run_cmd", side_effect=subprocess.CalledProcessError(1, "sh")):
        runner.export_db_size_metric("nethermind", 1)  # must not raise
    assert any("DB size metric" in r.message for r in caplog.records if r.levelno >= 30)


# ---------- write_sync_metrics ----------

def test_write_sync_metrics_round_trip(tmp_path):
    runner.write_sync_metrics(str(tmp_path), "geth", sync_time=42.5, db_size=1024)

    p = tmp_path / "sync_metrics_geth.json"
    payload = json.loads(p.read_text())
    assert payload == {"client": "geth", "sync_time_sec": 42.5, "db_size_bytes": 1024}


def test_write_sync_metrics_handles_none_values(tmp_path):
    runner.write_sync_metrics(str(tmp_path), "reth", sync_time=None, db_size=None)
    payload = json.loads((tmp_path / "sync_metrics_reth.json").read_text())
    assert payload["sync_time_sec"] is None
    assert payload["db_size_bytes"] is None


# ---------- load_config (smoke check; we use it heavily but it's trivial) ----------

def test_load_config_reads_yaml(tmp_path):
    p = tmp_path / "config.yml"
    p.write_text("foo: bar\nbaz: [1, 2]\n")
    cfg = runner.load_config(str(p))
    assert cfg == {"foo": "bar", "baz": [1, 2]}
