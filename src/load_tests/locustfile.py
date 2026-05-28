import json
import random
import os
import yaml
from locust import FastHttpUser, task, between, events

def load_config(path: str = "config.yml"):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

CONFIG = load_config(os.environ.get("LOCUST_CONFIG", "config.yml"))
ADDRESSES = CONFIG.get("load_test", {}).get("addresses", ["0x0000000000000000000000000000000000000000"])
SLOTS = CONFIG.get("load_test", {}).get("slots", ["0x0"])
LATEST_BLOCK = "latest"

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    pass

class EthereumRPCUser(FastHttpUser):
    wait_time = between(0.1, 0.5)

    def rpc_call(self, method, params, name=None):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1
        }
        name = name or method
        with self.client.post("/", json=payload, name=name, catch_response=True) as response:
            if response.status_code == 200:
                try:
                    resp_json = response.json()
                    if "error" in resp_json:
                        response.failure(f"RPC Error: {resp_json['error']}")
                    else:
                        response.success()
                except json.JSONDecodeError:
                    response.failure("Failed to parse JSON")
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(3)
    def eth_get_balance(self):
        address = random.choice(ADDRESSES)
        self.rpc_call("eth_getBalance", [address, LATEST_BLOCK])

    @task(3)
    def eth_get_storage_at(self):
        address = random.choice(ADDRESSES)
        slot = random.choice(SLOTS)
        self.rpc_call("eth_getStorageAt", [address, slot, LATEST_BLOCK])

    @task(2)
    def eth_get_code(self):
        address = random.choice(ADDRESSES)
        self.rpc_call("eth_getCode", [address, LATEST_BLOCK])

    @task(2)
    def eth_call(self):
        address = random.choice(ADDRESSES)
        self.rpc_call("eth_call", [{"to": address, "data": "0x18160ddd"}, LATEST_BLOCK])

    @task(1)
    def eth_get_proof(self):
        address = random.choice(ADDRESSES)
        slots = [random.choice(SLOTS)]
        self.rpc_call("eth_getProof", [address, slots, LATEST_BLOCK])
