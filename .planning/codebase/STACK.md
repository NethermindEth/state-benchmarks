# Technology Stack

**Analysis Date:** 2026-03-25

## Languages

**Primary:**
- None currently - This is a research/documentation project in Phase 1 (Preparation)

**Secondary (Referenced/Planned):**
- Go - External tool development (Spamoor, State Actor, go-ethereum)
- C# - Planned Nethermind plugin (`Nethermind.StateComposition`)
- Python - Planned execution-specs test development
- Bash - Future automation scripts
- JSON - Configuration files (genesis files, project metadata)

## Runtime

**Current:**
- No local runtime required (documentation-only repository)

**Future:**
- .NET 9.0 for Nethermind plugin development
- Go toolchain for building external tools (Spamoor, State Actor)
- Python runtime for execution-specs test development

**Package Manager:**
- None currently in use
- Future: NuGet (C# plugin), pip (Python tests)

## Frameworks

**Core:** None in use

**Planned:**
- Nethermind plugin framework (`INethermindPlugin`)
- NUnit for C# testing (Nethermind convention)
- pytest for Python execution-specs tests

## Key Dependencies

**None in repository** - all tooling references point to external repos:

| Tool | Source | Purpose |
|------|--------|---------|
| Spamoor | ethpandaops/spamoor | Transaction generation |
| State Actor | freefox/state-actor | Direct DB state bloating |
| Geth | Local: `/Users/daniilankusin/GolandProjects/go-ethereum` | Reference implementation |
| Nethermind | Local: `/Users/daniilankusin/RiderProjects/nethermind-arbitrum/src/Nethermind` | Plugin target |

## Configuration

- `lab-genesis.json` - Test genesis file for local devnet (chainId: 13337)
- `.gitignore` - Standard ignores for data/payload artifacts
- `.claude/settings.json` - Claude Code configuration

## Platform Requirements

**Development:**
- Git, text editor, access to Geth/Nethermind codebases

**Production (Future):**
- OVH virtual machines (192GB RAM, 4TB NVMe SSD)
- Ethereum EL clients (Geth, Nethermind, Besu, Erigon, Reth)

---

*Stack analysis: 2026-03-25*
