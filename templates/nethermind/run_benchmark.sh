#!/usr/bin/env bash
#
# Run the benchmark for this template against the infrastructure brought up
# by ./templates/<client>/start_infra.sh.
#
# Designed to be **invoked from the repo root**:
#   ./templates/geth/run_benchmark.sh                       # milestone "<client>-default"
#   ./templates/geth/run_benchmark.sh v1.0.0
#   ./templates/geth/run_benchmark.sh v1.0.0 --fail-on-regression
#
# The templated config_<client>.yml has infrastructure.manage: false, so
# runner.py skips docker entirely: it just runs --skip-sync (DB size +
# Locust + aggregator). Extra args after the milestone pass through.
#
# Optional: drive the snapshot's head forward via mock-CL replay so locust
# hammers RPC against an advancing head (populates block_proc and gas/sec
# metrics in addition to the pure-RPC numbers). Set MOCK_CL_PAYLOADS in
# .env.<client> (or drop a JSONL at templates/<client>/payloads/<milestone>.jsonl)
# and the replay launches in the background alongside locust; it's a no-op
# warning when the payloads file is missing. Cancun+ replay needs CL-recorded
# payloads (eth_getBlockByNumber-only recordings won't work — see src/mock_cl).
#
# Same shape lives in templates/<other-client>/run_benchmark.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLIENT="$(basename "$SCRIPT_DIR")"
cd "$REPO_ROOT"

