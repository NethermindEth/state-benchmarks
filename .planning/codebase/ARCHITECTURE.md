# Architecture

**Analysis Date:** 2026-03-25

## Pattern Overview

**Overall:** Research and Benchmarking Platform

**Key Characteristics:**
- Documentation-driven research project with planned implementation phases
- Multi-phase workflow: preparation → tooling development → state generation → benchmarking
- External tooling evaluation (State Actor, Spamoor) with contribution-based approach
- Client-specific integration focused on Nethermind with cross-client comparison

## Layers

**Research & Documentation Layer:**
- Purpose: Knowledge capture, analysis, and planning
- Location: `docs/`
- Contains: Bloatnet analysis, EF alignment research, architecture plans, meeting notes
- Depends on: External sources (Bloatnet, EF roadmap documents, client codebases)
- Used by: Implementation planning, decision-making processes

**Planning & Configuration Layer:**
- Purpose: Project management, task tracking, and configuration
- Location: `.claude/`, `.agent/`, `lab-genesis.json`
- Contains: Action plans, agent configurations, genesis files, GSD task hierarchies
- Depends on: Research documentation
- Used by: Implementation execution, progress tracking

**Data Collection Layer:**
- Purpose: Metrics storage and analysis artifacts
- Location: `data/`, `benchmarks/`, `payloads/`
- Contains: State composition metrics, benchmark results, transaction payloads (planned)
- Depends on: State generation tooling (not yet implemented)
- Used by: Analysis and reporting

**Tooling Layer (Planned):**
- Purpose: State generation, metrics collection, benchmarking automation
- Location: `scripts/` (currently empty, planned implementation)
- Contains: Bloating scripts, transaction generators, data collectors, benchmark runners
- Depends on: External tools (State Actor, Nethermind APIs), Nethermind plugin architecture
- Used by: State generation workflow, data collection

**Plugin Layer (In Development):**
- Purpose: Nethermind-specific state composition metrics
- Location: Planned `nethermind-plugin/` (referenced in architecture docs)
- Contains: StateCompositionPlugin, RPC modules, Prometheus metrics, incremental tracking
- Depends on: Nethermind APIs (`ITreeVisitor`, `INodeStorage`, `IStateReader`)
- Used by: Metrics collection, state distribution analysis

## Data Flow

**Research-to-Implementation Flow:**

1. External research review (Bloatnet, EF priorities, Geth implementation)
2. Knowledge synthesis → architecture documents (`docs/architecture/`)
3. Task planning → implementation plans (`docs/architecture/implementation-task-plan.md`)
4. Execution tracking → agent task files (`.agent/tasks/`)
5. Results → data artifacts (`data/`, `benchmarks/`)

**State Composition Metrics Flow (Planned):**

1. Nethermind node runs with StateComposition plugin
2. Initial parallel scan via `ITreeVisitor` → baseline `StateCompositionStats`
3. Per-block incremental updates via `IBlockTree.NewHeadBlock` events
4. Metrics exposed via:
   - `statecomp_*` RPC namespace (JSON-RPC)
   - Prometheus gauges (`nethermind_statecomp_*`)
5. External collection scripts query RPC/Prometheus
6. Metrics stored in `data/` for analysis

**State Generation Flow (Planned):**

1. **Path A (State Actor)**: Direct DB writes to RocksDB, bypassing Engine API
2. **Path B (Engine API)**: `testing_commitBlockV1` endpoint with transaction payloads
3. Tooling decision pending evaluation (end of Week 3, per kickoff notes)
4. Generated states → checkpoint VMs for snap sync testing
5. Snap sync results → `benchmarks/` directory

**State Management:**
- Baseline: perf-devnet-3 snapshot (~3x mainnet, ~500GB state)
- Incremental bloating: 3x → 5x → 7x → 10x multipliers
- Each milestone: generation → metrics collection → snap sync testing → benchmarking

## Key Abstractions

