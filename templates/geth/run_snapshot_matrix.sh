#!/usr/bin/env bash
#
# Sweep a list of snapshots, running the overlay benchmark at one or more
# durations against each. Ties the existing pieces together end-to-end:
#
#   for each snapshot URL:
#     stop node + overlay  ->  fetch_snapshot.sh into a per-block dir  ->
#     repoint DB path + overlay.lowerdir + mock-CL payloads  ->
#     for each duration: down/up (pristine overlay) -> wait RPC -> run_benchmark.sh
#     tear down, free the snapshot dir, move on.
#
# Designed to be **invoked from the repo root** and **launched detached** on the
# benchmark VM so it survives disconnect (the whole matrix is many hours):
#
#   nohup setsid bash -c 'export PATH=$HOME/.local/bin:$PATH; \
#     ./templates/nethermind/run_snapshot_matrix.sh templates/nethermind/snapshots.txt; \
#     echo MATRIX-DONE-rc=$?' > matrix_driver.log 2>&1 &
#
# Same shape lives in templates/<other-client>/run_snapshot_matrix.sh — the
# client is derived from the parent directory, so the file is identical.
#
# It REUSES (never reimplements):
#   scripts/fetch_snapshot.sh              download+extract (detached snap_dl, wipes target)
#   templates/<client>/start_infra.sh      down/up = restore pristine overlay + boot node
#   templates/<client>/run_benchmark.sh    mock-CL replay + Locust + aggregator
#
# Manifest (arg 1, default templates/<client>/snapshots.txt): one snapshot per
# line, "#" comments allowed. Each line:
#     <snapshot_url> [payloads_file]
# The block is taken from the URL (ethpandaops layout .../<client>/<block>/snapshot.tar.zst).
# Payloads are PRE-STAGED per block: if the file is missing (or its first block
# is not head+1) the snapshot is skipped loudly.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLIENT="$(basename "$SCRIPT_DIR")"
cd "$REPO_ROOT"

# uv / node tooling typically lives in ~/.local/bin, not on PATH in non-login
# shells (SSH/Ansible). The sub-scripts need it.
export PATH="$HOME/.local/bin:$PATH"

# ---- per-client defaults (dir alias, dir suffix, DB subdir, DB bind env var) --
# Snapshot dir naming: "<alias>-<label>-<block>-benchmark" for both clients
# (unified convention across clients).
#   DEFAULT_SUBDIR       = where the DB lives INSIDE the snapshot dir (the value
#                          set as overlay.lowerdir / <client>_DB_PATH).
#   DEFAULT_FETCH_SUBDIR = subdir the snapshot tar is EXTRACTED into.
# Nethermind: tar ships mainnet/, so extract at root and DB = <dir>/mainnet.
# Geth: the ethpandaops tar ships the geth-datadir CONTENTS flat (chaindata,
# triedb, nodes, ...), but geth --datadir=/data needs them under /data/geth/ —
# so extract into <dir>/geth and use <dir> itself as the datadir (DB_SUBDIR="").
case "$CLIENT" in
  nethermind) DEFAULT_ALIAS=neth; DEFAULT_SUFFIX=-benchmark; DEFAULT_SUBDIR=mainnet; DEFAULT_FETCH_SUBDIR=""; DEFAULT_DBENV=NETHERMIND_DB_PATH ;;
  geth)       DEFAULT_ALIAS=geth; DEFAULT_SUFFIX=-benchmark; DEFAULT_SUBDIR="";      DEFAULT_FETCH_SUBDIR=geth; DEFAULT_DBENV=GETH_DB_PATH ;;
  *)          DEFAULT_ALIAS="$CLIENT"; DEFAULT_SUFFIX=-benchmark; DEFAULT_SUBDIR=""; DEFAULT_FETCH_SUBDIR=""; DEFAULT_DBENV="" ;;
esac

