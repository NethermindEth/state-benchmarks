#!/usr/bin/env bash
# Download + extract an ethpandaops-style snapshot.tar.zst into a target dir.
#
#   scripts/fetch_snapshot.sh <snapshot_url> <target_dir>
#
# Reproducible across snapshots — just change the URL and target. The work runs
# inside a throwaway alpine container (aria2 + zstd + tar), launched DETACHED as
# container "snap_dl", so it survives SSH disconnects on the benchmark VM.
#
# The target dir is WIPED before extraction (tar merges by name, so stale files
# from a previous snapshot must go). Peak disk use = sizeof(.tar.zst) +
# sizeof(extracted); make sure the filesystem has room.
#
# Monitor / check result:
#   docker logs -f snap_dl
#   docker inspect snap_dl --format '{{.State.Status}} {{.State.ExitCode}}'   # exited 0 = success
#
# Example (Nethermind perf-devnet-3 @ block 24358000):
#   scripts/fetch_snapshot.sh \
#     https://snapshots.ethpandaops.io/perf-devnet-3/nethermind/24358000/snapshot.tar.zst \
#     /mnt/bigdata/neth-x35-24358000
set -euo pipefail

URL="${1:?usage: fetch_snapshot.sh <snapshot_url> <target_dir>}"
TARGET="${2:?usage: fetch_snapshot.sh <snapshot_url> <target_dir>}"

mkdir -p "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"   # absolute path for the bind mount

docker rm -f snap_dl >/dev/null 2>&1 || true   # fresh container each run

echo "[fetch_snapshot] url    = $URL"
echo "[fetch_snapshot] target = $TARGET  (WIPED before extract)"

docker run -d --name snap_dl -w /data \
  -v "$TARGET":/data \
  -e URL="$URL" -e OWNER="$(id -u):$(id -g)" \
  --entrypoint /bin/sh alpine:3 -c '
set -eu
set -o pipefail
apk add --no-cache aria2 tar zstd coreutils binutils
echo ">>> WIPING target dir (tar merges by name; stale files must go)"
rm -rf /data/* /data/.[!.]* 2>/dev/null || true
echo ">>> downloading (16-way parallel): $URL"
aria2c -x16 -s16 -k100M -c --file-allocation=falloc \
       --max-tries=0 --retry-wait=10 --summary-interval=30 \
       -o snapshot.tar.zst "$URL"
echo ">>> extracting (zstd -T0 --long=31 | tar)"
zstd -d -T0 --long=31 -c snapshot.tar.zst | tar -xf - -C /data
rm -f snapshot.tar.zst
chown -R "$OWNER" /data
echo ">>> extracted top-level layout:"; ls -la /data
echo ">>> total size:"; du -sh /data
echo ">>> best-effort geth-style manifest sanity (no-op for Nethermind):"
CD=/data/chaindata; [ -d "$CD" ] || CD=/data
# Guard the read so a missing CURRENT (Nethermind has none) does not trip set -e.
if [ -f "$CD/CURRENT" ]; then
  MAN="$CD/$(tr -d "\n" < "$CD/CURRENT")"
  miss=0
  for n in $(strings "$MAN" 2>/dev/null | grep -oE "[0-9]{6,}" | sort -u || true); do
    [ -f "$CD/$n.sst" ] || [ -f "$CD/$n.ldb" ] || { echo "    MISSING sstable: $n"; miss=$((miss+1)); }
  done
  [ "$miss" -eq 0 ] && echo ">>> manifest fully satisfied" || echo ">>> WARNING: $miss referenced sstables missing (snapshot broken upstream)"
else
  echo "    (no geth CURRENT/MANIFEST under $CD — expected for Nethermind; skipping)"
fi
echo ">>> DONE"
'
echo "[fetch_snapshot] launched detached as container: snap_dl"
echo "  monitor:  docker logs -f snap_dl"
echo "  status :  docker inspect snap_dl --format '{{.State.Status}} {{.State.ExitCode}}'  (exited 0 = ok)"
