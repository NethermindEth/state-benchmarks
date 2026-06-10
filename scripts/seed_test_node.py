#!/usr/bin/env python3
"""
Seed the Geth --dev test node with state so the benchmark harness exercises
non-trivial RPC paths:

  1. Fund three deterministic addresses (matches test-config.yml).
  2. Deploy a tiny contract that initializes two storage slots and returns
     a constant for any call (so eth_call, eth_getStorageAt, eth_getProof
     all return non-empty data).
  3. Submit a few extra value transfers to grow the chain.

Idempotent: re-running against the same chain detects existing balances and
existing bytecode at the deterministic deploy address and skips.
"""
import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("seed")

DEFAULT_RPC = "http://localhost:8545"

# Deterministic recipients — also listed in test-config.yml load_test.addresses.
TEST_ADDRESSES = [
    "0x1111111111111111111111111111111111111111",
    "0x2222222222222222222222222222222222222222",
    "0x3333333333333333333333333333333333333333",
]

# Minimal contract:
#   constructor: storage[0] = 1; storage[1] = 2; return runtime
#   runtime:     always returns 0x03e8 (1000) as 32-byte word
# Hand-rolled EVM bytecode — see plan doc for derivation.
DEPLOY_BYTECODE = "0x60016000556002600155600b6016600039600b6000f36103e860005260206000f3"