# ---- config knobs (all env-overridable) -------------------------------------
DURATIONS="${DURATIONS:-5m 60m}"                 # space-separated Locust run_times
SNAP_BASE="${SNAP_BASE:-/mnt/bigdata}"           # where snapshot dirs live
SNAP_LABEL="${SNAP_LABEL:-x35}"                  # bloat label in dir/milestone names
SNAP_ALIAS="${SNAP_ALIAS:-$DEFAULT_ALIAS}"       # short client alias for dir/milestone
SNAP_SUFFIX="${SNAP_SUFFIX-$DEFAULT_SUFFIX}"     # dir suffix ("" = none); note '-' not ':-'
DB_SUBDIR="${DB_SUBDIR-$DEFAULT_SUBDIR}"         # DB dir inside the extract ("" = root); note '-' not ':-'
FETCH_SUBDIR="${FETCH_SUBDIR-$DEFAULT_FETCH_SUBDIR}"  # subdir the tar is extracted into ("" = target root)
DB_PATH_ENV="${DB_PATH_ENV:-$DEFAULT_DBENV}"     # compose bind var to repoint
PAYLOADS_PATTERN="${PAYLOADS_PATTERN:-payloads_${SNAP_LABEL}_<block>.jsonl}"  # <block> substituted
KEEP_SNAPSHOTS="${KEEP_SNAPSHOTS:-0}"            # 1 = keep extracts (needs disk for all); 0 = rm after runs
SKIP_FETCH="${SKIP_FETCH:-0}"                    # 1 = reuse an already-extracted target dir (no re-download)
KEEP_INFRA="${KEEP_INFRA:-1}"                    # 1 = leave node+monitoring UP after the run (Grafana stays live)
SNAP_HEAD="${SNAP_HEAD:-}"                        # override the snapshot head (else auto-detected from snapshot metadata / URL block)
RPC_READY_TIMEOUT="${RPC_READY_TIMEOUT:-1800}"   # cap the ~10-15 min FlatDb-reprocess wait (s)
FETCH_POLL_TIMEOUT="${FETCH_POLL_TIMEOUT:-86400}" # cap the snapshot download wait (s)

MANIFEST="${1:-$SCRIPT_DIR/snapshots.txt}"
CONFIG="$SCRIPT_DIR/config_${CLIENT}.yml"
ENV_FILE="$SCRIPT_DIR/.env.${CLIENT}"
INFRA="$SCRIPT_DIR/start_infra.sh"
RUNNER="$SCRIPT_DIR/run_benchmark.sh"

for f in "$MANIFEST" "$CONFIG" "$ENV_FILE" "$INFRA" "$RUNNER" "scripts/fetch_snapshot.sh"; do
  [[ -f "$f" ]] || { echo "ERROR: required file not found: $f" >&2; exit 1; }
done
[[ -n "$DB_PATH_ENV" ]] || { echo "ERROR: DB_PATH_ENV unset and no default for client '$CLIENT' — set DB_PATH_ENV." >&2; exit 1; }

SUMMARY=()   # "milestone<TAB>status" lines, printed at the end

# Set KEY=VALUE in an env file (replace in place, else append).
set_kv() {
  local key="$1" val="$2" file="$3"
  if grep -qE "^${key}=" "$file"; then
    sed -i -E "s|^${key}=.*|${key}=${val}|" "$file"
  else
    printf '%s=%s\n' "$key" "$val" >> "$file"
  fi
  grep -E "^${key}=" "$file"
}

# Replace a quoted YAML scalar (lowerdir / run_time) in place.
set_yaml_quoted() {
  local key="$1" val="$2" file="$3"
  sed -i -E "s|(^[[:space:]]*${key}:[[:space:]]*\").*(\")|\1${val}\2|" "$file"
}

wait_for_rpc() {
  local port="$1" start now
  start="$(date +%s)"
  while true; do
    if curl -sSf -X POST -H 'Content-Type: application/json' --max-time 5 \
         --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' \
         "http://localhost:${port}" | grep -q '"result"'; then
      return 0
    fi
    now="$(date +%s)"
    (( now - start > RPC_READY_TIMEOUT )) && return 1
    sleep 20
  done
}

