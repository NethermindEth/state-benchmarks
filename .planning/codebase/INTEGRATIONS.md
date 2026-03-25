# External Integrations

**Analysis Date:** 2026-03-25

## External Tools

| Tool | Repository | Status | Purpose |
|------|-----------|--------|---------|
| **Spamoor** | ethpandaops/spamoor | External | Transaction spamming for BloatNet |
| **State Actor** | freefox/state-actor | External (alpha) | Direct DB state bloating via RocksDB |
| **execution-specs** | ethereum/execution-specs | External | Stateful test framework (bloatnet tests) |

## Ethereum Clients

| Client | Role | Integration |
|--------|------|-------------|
| **Nethermind** | Primary target | Plugin development, `testing_commitBlockV1` |
| **Geth** | Reference impl | State composition analysis (`SizeStats`) |
| **Besu** | Testing target | Snap sync benchmarking |
| **Erigon** | Testing target | Snap sync benchmarking |
| **Reth** | Testing target | Snap sync benchmarking |

## APIs & Endpoints

**Ethereum JSON-RPC:**
- `eth_getProof(address, slots[], block)` - Proof size measurement
- `testing_commitBlockV1` - Nethermind-specific block commitment

**Planned Plugin RPC:**
- `statecomp_getStats` - State composition metrics
- `statecomp_getTrieStructure` - Trie structure analysis

**Planned Prometheus:**
- `nethermind_statecomp_accounts_total`
- `nethermind_statecomp_storage_slots_total`
- `nethermind_statecomp_account_proof_bytes_p50/p95`

## Infrastructure

| Service | Provider | Purpose |
|---------|----------|---------|
| **Generation VM** | OVH Cloud | State bloating and metrics collection |
| **Snapshot hosting** | ethpandaops.io | perf-devnet-3 baseline (1.31 TB compressed) |
| **Issue tracking** | GitHub | Project management (META issue #1) |

## Data Sources

- **perf-devnet-3 snapshot** - Baseline ~3x mainnet state (~500GB)
- **Mainnet state data** - Reference metrics for distribution validation
- **BloatNet results** - Previous benchmarking data from EF research

## Key External References

- [Bloatnet Website](https://cperezz.github.io/bloatnet-website/)
- [Standardized Ethereum Metrics](https://hackmd.io/dg7rizTyTXuCf2LSa2LsyQ)
- [State Bloating Methods](https://hackmd.io/D12VBHdMSU6y_vcqWcJV_g)

---

*Integrations analysis: 2026-03-25*