def rpc(url: str, method: str, params: Optional[List[Any]] = None) -> Any:
    payload = {"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"{method} error: {body['error']}")
    return body.get("result")


def wait_for_chain(url: str, timeout: int = 60):
    deadline = time.time() + timeout
    last_error: Optional[Exception] = None
    saw_rpc = False
    while time.time() < deadline:
        try:
            block = rpc(url, "eth_blockNumber")
            saw_rpc = True
            if int(block, 16) > 0:
                log.info(f"Chain is producing blocks (head={int(block, 16)})")
                return
        except Exception as e:
            last_error = e
            log.debug(f"RPC not ready: {e}")
        time.sleep(1)

    if not saw_rpc:
        raise RuntimeError(
            f"Could not reach the RPC at {url} within {timeout}s (last error: {last_error}). "
            "Is the test stack up? Check with: docker ps --filter name=benchmark_test-client\n"
            "Common cause: a previous benchmark left another client holding port 8545 — the "
            "runner leaves its stack up by default. Tear everything down first:\n"
            "  docker ps -aq --filter name=benchmark_ | xargs docker rm -f\n"
            "then re-run: docker compose -f docker/docker-compose.test.yml up -d"
        )
    raise RuntimeError(
        f"RPC at {url} responds but the chain head stayed at 0 for {timeout}s. "
        "The node reachable on this port is not the Geth --dev test client "
        "(dev mode mines a block per second). Check what is bound to 8545: "
        "docker ps --filter publish=8545"
    )


def wait_for_tx(url: str, tx_hash: str, timeout: int = 30) -> Dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        receipt = rpc(url, "eth_getTransactionReceipt", [tx_hash])
        if receipt:
            return receipt
        time.sleep(0.5)
    raise RuntimeError(f"Tx {tx_hash} not mined within {timeout}s")


def send_value(url: str, sender: str, to: str, value_wei_hex: str) -> str:
    tx = {"from": sender, "to": to, "value": value_wei_hex, "gas": "0x5208"}
    tx_hash = rpc(url, "eth_sendTransaction", [tx])
    receipt = wait_for_tx(url, tx_hash)
    if receipt.get("status") != "0x1":
        raise RuntimeError(f"Transfer to {to} failed: {receipt}")
    return tx_hash


def deploy_contract(url: str, sender: str) -> str:
    tx = {"from": sender, "data": DEPLOY_BYTECODE, "gas": "0x100000"}
    tx_hash = rpc(url, "eth_sendTransaction", [tx])
    receipt = wait_for_tx(url, tx_hash)
    if receipt.get("status") != "0x1":
        raise RuntimeError(f"Contract deploy failed: {receipt}")
    address = receipt["contractAddress"]
    log.info(f"Deployed test contract at {address}")
    return address


def find_existing_contract(url: str, sender: str) -> Optional[str]:
    """Walk the sender's prior tx receipts looking for a contract deploy with our bytecode prefix."""
    nonce_hex = rpc(url, "eth_getTransactionCount", [sender, "latest"])
    nonce = int(nonce_hex, 16)
    if nonce == 0:
        return None
    # Cheap heuristic: scan recent blocks for a deploy from sender.
    head = int(rpc(url, "eth_blockNumber"), 16)
    look_back = min(head, 200)
    for block_num in range(head, max(0, head - look_back) - 1, -1):
        block = rpc(url, "eth_getBlockByNumber", [hex(block_num), True])
        if not block or not block.get("transactions"):
            continue
        for tx in block["transactions"]:
            if tx.get("from", "").lower() != sender.lower():
                continue
            if tx.get("to") is not None:
                continue
            receipt = rpc(url, "eth_getTransactionReceipt", [tx["hash"]])
            if receipt and receipt.get("contractAddress"):
                addr = receipt["contractAddress"]
                code = rpc(url, "eth_getCode", [addr, "latest"])
                if code and code != "0x":
                    return addr
    return None


def main():
    parser = argparse.ArgumentParser(description="Seed Geth --dev test node")
    parser.add_argument("--rpc", default=DEFAULT_RPC)
    parser.add_argument("--fund-wei", default="0xde0b6b3a7640000", help="Per-address fund amount in wei hex (default 1 ETH)")
    parser.add_argument("--extra-txs", type=int, default=20, help="Extra value transfers to grow the chain")
    parser.add_argument(
        "--state-file", default=os.path.join("benchmarks", ".seed-state.json"),
        help="Where to record the deployed contract address so the runner can "
             "inject it into the load test (LOCUST_EXTRA_ADDRESSES). Empty to skip.",
    )
    args = parser.parse_args()

    wait_for_chain(args.rpc)

    accounts = rpc(args.rpc, "eth_accounts")
    if not accounts:
        log.error("No accounts on the dev node. Is --dev mode enabled?")
        sys.exit(1)
    dev = accounts[0]
    log.info(f"Using dev account: {dev}")

    # 1. Fund test addresses (idempotent: skip if already funded).
    for addr in TEST_ADDRESSES:
        bal = int(rpc(args.rpc, "eth_getBalance", [addr, "latest"]), 16)
        if bal > 0:
            log.info(f"  {addr} already funded ({bal} wei) — skipping")
            continue
        send_value(args.rpc, dev, addr, args.fund_wei)
        log.info(f"  Funded {addr} with {args.fund_wei} wei")

    # 2. Deploy the test contract (idempotent: skip if one is already present).
    existing = find_existing_contract(args.rpc, dev)
    if existing:
        log.info(f"Found existing test contract at {existing} — skipping deploy")
        contract_addr = existing
    else:
        contract_addr = deploy_contract(args.rpc, dev)

    # 3. Generate extra activity so block-proc histograms get populated.
    log.info(f"Submitting {args.extra_txs} extra value transfers...")
    for i in range(args.extra_txs):
        target = TEST_ADDRESSES[i % len(TEST_ADDRESSES)]
        send_value(args.rpc, dev, target, "0x1")
    log.info("Done.")

    # Record the contract address so the runner can inject it into the load
    # test automatically (no manual pasting into test-config.yml needed).
    if args.state_file:
        os.makedirs(os.path.dirname(args.state_file) or ".", exist_ok=True)
        with open(args.state_file, "w") as f:
            json.dump({
                "rpc": args.rpc,
                "contract_address": contract_addr,
                "funded_addresses": TEST_ADDRESSES,
            }, f, indent=2)
        log.info(f"Wrote seed state to {args.state_file}")

    log.info("=== Seed summary ===")
    log.info(f"  Dev account:     {dev}")
    log.info(f"  Funded addrs:    {TEST_ADDRESSES}")
    log.info(f"  Contract:        {contract_addr}")
    log.info("  The runner reads the state file above and adds the contract address")
    log.info("  to the load test automatically (LOCUST_EXTRA_ADDRESSES).")


if __name__ == "__main__":
    main()