# Force-remove any leftover benchmark containers by NAME so `start_infra up`'s
# `docker compose up` never dies on "container name already in use" when a
# foreign-owned stack (e.g. a manually-started monitoring stack) holds the name.
# Removes CONTAINERS only — the named prometheus_data/grafana_data volumes (and
# the snapshot on disk) are untouched, so metrics history is always kept.
clean_stale_containers() {
  docker rm -f benchmark_prometheus benchmark_grafana benchmark_cadvisor \
    benchmark_node_exporter "benchmark_${CLIENT}" >/dev/null 2>&1 || true
}

# Process one snapshot fully. Called under `|| true`, so set -e is suspended for
# the whole body — `return 1` just skips to the next snapshot.
run_one_snapshot() {
  local url="$1" payloads="${2:-}"
  local block target db_dir dur milestone port line first_expected out status

  block="$(basename "$(dirname "$url")")"
  if ! [[ "$block" =~ ^[0-9]+$ ]]; then
    echo "ERROR: could not derive a numeric block from URL: $url (got '$block') — skipping." >&2
    SUMMARY+=("$(printf '%s\t%s' "$url" "SKIP:bad-url")")
    return 1
  fi
  target="$SNAP_BASE/$SNAP_ALIAS-$SNAP_LABEL-$block$SNAP_SUFFIX"

  echo
  echo "=== snapshot $block ==="
  echo "  url:    $url"
  echo "  target: $target"

  # 1. Stop node + drop overlay so the old lowerdir is free to replace.
  echo "--- stopping infra (frees old snapshot dir) ---"
  "$INFRA" down || true

  # 1b/2. Fetch the snapshot — unless SKIP_FETCH=1 and the target already holds an
  # extracted snapshot (reuse a prior/in-flight download, e.g. after a mid-run fix).
  if [[ "${SKIP_FETCH:-0}" == "1" && -d "$target" && -n "$(ls -A "$target" 2>/dev/null)" ]]; then
    echo "--- SKIP_FETCH=1: reusing existing snapshot at $target ---"
  else
    # 1b. Remove the currently-configured snapshot dir if it differs from the new
    # target (moving to a new block / old naming). Same-dir case is left to
    # fetch_snapshot.sh, which wipes the target itself. Strictly guarded: must
    # live under SNAP_BASE, be non-empty, and not be SNAP_BASE or the target.
    local prev_lower prev_root
    prev_lower="$(grep -E '^[[:space:]]*lowerdir:' "$CONFIG" | head -1 | sed -E 's|^[^"]*"([^"]*)".*|\1|')"
    prev_root="$prev_lower"
    [[ -n "$DB_SUBDIR" && "$prev_root" == */"$DB_SUBDIR" ]] && prev_root="${prev_root%/"$DB_SUBDIR"}"
    if [[ -n "$prev_root" && "$prev_root" == "$SNAP_BASE"/* && "$prev_root" != "$SNAP_BASE" && "$prev_root" != "$target" ]]; then
      echo "  removing previous snapshot dir: $prev_root"
      rm -rf "$prev_root"
    fi

    # 2. Fetch (detached snap_dl; wipes target itself), then poll to completion.
    # Geth's tar ships datadir contents flat, so extract into <target>/geth
    # (FETCH_SUBDIR) — geth --datadir then finds them under /data/geth/.
    local fetch_target="$target${FETCH_SUBDIR:+/$FETCH_SUBDIR}"
    echo "--- fetching snapshot into $fetch_target ---"
    scripts/fetch_snapshot.sh "$url" "$fetch_target"
    local fstart fnow
    fstart="$(date +%s)"
    while true; do
      line="$(docker inspect snap_dl --format '{{.State.Status}} {{.State.ExitCode}}' 2>/dev/null || echo 'missing 1')"
      [[ "${line%% *}" == "exited" ]] && break
      fnow="$(date +%s)"
      if (( fnow - fstart > FETCH_POLL_TIMEOUT )); then
        echo "ERROR: fetch for block $block did not finish within ${FETCH_POLL_TIMEOUT}s — skipping." >&2
        SUMMARY+=("$(printf '%s\t%s' "$block" "SKIP:fetch-timeout")")
        return 1
      fi
      sleep 15
    done
    if [[ "${line##* }" != "0" ]]; then
      echo "ERROR: snap_dl exited with code '${line##* }' for block $block — skipping. See: docker logs snap_dl" >&2
      SUMMARY+=("$(printf '%s\t%s' "$block" "SKIP:fetch-failed")")
      return 1
    fi
  fi

  # 3. Resolve the DB dir inside the extract.
  if [[ -n "$DB_SUBDIR" && -d "$target/$DB_SUBDIR" ]]; then
    db_dir="$target/$DB_SUBDIR"
  elif [[ -d "$target/mainnet" ]]; then
    db_dir="$target/mainnet"
  else
    db_dir="$target"
  fi
  echo "--- pointing templates at DB dir: $db_dir ---"
  set_kv "$DB_PATH_ENV" "$db_dir" "$ENV_FILE"
  set_yaml_quoted "lowerdir" "$db_dir" "$CONFIG"
  grep -nE '^[[:space:]]*lowerdir:' "$CONFIG" || true

  # 4. Payloads: pre-staged per block; must start at head+1.
  if [[ -z "$payloads" ]]; then
    payloads="${PAYLOADS_PATTERN//<block>/$block}"
  fi
  if [[ ! -f "$payloads" ]]; then
    echo "ERROR: payloads not found for block $block: '$payloads' — pre-stage it (first block must be $((block+1))). Skipping." >&2
    SUMMARY+=("$(printf '%s\t%s' "$block" "SKIP:no-payloads")")
    return 1
  fi
  # Determine the snapshot's REAL head: SNAP_HEAD override > the snapshot's own
  # _snapshot_eth_getBlockByNumber.json > the URL block label. The labeled block
  # can differ from the real head (e.g. the EF 3.5x/5x snapshots ship a head
  # thousands of blocks below their name), so validate payloads against the REAL
  # head, not the label.
  local snap_head metaf
  snap_head="${SNAP_HEAD:-}"
  if [[ -z "$snap_head" ]]; then
    metaf="$(find "$target" -maxdepth 3 -name '_snapshot_eth_getBlockByNumber.json' 2>/dev/null | head -1)"
    [[ -n "$metaf" ]] && snap_head="$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["result"]["number"],16))' "$metaf" 2>/dev/null || true)"
  fi
  snap_head="${snap_head:-$block}"
  first_expected=$((snap_head + 1))
  echo "  snapshot head: $snap_head (payloads must start at $first_expected)"
  if ! python3 -c 'import json,sys
d=json.loads(open(sys.argv[1]).readline())
sys.exit(0 if int(d["payload"]["blockNumber"],16)==int(sys.argv[2]) else 3)' "$payloads" "$first_expected"; then
    echo "ERROR: payloads '$payloads' first block != $first_expected (head+1) — skipping block $block." >&2
    SUMMARY+=("$(printf '%s\t%s' "$block" "SKIP:payloads-not-head+1")")
    return 1
  fi
  set_kv "MOCK_CL_PAYLOADS" "$payloads" "$ENV_FILE"

  # Geth's engine-API JWT is a mounted volume (docker-compose.geth-bloatnet.yml:
  # /jwt <- GETH_JWT_PATH, default docker/jwtsecret) — NOT taken from the snapshot,
  # so the snapshot stays pristine and boots even if it ships no jwt.hex. Point
  # mock-CL at the SAME host file so the replay authenticates. Nethermind mounts
  # its own /jwt likewise; run_benchmark's default already matches there.
  if [[ "$CLIENT" == "geth" ]]; then
    set_kv "MOCK_CL_JWT" "${GETH_JWT_PATH:-docker/jwtsecret}" "$ENV_FILE"
  fi

  # RPC port to poll (published by compose; port-agnostic).
  port="$(grep -E '^CLIENT_RPC_PORT=' "$ENV_FILE" | tail -1 | cut -d= -f2)"
  port="${port:-8545}"

  # 5. Run each duration from a pristine overlay.
  for dur in $DURATIONS; do
    milestone="$SNAP_ALIAS-$SNAP_LABEL-$block-$dur"
    echo
    echo "--- run $milestone ($dur) ---"
    set_yaml_quoted "run_time" "$dur" "$CONFIG"

    "$INFRA" down || true
    clean_stale_containers    # clear any foreign-held benchmark_* names (keeps data volumes)
    if ! "$INFRA"; then
      echo "WARN: start_infra reported non-zero for $milestone (continuing to RPC wait)." >&2
    fi

    echo "  waiting for ${CLIENT} RPC on :$port (up to ${RPC_READY_TIMEOUT}s; FlatDb reprocess ~10-15 min)..."
    if ! wait_for_rpc "$port"; then
      echo "ERROR: ${CLIENT} RPC not ready within ${RPC_READY_TIMEOUT}s — skipping $milestone (docker logs benchmark_${CLIENT})." >&2
      SUMMARY+=("$(printf '%s\t%s' "$milestone" "SKIP:rpc-not-ready")")
      continue
    fi
    echo "  RPC ready — launching benchmark."

    "$RUNNER" "$milestone" || echo "WARN: run_benchmark returned non-zero for $milestone (may be a 60m OOM — partial metrics kept)." >&2

    out="benchmarks/$milestone/metrics_${CLIENT}.json"
    if [[ -f "$out" ]] && grep -q '"cpu_percent_peak"' "$out"; then
      status="OK"
    elif [[ -f "$out" ]]; then
      status="PARTIAL:no-resource-metrics"
    else
      status="FAIL:no-metrics"
    fi
    echo "  $milestone -> $status ($out)"
    SUMMARY+=("$(printf '%s\t%s' "$milestone" "$status")")
  done

  # 6. Leave infra UP by default (KEEP_INFRA=1) so Grafana + the node stay
  # queryable after the run; the snapshot is kept (the overlay is still mounted
  # on it). Set KEEP_INFRA=0 to tear down, and KEEP_SNAPSHOTS=0 to also free the
  # snapshot dir. Note: with a multi-snapshot manifest, the NEXT snapshot's
  # start_infra down (step 1) still tears this one down first, so KEEP_INFRA only
  # leaves the LAST snapshot's stack running.
  if [[ "$KEEP_INFRA" == "1" ]]; then
    echo "--- leaving infra UP after $block (KEEP_INFRA=1): Grafana + ${CLIENT} RPC stay live; snapshot kept ---"
  else
    echo "--- tearing down snapshot $block ---"
    "$INFRA" down || true
    if [[ "$KEEP_SNAPSHOTS" == "0" ]]; then
      echo "  removing $target (KEEP_SNAPSHOTS=0)"
      rm -rf "$target"
    fi
  fi
  return 0
}

echo "[$CLIENT] snapshot matrix starting"
echo "  manifest:  $MANIFEST"
echo "  durations: $DURATIONS"
echo "  dir/name:  $SNAP_ALIAS-$SNAP_LABEL-<block>$SNAP_SUFFIX   (base $SNAP_BASE)"
echo "  keep:      KEEP_SNAPSHOTS=$KEEP_SNAPSHOTS"

# Main loop: read the manifest, dispatch each snapshot (never abort the sweep).
while IFS= read -r rawline || [[ -n "$rawline" ]]; do
  line="${rawline%%#*}"                       # strip trailing comments
  line="$(echo "$line" | xargs || true)"      # trim whitespace, collapse spaces
  [[ -z "$line" ]] && continue
  # shellcheck disable=SC2086
  set -- $line
  run_one_snapshot "$1" "${2:-}" || true
done < "$MANIFEST"

echo
echo "===================== SUMMARY ====================="
if (( ${#SUMMARY[@]} == 0 )); then
  echo "  (no snapshots processed)"
else
  printf '  %s\n' "${SUMMARY[@]}"
fi
echo "==================================================="
