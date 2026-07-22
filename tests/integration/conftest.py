"""Fixtures that bring up docker/docker-compose.test.yml and seed the chain.

These fixtures are session-scoped — the stack survives across all integration
tests in a single pytest invocation.
"""
import os
import pathlib
import re
import shutil
import subprocess
import time

import pytest
import requests

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
COMPOSE_FILE = REPO_ROOT / "docker" / "docker-compose.test.yml"
SEEDER = REPO_ROOT / "scripts" / "seed_test_node.py"
RPC_URL = "http://localhost:8545"
PROM_URL = "http://localhost:9090"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "compose", "version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _wait_for_rpc(timeout: int):
    payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.post(RPC_URL, json=payload, timeout=2)
            if r.status_code == 200 and int(r.json()["result"], 16) > 0:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"RPC did not produce a block within {timeout}s")


def _wait_for_prom(timeout: int):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{PROM_URL}/-/ready", timeout=2)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"Prometheus not ready within {timeout}s")


@pytest.fixture(scope="session")
def docker_stack():
    if not _docker_available():
        pytest.skip("Docker not available; skipping integration tests.")

    # Defensive teardown of any leftover stack from a prior interrupted run.
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
        cwd=str(REPO_ROOT), capture_output=True,
    )

    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"],
        cwd=str(REPO_ROOT), check=True,
    )
    try:
        _wait_for_rpc(timeout=60)
        _wait_for_prom(timeout=30)
        yield
    finally:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
            cwd=str(REPO_ROOT), check=False,
        )


@pytest.fixture(scope="session")
def seeded_chain(docker_stack):
    """Run the seeder; return the deployed contract address."""
    result = subprocess.run(
        ["uv", "run", "python", str(SEEDER)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=True,
    )
    output = result.stdout + result.stderr
    m = re.search(r"Contract:\s+(0x[0-9a-fA-F]{40})", output)
    if not m:
        raise RuntimeError(f"Could not parse contract address from seeder output:\n{output}")
    return m.group(1)
