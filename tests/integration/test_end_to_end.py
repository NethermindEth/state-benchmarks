"""End-to-end integration test against the docker-compose.test.yml stack.

Mark this file as `integration`; it's skipped by default. Run with:
    uv run pytest -m integration
"""
import json
import pathlib
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
TEMPLATE_CONFIG = REPO_ROOT / "test-config.yml"
RUNNER = REPO_ROOT / "src" / "orchestrator" / "runner.py"

pytestmark = pytest.mark.integration


def _render_config(tmp_path: pathlib.Path, contract_addr: str) -> pathlib.Path:
    """Take test-config.yml, swap manage:false, ensure the contract address is included."""
    cfg = yaml.safe_load(TEMPLATE_CONFIG.read_text())
    cfg["infrastructure"]["manage"] = False
    # Make sure the seeded contract is in the address pool; deduplicate.
    addrs = set(a.lower() for a in cfg["load_test"]["addresses"])
    if contract_addr.lower() not in addrs:
        cfg["load_test"]["addresses"].append(contract_addr)
    # Shorten the locust run so the test is fast.
    cfg["load_test"]["run_time"] = "15s"
    cfg["load_test"]["users"] = 8

    out = tmp_path / "rendered-config.yml"
    out.write_text(yaml.safe_dump(cfg))
    return out


def _run_orchestrator(config_path: pathlib.Path, milestone: str):
    return subprocess.run(
        ["uv", "run", "python", str(RUNNER),
         "--config", str(config_path),
         "--milestone", milestone,
         "--skip-sync"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=True,
    )


def _milestone_dir(milestone: str) -> pathlib.Path:
    return REPO_ROOT / "benchmarks" / milestone


def test_orchestrator_end_to_end_produces_real_metrics(seeded_chain, tmp_path):
    milestone = "pytest-integration"
    out_dir = _milestone_dir(milestone)
    if out_dir.exists():
        shutil.rmtree(out_dir)

    try:
        config_path = _render_config(tmp_path, seeded_chain)
        _run_orchestrator(config_path, milestone)

        json_path = out_dir / "metrics_test-client.json"
        assert json_path.exists(), f"Expected output JSON at {json_path}"
        payload = json.loads(json_path.read_text())

        # Identity + schema
        assert payload["client"] == "test-client"
        assert payload["milestone"] == milestone
        assert payload["timestamp"]

        # Locust ran cleanly across all 5 RPC methods.
        agg = payload["rpc"]["aggregate"]
        assert agg["request_count"] > 0
        assert agg["failure_count"] == 0
        per_method = payload["rpc"]["per_method"]
        expected_methods = {"eth_getBalance", "eth_getStorageAt", "eth_call", "eth_getCode", "eth_getProof"}
        assert expected_methods.issubset(per_method.keys()), \
            f"Missing RPC methods: {expected_methods - per_method.keys()}"

        # Geth client metrics produced real percentiles.
        metrics = payload["metrics"]
        assert metrics["block_proc_p95"] > 0
        assert metrics["rpc_p95_latency_ms"] > 0
        assert metrics["rpc_requests_sec"] > 0

        # eth_getProof was actually exercised and proof bytes were captured.
        assert payload["proof_sizes"]["sample_count"] > 0
        assert metrics["proof_p50_bytes"] > 0
        assert per_method["eth_getProof"]["avg_content_bytes"] > 0

        # CSV summary matches the spec schema (columns + at least the proof row).
        csv_path = out_dir / "metrics_test-client.csv"
        assert csv_path.exists()
        csv_text = csv_path.read_text()
        assert csv_text.startswith("client,state_size,metric,p50,p95,p99,mean")
        assert "eth_getProof:response_bytes" in csv_text

        # Per-client locust output landed in the milestone subdir, not the top-level.
        assert (out_dir / "test-client" / "locust_stats_stats.csv").exists()
        assert (out_dir / "test-client" / "proof_sizes.csv").exists()
    finally:
        if out_dir.exists():
            shutil.rmtree(out_dir)


def test_regression_comparator_runs_against_previous(seeded_chain, tmp_path):
    """Run the orchestrator twice; the second invocation should log a comparison line."""
    m_prev = "pytest-integration-prev"
    m_curr = "pytest-integration-curr"

    for m in (m_prev, m_curr):
        d = _milestone_dir(m)
        if d.exists():
            shutil.rmtree(d)

    try:
        config_path = _render_config(tmp_path, seeded_chain)
        _run_orchestrator(config_path, m_prev)
        result = _run_orchestrator(config_path, m_curr)

        # The aggregator logs the previous milestone it picked.
        combined = (result.stdout or "") + (result.stderr or "")
        assert f"Comparing against previous milestone: {m_prev}" in combined, \
            f"Comparator didn't pick up previous milestone. Output:\n{combined[-2000:]}"
    finally:
        for m in (m_prev, m_curr):
            d = _milestone_dir(m)
            if d.exists():
                shutil.rmtree(d)
