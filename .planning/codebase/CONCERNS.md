# Codebase Concerns

**Analysis Date:** 2026-03-25

## Critical Issues

### 1. Phase 1 Stalled - No Production Code
- **Issue:** Project is 2 months into a 6-month grant (Feb-July 2026) with zero production code. All planned directories (`scripts/`, `data/`, `benchmarks/`, `payloads/`) are empty.
- **Impact:** Cannot collect metrics, cannot bloat state, cannot benchmark. All Phase 2-4 work blocked.
- **Action:** Begin Nethermind plugin implementation immediately (Phase 1.1 of task plan).

### 2. Tooling Decision Not Made
- **Issue:** Two competing paths (State Actor vs Engine API) identified at March 19 kickoff. Evaluation deadline passed.
- **Impact:** State bloating cannot begin until tooling is chosen.
- **Action:** Dmitro to complete State Actor evaluation. Team decision meeting needed.

### 3. No CI/CD or Testing Infrastructure
- **Issue:** No GitHub Actions, no test framework, no linting, no code quality gates.
- **Impact:** When code is written, it will be unvalidated. Wrong benchmarks = invalid research.
- **Action:** Add CI pipeline before first code PR.

## Missing Infrastructure

| Component | Status | Priority |
|-----------|--------|----------|
| CI/CD pipeline | Missing | CRITICAL |
| Test framework | Missing | HIGH |
| Build system (Makefile/Docker) | Missing | HIGH |
| Linting/formatting config | Missing | MEDIUM |
| Package manifests (.csproj) | Missing | HIGH |
| README.md | Missing | MEDIUM |
| LICENSE | Missing | MEDIUM |
| `.env.example` | Missing | LOW |

## Scalability Risks

**650GB State Threshold:** Known performance cliff from Bloatnet research. Project targets 10x mainnet (~1.6TB state) = 2.5x beyond the breaking point. VM specs upgraded to 192GB RAM, but software-level risk remains HIGH.

**Snap Sync at Scale:** No existing data on snap sync success for states >1TB. Client failures at 7x-10x are probable.

**Storage Headroom:** 10x target needs ~2.46TB. Generation VM has 4TB disk. Only 1.5TB headroom for working space.

## Security Concerns

- `lab-genesis.json` contains well-known Hardhat test account keys (LOW risk - test network only)
- No secrets management documentation (no `.env.example`)
- External snapshot dependency (ethpandaops.io) with no backup or checksum verification

## Dependency Risks

- **State Actor:** ~2 weeks old at kickoff, alpha quality, Nethermind support only in PRs
- **Nethermind `testing_commitBlockV1`:** Non-standard endpoint, version-dependent
- **`ITreeVisitor` API:** Stable since 2022 but no version pinning documented
- **Local client repos:** Referenced by absolute paths on one developer's machine only

## Documentation Gaps

- No `README.md` for external visibility (only `CLAUDE.md` which is internal)
- No `LICENSE` file (EF grant requires open source)
- No `CONTRIBUTING.md` for external contributors
- No setup quickstart guide (VM requirements exist, but no step-by-step walkthrough)
- No mainnet baseline data committed to `data/` yet

## Team Risks

- **Marcin Sobczak** (project lead) is "currently off" with no return date
- No interim lead formally assigned
- Tooling evaluation assigned to Dmitro - status unclear

## Immediate Actions Needed

1. **Daniel:** Ship Nethermind plugin skeleton this week (Phase 1.1)
2. **Dmitro:** Complete State Actor evaluation and provision generation VM
3. **Team:** Tooling decision meeting (State Actor vs Engine API)
4. **Daniel:** Commit initial mainnet metrics data to `data/`
5. Add `README.md` and `LICENSE` to repo

---

*Concerns audit: 2026-03-25*
