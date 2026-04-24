# Subtask 07: Docker package + compose + JWT

**Business Value**: `docker compose up` starts the orchestrator and Nethermind together with a fresh ephemeral JWT — one command brings the whole lab up; shutdown shreds secrets.

**Dependencies**: 05 (CLI must exist to be invoked from the container)

---

## Implementation Plan

### Files to Create

| File | Purpose |
|------|---------|
| `orchestrator/Dockerfile` | Multi-stage build: builder (uv/pip install) → runtime (non-root, slim) |
| `orchestrator/docker-compose.yml` | Wires orchestrator + Nethermind + shared tmpfs JWT volume |
| `orchestrator/scripts/gen-jwt.sh` | Writes fresh 32-byte JWT to tmpfs on compose-up |
| `orchestrator/scripts/shred-jwt.sh` | `shred -u` before `docker compose down` |
| `orchestrator/.dockerignore` | Keep image small (exclude tests, docs, state dirs) |
| `tests/test_docker_smoke.sh` | Smoke test: compose-up, run 5 batches, compose-down, verify artifacts |

### Steps

1. **Dockerfile** — multi-stage:
   - **Stage 1 (`builder`)**: `python:3.12-slim` base; install `uv`; copy `pyproject.toml` + `uv.lock`; `uv sync --no-dev`.
   - **Stage 2 (`runtime`)**: `python:3.12-slim` base; `useradd orchestrator` (non-root); copy `/app/.venv` from builder; copy source under `/app/src`; `USER orchestrator`; `ENTRYPOINT ["orchestrator"]`.
2. **`docker-compose.yml`**:
   - `nethermind` service: image `nethermind:pr-A-dev` (or published tag), `network_mode: bridge`, `read_only: true` with mounted volumes for DB + tmpfs for `/run/jwt`. Command flags per design §9 minus `--StateComposition.RpcAccessLevel=loopback` (not needed on master; verify flag exists), with `--Testing.Enabled=true` kept.
   - `orchestrator` service: builds from `./Dockerfile`, `depends_on: [nethermind]` with `condition: service_healthy`, tmpfs mount of `/run/jwt` shared with `nethermind`, env `NODE_RPC=http://nethermind:8545`, `ENGINE_RPC=http://nethermind:8551`, `JWT_PATH=/run/jwt/jwt.hex`, `STATE_DIR=/app/state`, bind-mount `./state:/app/state`.
   - Nethermind healthcheck: `curl -sf http://localhost:8545 -d '{"jsonrpc":"2.0","method":"eth_syncing","id":1}'` returns 200.
3. **`gen-jwt.sh`**: `openssl rand -hex 32 > /run/jwt/jwt.hex && chmod 600 /run/jwt/jwt.hex`. Runs as a pre-start hook (e.g., via an `init` container or a compose `command:` prefix). Idempotent: no-op if file exists this session.
4. **`shred-jwt.sh`**: `shred -u /run/jwt/jwt.hex 2>/dev/null || rm -f /run/jwt/jwt.hex`. Called during compose-down. Optional because tmpfs disappears on container-down anyway — belt-and-suspenders.
5. **`.dockerignore`**: exclude `tests/`, `docs/`, `.venv/`, `state/`, `*.rlp`, `*.jsonl`, `__pycache__/`, `.git/`.
6. **Smoke test** (`test_docker_smoke.sh`): `docker compose up -d`; wait for healthy; exec into orchestrator container: `orchestrator --state-dir /app/state --target-yaml /app/target.yaml &`; wait 30 s; exec `wc -l /app/state/orchestrator.journal.jsonl` (expect > 0); `docker compose down`; verify JWT file absent on host.

### Patterns & Hints

Image size target: ≤ 200 MB. `python:3.12-slim` is ~120 MB; `orchestrator` package + deps (numpy, httpx, pyyaml, rlp, eth-account) ~60 MB. Stay under.

```dockerfile
# Dockerfile (sketch)
FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

FROM python:3.12-slim AS runtime
RUN useradd --uid 10000 --no-create-home --shell /sbin/nologin orchestrator
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src/ /app/src/
ENV PATH="/app/.venv/bin:$PATH"
USER orchestrator
ENTRYPOINT ["orchestrator"]
```

JWT tmpfs volume sharing (compose-level):

```yaml
volumes:
  jwt:
    driver_opts: { type: tmpfs, device: tmpfs }
```

Mount into both services at `/run/jwt`. Nethermind reads from it; orchestrator reads from it.

**Do not** bake JWT into the image or persist it anywhere. Tmpfs + ephemeral generation per compose-up is the discipline.

---

## Testing

**Test File**: `tests/test_docker_smoke.sh` (bash), `tests/test_dockerfile_build.py` (Python)

| Test Name | Description | Expected |
|-----------|-------------|----------|
| `test_image_builds_under_200mb` | `docker build`; inspect image size | ≤ 200 MB |
| `test_image_runs_as_non_root` | `docker run orchestrator id` | uid 10000, not 0 |
| `test_entrypoint_is_orchestrator` | `docker run orchestrator --help` | Typer help output |
| `test_compose_up_healthy` | `docker compose up -d`; wait for healthy | Both services healthy within 60 s |
| `test_compose_runs_5_batches` | Compose up; exec `orchestrator` with test target; wait 30 s | Journal has ≥ 5 records |
| `test_compose_down_shreds_jwt` | Compose up → down; inspect host | No JWT file remains |
| `test_jwt_not_world_readable` | Compose up; check permissions inside container | `0600` |

**Faking Strategy**: smoke tests require a real Docker daemon. Can run in CI via `docker-in-docker` or on developer machines. Unit tests for the compose YAML itself (using `yaml.safe_load` + schema assertions) don't need Docker.

---

## Acceptance Criteria

- [ ] Multi-stage Dockerfile produces a runtime image ≤ 200 MB
- [ ] Image runs as non-root UID 10000
- [ ] `docker compose up -d` brings orchestrator + Nethermind up with a shared ephemeral JWT
- [ ] Nethermind healthcheck passes before orchestrator starts
- [ ] Orchestrator reads JWT from tmpfs-mounted `/run/jwt/jwt.hex` (permissions `0600`)
- [ ] `docker compose down` removes the JWT (tmpfs-backed, auto-disappears on container stop)
- [ ] Bind-mount `./state` exposes journal + payloads on host for replay/inspection
- [ ] Smoke test runs 5 batches end-to-end and produces journal + payloads files
- [ ] `.dockerignore` keeps state, tests, and caches out of the image
