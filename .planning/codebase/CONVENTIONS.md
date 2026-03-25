# Coding Conventions

**Analysis Date:** 2026-03-25

## Project Status

**Stage:** Early preparation phase (Phase 1) - documentation only, no production code yet

## File Naming Patterns

**Documentation:**
- Lowercase with hyphens: `state-composition-metrics.md`, `vm-requirements-by-milestone.md`
- Numbered prefixes for series: `01-architecture-overview.md`, `02-bloating-methods-analysis.md`
- UPPERCASE for root docs: `CLAUDE.md`, `AGENTS.md`

**Directories:**
- Lowercase, hyphenated: `bloatnet-review/`, `ef-alignment/`
- Plural for collections: `scripts/`, `docs/`, `benchmarks/`, `payloads/`

**JSON:** Lowercase with hyphens: `lab-genesis.json`

## Markdown Conventions

- H1 once per document, H2 for major sections, H3 for subsections
- Language tags on all code blocks (`python`, `csharp`, `bash`, `json`)
- Tables with header rows and pipe separators
- Backticks for file paths and code references
- Unordered lists: `- ` (hyphen + space)
- Checkboxes: `- [ ]` / `- [x]` for task tracking
- Bold for emphasis: `**CRITICAL**`, `**NEW**`, `**DONE**`

## Naming Patterns

**Metrics:** snake_case with consistent suffixes: `accounts_total`, `storage_slots_total`, `account_proof_bytes_p50`

**Ethereum Terms:**
- PascalCase for types: `StateRoot`, `Hash256`, `BlockHeader`
- camelCase for fields: `storageRoot`, `codeHash`
- UPPERCASE for opcodes: `SLOAD`, `SSTORE`, `EXTCODESIZE`

**Multipliers:** number + 'x': `1.5x`, `2x`, `3x`, `5x`, `7x`, `10x`

## Git Conventions

**Commit format:** `<type>: <description>` (feat, fix, refactor, docs, test, chore, perf, ci)
**Attribution:** Disabled globally

**Gitignored:**
- `data/*.json`, `payloads/*.json`, `benchmarks/*.csv` (except `.gitkeep`)
- `.omc/state/`, `.omc/notepad.md`
- `.DS_Store`, `.idea/`, `.vscode/`
- `node_modules/`, `__pycache__/`, `.venv/`

## JSON Formatting

- 2-space indentation
- Hex values prefixed with `0x`
- Large numbers in hex: `"0x1c9c380"` for gas limits

## Planned C# Conventions (Nethermind Plugin)

- Namespace: `Nethermind.StateComposition`
- Plugin: `StateCompositionPlugin : INethermindPlugin`
- RPC namespace: `statecomp_*`
- DI: Autofac `Module` registration
- Nullable: enabled, warnings as errors

## Planned Python Conventions (Tests)

- Framework: pytest with execution-specs format
- Naming: `test_*.py` files, `test_*()` functions
- Parametrization: `@pytest.mark.parametrize`

## Issue Tracking

- GitHub issues with `#<number>` references in docs
- META issue #1 contains full dependency flowchart
- Task status: `**DONE**`, `**in progress**`, `blocked on #N`

## Acronyms

| Acronym | Meaning |
|---------|---------|
| EL | Execution Layer |
| EF | Ethereum Foundation |
| ESP | Ecosystem Support Program |
| RPC | Remote Procedure Call |
| MPT | Merkle Patricia Trie |
| DB | Database |

---

*Convention analysis: 2026-03-25*
