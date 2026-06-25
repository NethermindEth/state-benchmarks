#!/usr/bin/env bash
#
# Bring up the monitoring stack + EL client for this template.
#
# Designed to be **invoked from the repo root**:
#   ./templates/geth/start_infra.sh             # up; idempotent
#   ./templates/geth/start_infra.sh down        # tear down (keep volumes)
#   ./templates/geth/start_infra.sh down -v     # tear down and wipe volumes
#
# Same shape lives in templates/<other-client>/start_infra.sh — copy this
# directory wholesale, drop in the client's docker-compose.<client>-*.yml,
# config_<client>.yml, and .env.<client>; nothing else changes.
#
# What it does:
#   1. derives the client name from its parent directory (templates/<client>)
#   2. picks docker compose v2 if present, else docker-compose v1
#   3. brings up monitoring + the per-client compose with --env-file .env.<client>
#   4. waits for Prometheus, Grafana, and the EL RPC to respond

set -euo pipefail

# Locate this script + the repo root regardless of how it was invoked, then
# work from the repo root so relative paths line up with runner.py later.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLIENT="$(basename "$SCRIPT_DIR")"
cd "$REPO_ROOT"

ENV_FILE="$SCRIPT_DIR/.env.${CLIENT}"
MONITORING_COMPOSE="$SCRIPT_DIR/docker-compose.monitoring.yml"

# Per-client compose file lives next to this script as
# docker-compose.<client>-*.yml (e.g. -bloatnet, -mainnet, -sepolia).
shopt -s nullglob
CLIENT_COMPOSE_CANDIDATES=("$SCRIPT_DIR"/docker-compose."${CLIENT}"-*.yml)
shopt -u nullglob

if (( ${#CLIENT_COMPOSE_CANDIDATES[@]} == 0 )); then
  echo "ERROR: no docker-compose.${CLIENT}-*.yml found in $SCRIPT_DIR." >&2
  exit 1
elif (( ${#CLIENT_COMPOSE_CANDIDATES[@]} > 1 )); then
  echo "ERROR: multiple client compose files in $SCRIPT_DIR — keep just one:" >&2
  printf '  %s\n' "${CLIENT_COMPOSE_CANDIDATES[@]}" >&2
  exit 1
fi
CLIENTS_COMPOSE="${CLIENT_COMPOSE_CANDIDATES[0]}"

for f in "$ENV_FILE" "$MONITORING_COMPOSE" "$CLIENTS_COMPOSE"; do
  if [[ ! -f "$f" ]]; then
    echo "ERROR: $f not found." >&2
    exit 1
  fi
done

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "ERROR: neither 'docker compose' nor 'docker-compose' is available." >&2
  exit 1
fi

COMPOSE_ARGS=(--env-file "$ENV_FILE" -f "$MONITORING_COMPOSE" -f "$CLIENTS_COMPOSE")

case "${1:-up}" in
  down)
    shift || true
    echo "[$CLIENT] tearing down monitoring + client stacks..."
    "${COMPOSE[@]}" "${COMPOSE_ARGS[@]}" down "$@"
    exit 0
    ;;
  up|"")
    ;;
  *)
    echo "Usage: $0 [up|down [-v]]" >&2
    exit 2
    ;;
esac

echo "[$CLIENT] starting monitoring stack + client..."
"${COMPOSE[@]}" "${COMPOSE_ARGS[@]}" up -d

# Load .env so we probe what compose actually published.
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
PROM_PORT="${PROMETHEUS_PORT:-9090}"
RPC_PORT="${CLIENT_RPC_PORT:-8545}"
GRAFANA_PORT="${GRAFANA_PORT:-3000}"

wait_for() {
  local label="$1" url="$2" timeout="${3:-120}" start
  start="$(date +%s)"
  while true; do
    if curl -sSf -o /dev/null --max-time 5 "$url"; then
      echo "  $label OK ($url)"
      return 0
    fi
    if (( $(date +%s) - start > timeout )); then
      echo "  $label NOT READY after ${timeout}s ($url)"
      return 1
    fi
    sleep 2
  done
}

echo
echo "Waiting for services to become reachable..."
wait_for "Prometheus" "http://localhost:${PROM_PORT}/-/healthy"   60 || true
wait_for "Grafana"    "http://localhost:${GRAFANA_PORT}/api/health" 60 || true

echo "Waiting for ${CLIENT} RPC (eth_blockNumber)..."
start_ts="$(date +%s)"
while true; do
  if curl -sSf -X POST -H 'Content-Type: application/json' \
       --max-time 5 \
       --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' \
       "http://localhost:${RPC_PORT}" \
       | grep -q '"result"'; then
    echo "  ${CLIENT} RPC OK (http://localhost:${RPC_PORT})"
    break
  fi
  if (( $(date +%s) - start_ts > 300 )); then
    echo "  ${CLIENT} RPC NOT READY after 300s (docker logs benchmark_${CLIENT})" >&2
    break
  fi
  sleep 3
done

echo
echo "[$CLIENT] infrastructure ready. Useful endpoints:"
echo "  Grafana:        http://localhost:${GRAFANA_PORT}/d/benchmark"
echo "  Prometheus:     http://localhost:${PROM_PORT}"
echo "  ${CLIENT} RPC:  http://localhost:${RPC_PORT}"
echo
echo "Next: ./templates/${CLIENT}/run_benchmark.sh <milestone>"
