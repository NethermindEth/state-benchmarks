# Project Structure

**Analysis Date:** 2026-03-25

## Directory Layout

```
state-benchmarks/
├── CLAUDE.md                    # Project context (8KB, comprehensive)
├── AGENTS.md                    # Agent configuration for specialized tasks
├── lab-genesis.json             # Test genesis file (chainId: 13337)
├── .gitignore                   # Ignores data artifacts, IDE files, .omc state
│
├── docs/                        # Research & documentation (primary content)
│   ├── meetings/                # Meeting notes
│   │   └── 2026-03-19-kickoff-sync.md
│   ├── bloatnet-review/         # Bloatnet analysis (numbered series)
│   │   ├── 01-architecture-overview.md
│   │   ├── 02-bloating-methods-analysis.md
│   │   ├── 03-test-case-patterns.md
│   │   ├── 04-findings-and-gaps.md
│   │   └── 05-recommendations.md
│   ├── ef-alignment/            # EF 2026 strategic alignment
│   │   ├── ef-research-2026.md
│   │   ├── ethereum-2026-timeline.md
│   │   └── related-projects.md
│   ├── architecture/            # Design documents
│   │   ├── state-composition-metrics.md    # Plugin spec (most accessed)
│   │   └── implementation-task-plan.md     # Task breakdown
│   ├── research/                # Analysis documents
│   │   └── geth-state-metrics-analysis.md
│   ├── setup/                   # Infrastructure guides
│   │   ├── vm-requirements-by-milestone.md
│   │   ├── hybrid-infrastructure-guide.md
│   │   └── vm-proposal-perf-devnet-3.md
│   └── study-guide-state-bloating-tools.md
│
├── scripts/                     # EMPTY - Future automation scripts
├── data/                        # EMPTY - Future metrics data (.gitkeep only)
├── payloads/                    # EMPTY - Future bloating payloads (.gitkeep only)
├── benchmarks/                  # EMPTY - Future benchmark results
│
├── .claude/                     # Claude Code configuration
│   ├── settings.json
│   ├── settings.local.json
│   └── plan/
│       └── month1-action-plan.md
│
├── .agent/                      # Agent task tracking (GSD)
│   └── tasks/
│       └── 02-state-composition-plugin.todo/
│
├── .planning/                   # GSD planning documents
│   └── codebase/                # THIS codebase map
│
└── .omc/                        # OMC session state (gitignored)
```

## File Organization Patterns

**Documentation:** Numbered prefixes for series (`01-`, `02-`...), hyphens for multi-word names
**Root docs:** UPPERCASE (`CLAUDE.md`, `AGENTS.md`)
**Directories:** Lowercase, hyphenated, plural for collections
**Config:** Lowercase with hyphens (`lab-genesis.json`)

## Key Files by Importance

| File | Lines | Purpose | Access Frequency |
|------|-------|---------|-----------------|
| `docs/architecture/state-composition-metrics.md` | ~350 | Plugin design spec | 11x (highest) |
| `CLAUDE.md` | ~200 | Project context | Every session |
| `docs/architecture/implementation-task-plan.md` | ~356 | Task breakdown | 7x |
| `AGENTS.md` | ~150 | Agent context | Every agent spawn |
| `lab-genesis.json` | ~35 | Test genesis | On demand |

## Content Distribution

- **docs/**: 15 markdown files (~95% of meaningful content)
- **Root**: 3 files (CLAUDE.md, AGENTS.md, lab-genesis.json)
- **scripts/, data/, payloads/, benchmarks/**: Empty placeholders
- **No executable code exists** in this repository

---

*Structure analysis: 2026-03-25*