CONFIG="$SCRIPT_DIR/config_${CLIENT}.yml"
ENV_FILE="$SCRIPT_DIR/.env.${CLIENT}"
MILESTONE="${1:-${CLIENT}-default}"
if (( $# > 0 )); then shift; fi

for f in "$CONFIG" "$ENV_FILE"; do
  if [[ ! -f "$f" ]]; then
    echo "ERROR: $f not found." >&2
    exit 1
  fi
done

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
PROM_PORT="${PROMETHEUS_PORT:-9090}"
RPC_PORT="${CLIENT_RPC_PORT:-8545}"
ENGINE_PORT="${CLIENT_ENGINE_PORT:-8551}"

# Mock-CL replay knobs (all overridable via .env.<client>):
#   MOCK_CL_PAYLOADS   JSONL file. Defaults to templates/<client>/payloads/<milestone>.jsonl.
#   MOCK_CL_JWT        Secret path or 0x-hex. Defaults to NETHERMIND_JWT_PATH, falling back
#                      to the repo-tracked docker/jwtsecret (the same file the compose mounts
#                      into the nethermind container at /jwt).
#   MOCK_CL_ENGINE_URL Engine API URL. Defaults to http://localhost:${ENGINE_PORT}.
#   MOCK_CL_LATENCY_CSV Per-block latency CSV. Defaults to benchmarks/<milestone>/replay_latency.csv.
#   MOCK_CL_ENGINE_TIMEOUT Per-request Engine-API timeout (s). Defaults to 60 — heavy x3.5
#                      blocks can take >10s to process; a 10s default killed the replay
#                      on a single slow newPayload in past long runs.
DEFAULT_PAYLOADS="$SCRIPT_DIR/payloads/${MILESTONE}.jsonl"
DEFAULT_JWT="${NETHERMIND_JWT_PATH:-docker/jwtsecret}"
MOCK_CL_PAYLOADS="${MOCK_CL_PAYLOADS:-$DEFAULT_PAYLOADS}"
MOCK_CL_JWT="${MOCK_CL_JWT:-$DEFAULT_JWT}"
MOCK_CL_ENGINE_URL="${MOCK_CL_ENGINE_URL:-http://localhost:${ENGINE_PORT}}"
MOCK_CL_LATENCY_CSV="${MOCK_CL_LATENCY_CSV:-benchmarks/${MILESTONE}/replay_latency.csv}"
MOCK_CL_ENGINE_TIMEOUT="${MOCK_CL_ENGINE_TIMEOUT:-60}"

echo "[$CLIENT] preflight..."

if ! curl -sSf -o /dev/null --max-time 5 "http://localhost:${PROM_PORT}/-/healthy"; then
  echo "ERROR: Prometheus not reachable at http://localhost:${PROM_PORT}." >&2
  echo "       Run ./templates/${CLIENT}/start_infra.sh first." >&2
  exit 1
fi
echo "  Prometheus OK (:${PROM_PORT})"

if ! curl -sSf -X POST -H 'Content-Type: application/json' \
     --max-time 5 \
     --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' \
     "http://localhost:${RPC_PORT}" \
     | grep -q '"result"'; then
  echo "ERROR: ${CLIENT} RPC not reachable at http://localhost:${RPC_PORT}." >&2
  echo "       Run ./templates/${CLIENT}/start_infra.sh first." >&2
  exit 1
fi
echo "  ${CLIENT} RPC OK (:${RPC_PORT})"

# Catch the silent-drop failure mode that bit the original geth-35 run: if
# cAdvisor isn't enriching containers with name="benchmark_<client>", the
# aggregator produces a JSON with no resource metrics.
if ! curl -sSf --max-time 5 \
       "http://localhost:${PROM_PORT}/api/v1/query?query=container_memory_rss%7Bname%3D%22benchmark_${CLIENT}%22%7D" \
     | grep -q '"result":\[{'; then
  echo "WARN: Prometheus has no container_memory_rss{name=\"benchmark_${CLIENT}\"} series."
  echo "      cAdvisor likely cannot enrich container labels — see the README"
  echo "      note on the containerd-snapshotter / rootless Docker workaround."
  echo "      The benchmark will still run, but resource metrics will be empty."
fi

echo
echo "[$CLIENT] running benchmark: milestone=${MILESTONE}"
echo "  config:    ${CONFIG#$REPO_ROOT/}"
echo "  extra:     $*"

# Background mock-CL replay (head advance under locust load). Skipped silently-ish
# when payloads are missing — locust still runs against a static head as before.
REPLAY_PID=
REPLAY_LOG=
cleanup_replay() {
  if [[ -n "${REPLAY_PID:-}" ]] && kill -0 "$REPLAY_PID" 2>/dev/null; then
    echo "[$CLIENT] stopping mock-CL replay (pid=$REPLAY_PID)..."
    kill -TERM "$REPLAY_PID" 2>/dev/null || true
    # `wait` only works on direct children; tolerate the not-a-child case from
    # SIGINT racing the process exit.
    wait "$REPLAY_PID" 2>/dev/null || true
  fi
}
trap cleanup_replay EXIT INT TERM

if [[ -f "$MOCK_CL_PAYLOADS" ]]; then
  if [[ ! -f "$MOCK_CL_JWT" ]] && [[ ! "$MOCK_CL_JWT" =~ ^0x ]]; then
    echo "WARN: mock-CL JWT not found at '$MOCK_CL_JWT' and not a 0x-hex secret."
    echo "      Replay will likely fail engine API auth — set MOCK_CL_JWT in .env.${CLIENT}."
  fi
  mkdir -p "benchmarks/${MILESTONE}"
  REPLAY_LOG="benchmarks/${MILESTONE}/mock_cl_replay.log"
  echo
  echo "[$CLIENT] launching mock-CL replay in background:"
  echo "  payloads:    ${MOCK_CL_PAYLOADS#$REPO_ROOT/}"
  echo "  engine_url:  $MOCK_CL_ENGINE_URL"
  echo "  jwt:         $MOCK_CL_JWT"
  echo "  latency_csv: ${MOCK_CL_LATENCY_CSV#$REPO_ROOT/}"
  echo "  timeout:     ${MOCK_CL_ENGINE_TIMEOUT}s"
  echo "  log:         ${REPLAY_LOG#$REPO_ROOT/}"
  PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}" uv run python -m mock_cl \
    --engine-url "$MOCK_CL_ENGINE_URL" \
    --jwt "$MOCK_CL_JWT" \
    --engine-timeout "$MOCK_CL_ENGINE_TIMEOUT" \
    replay \
    --payloads "$MOCK_CL_PAYLOADS" \
    --latency-csv "$MOCK_CL_LATENCY_CSV" \
    >"$REPLAY_LOG" 2>&1 &
  REPLAY_PID=$!
  # Sanity: bail early if the child died immediately (bad JWT, malformed JSONL, etc.).
  sleep 1
  if ! kill -0 "$REPLAY_PID" 2>/dev/null; then
    echo "ERROR: mock-CL replay died on startup. Tail of $REPLAY_LOG:" >&2
    tail -20 "$REPLAY_LOG" >&2 || true
    REPLAY_PID=
    exit 1
  fi
  echo "  pid=$REPLAY_PID — running concurrently with locust."
else
  echo
  echo "[$CLIENT] mock-CL replay SKIPPED: no payloads at"
  echo "  $MOCK_CL_PAYLOADS"
  echo "  Drop a CL-recorded JSONL there (or set MOCK_CL_PAYLOADS in .env.${CLIENT})"
  echo "  to advance head during the run. Locust will measure against a static head."
fi
echo

uv run python src/orchestrator/runner.py \
  --config "$CONFIG" \
  --client "$CLIENT" \
  --milestone "$MILESTONE" \
  --skip-sync \
  "$@"

OUT="benchmarks/${MILESTONE}/metrics_${CLIENT}.json"
echo
echo "[$CLIENT] done. Output: $OUT"
if [[ -f "$OUT" ]]; then
  if grep -q '"cpu_percent_peak"' "$OUT"; then
    echo "  ✓ cAdvisor metrics present in $(basename "$OUT")"
  else
    echo "  ✗ cAdvisor metrics MISSING — check Prometheus has 'benchmark_${CLIENT}' series:"
    echo "    curl 'http://localhost:${PROM_PORT}/api/v1/query?query=up{job=\"cadvisor\"}'"
  fi
fi

if [[ -n "${REPLAY_PID:-}" ]]; then
  if [[ -f "$MOCK_CL_LATENCY_CSV" ]]; then
    # Subtract 1 for the header row.
    REPLAY_ROWS=$(($(wc -l < "$MOCK_CL_LATENCY_CSV") - 1))
    echo "  ✓ mock-CL replay drove $REPLAY_ROWS blocks (${MOCK_CL_LATENCY_CSV#$REPO_ROOT/})"
  else
    echo "  ✗ mock-CL replay produced no latency CSV — see ${REPLAY_LOG#$REPO_ROOT/}"
  fi
fi