**State Multiplier:**
- Purpose: Standardized scaling factor relative to mainnet state size
- Examples: `1.5x`, `2x`, `3x`, `5x`, `7x`, `10x`
- Pattern: Sequential milestones with increasing state size
- Critical threshold: `650GB` (performance degradation point from Bloatnet research)

**State Composition Metrics:**
- Purpose: Quantify state structure (accounts, contracts, storage, trie nodes)
- Examples: `StateCompositionStats` (architecture plan), Geth SizeStats (reference)
- Pattern: Parallel initial scan + incremental per-block updates
- Implementation: Nethermind plugin with `ITreeVisitor` traversal

**Bloating Methods:**
- Purpose: Gas-efficient techniques for controlled state growth
- Examples: 24kB contracts (202 gas/byte), EOA funding (267 gas/byte), SSTORE (625 gas/byte)
- Pattern: Ranked by gas efficiency, categorized by target (code, accounts, storage)
- Source: `docs/bloatnet-review/02-bloating-methods-analysis.md`

**Proof-Based Witness Metrics:**
- Purpose: Client-agnostic measurement via Merkle proofs
- Examples: `eth_getProof` RPC, account/storage proof sizes (p50/p95)
- Pattern: Statistical sampling of addresses/slots
- Relevance: Verkle tree transition, statelessness roadmap validation

## Entry Points

**Project Documentation:**
- Location: `CLAUDE.md`
- Triggers: Initial context loading for all agents/Claude instances
- Responsibilities: Project overview, team roles, workflow phases, external references

**Agent Configuration:**
- Location: `AGENTS.md`
- Triggers: Agent spawn for specialized tasks
- Responsibilities: EF alignment context, technical constraints, domain knowledge, file conventions

**Genesis Configuration:**
- Location: `lab-genesis.json`
- Triggers: Local testnet initialization
- Responsibilities: Chain config (chainId: 13337), fork activation blocks, initial account allocations

**Architecture Plans:**
- Location: `docs/architecture/state-composition-metrics.md`
- Triggers: Nethermind plugin implementation
- Responsibilities: Plugin design, data structures, implementation order, risk assessment

**Implementation Tasks:**
- Location: `docs/architecture/implementation-task-plan.md`
- Triggers: Phase-based execution
- Responsibilities: Task breakdown, dependency graph, parallelization opportunities

## Error Handling

**Strategy:** Documentation-first with planned implementation rigor

**Patterns:**
- **External tool evaluation**: State Actor + Spamoor analysis before building custom tooling
- **Incremental validation**: Per-milestone checkpoints with snap sync testing
- **Reference implementations**: Geth code analysis for Nethermind equivalency
- **Risk mitigation**: Documented in architecture plans (Section 13 of `docs/architecture/state-composition-metrics.md`)

## Cross-Cutting Concerns

**Logging:** Planned via Nethermind plugin progress logging (every 1M accounts during traversal)

**Validation:**
- State distribution validation against mainnet patterns
- Metrics verification via Prometheus + RPC comparison
- Cross-client snap sync success/failure tracking

**Authentication:**
- Not applicable (research project, no external auth required)
- Local RPC endpoints for metrics collection

**EF 2026 Alignment:**
- Scale Track: Gas limit research (100M+ benchmarks), witness size data (`eth_getProof` metrics)
- Harden L1 Track: Testing infrastructure, client interoperability (snap sync across 5 EL clients)
- Fork alignment: Glamsterdam (H1 2026) gas limit data, Hegotá (H2 2026) Verkle baseline

**Reproducibility:**
- Deterministic state generation from perf-devnet-3 baseline
- Documented tooling paths (State Actor vs Engine API)
- Versioned snapshots at each multiplier checkpoint

**Client Coverage:**
- Primary: Nethermind (plugin development, metrics collection)
- Comparison: Geth (reference implementation), Besu, Erigon, Reth
- Testing scope: Snap sync resilience at all state size milestones

---

*Architecture analysis: 2026-03-25*
