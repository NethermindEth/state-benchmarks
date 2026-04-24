# Subtask 02: Facade dispatch (12 verbs over EELS scenarios)

**Business Value**: Controller can request a byte-budgeted batch of transactions for any of 12 scenarios by name, without knowing anything about the underlying EELS test modules.

**Dependencies**: None (external dep on EELS port being installable)

---

## Implementation Plan

### Files to Create

| File | Purpose |
|------|---------|
| `src/orchestrator/facade/__init__.py` | Registry + `dispatch(verb, deadline_bytes, context)` entry |
| `src/orchestrator/facade/context.py` | `FacadeContext` dataclass (cursors, genesis_sha256, chain_id, gas_limit) |
| `src/orchestrator/facade/verbs/<verb>.py` | One thin adapter per scenario — 12 files total |
| `tests/test_facade.py` | Per-verb unit tests asserting well-formed signed txs |

### Steps

1. Define `FacadeContext`: `base_address: bytes`, `revision: int`, `address_cursor: int`, `salt_cursor: int`, `genesis_sha256: bytes`, `chain_id: int`, `gas_limit: int`, `deploy_private_key: bytes`.
2. Define `dispatch(verb: str, deadline_bytes: int, context: FacadeContext) → list[SignedTransaction]`. Looks up the verb in a registry and calls the adapter.
3. For each of the 12 scenarios (`eoatx`, `calltx`, `deploytx`, `factorydeploytx`, `storagespam`, `erc20_bloater`, `erc20tx`, `uniswap_swaps`, `storagerefundtx`, `gasburnertx`, `blob_combined`, `evm_fuzz`), write an adapter module:
   - Imports the EELS test module (`from execution_specs_tests.benchmark.spamoor.test_eoatx import build_txs` or equivalent).
   - Wraps it into the common `(deadline_bytes, context) → list[SignedTx]` signature.
   - Advances the relevant cursor in `context` (addresses for `eoatx`, salts for CREATE2, etc.) so the next call picks up where this left off.
4. The adapter stops at the `deadline_bytes` budget: accumulates tx sizes (RLP length + overhead), stops when the next tx would exceed the deadline. Returns the accumulated list.
5. Registry: `VERBS: dict[str, Callable[[int, FacadeContext], list[SignedTx]]] = { "eoatx": eoatx_adapter, ... }`.

### Patterns & Hints

EELS scenarios live at `NethermindEth/execution-specs feat/spamoor-to-est` under `tests/benchmark/spamoor/test_*.py`. They build txs via helper functions (see `helpers.py` in that branch). Each test has a slightly different signature — the adapter's job is to smooth that over.

```python
# src/orchestrator/facade/verbs/eoatx.py
from execution_specs_tests.benchmark.spamoor.helpers import build_eoatx_transactions

def adapter(deadline_bytes: int, ctx: FacadeContext) -> list[SignedTransaction]:
    txs = []
    size = 0
    while size < deadline_bytes:
        tx = build_eoatx_transactions(
            nonce=ctx.address_cursor,   # or deploy_nonce
            to=ctx.base_address + ctx.address_cursor.to_bytes(20, "big"),
            gas_limit=21_000,
            chain_id=ctx.chain_id,
            private_key=ctx.deploy_private_key,
        )
        tx_size = len(tx.rlp)
        if size + tx_size > deadline_bytes and txs:
            break
        txs.append(tx)
        size += tx_size
        ctx.address_cursor += 1
    return txs
```

Pin the EELS dep in `pyproject.toml` by branch or commit SHA for development; tag for releases.

---

## Testing

**Test File**: `tests/test_facade.py`

| Test Name | Description | Expected |
|-----------|-------------|----------|
| `test_registry_has_all_12_verbs` | `VERBS.keys()` matches the design's facade table | Exactly 12 entries |
| `test_eoatx_respects_deadline_bytes` | Call with `deadline_bytes=50_000`; sum of `len(tx.rlp)` | ≤ 50 000 |
| `test_eoatx_advances_address_cursor` | Call twice; compare cursor before/after | Monotonically increases |
| `test_deploytx_produces_valid_signed_deploy` | Decode the first tx; verify it's a contract deployment | `to is None`, `input` non-empty |
| `test_storagerefundtx_uses_SSTORE_to_zero` | Disassemble tx data; look for SSTORE of 0 | Present |
| `test_unknown_verb_raises` | `dispatch("no_such_verb", ...)` | `KeyError` |

**Faking Strategy**: most tests can run against the real EELS port (it's pure Python, no chain needed). For the "produces valid signed tx" tests, decode with `eth_account` or similar. For deadline-respect, compute RLP size of returned txs.

---

## Acceptance Criteria

- [ ] All 12 verbs from the design's facade table are registered
- [ ] `dispatch(verb, deadline_bytes, ctx)` returns a list of signed transactions whose cumulative RLP size is ≤ `deadline_bytes`
- [ ] Each adapter advances the appropriate cursor in `ctx` (sequential addresses, salts, etc.)
- [ ] `FacadeContext` is passed by reference (cursors mutated in place) — or returned alongside the tx list
- [ ] Unknown verb raises a clear error
- [ ] EELS dep is pinned in `pyproject.toml`
- [ ] Each verb has a smoke test asserting the first returned tx is well-formed
