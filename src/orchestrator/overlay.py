"""Overlayfs snapshot isolation for benchmark runs.

A benchmark that drives the mock-CL *replay* driver advances the chain head by
writing new blocks straight into the on-disk snapshot the client container
bind-mounts. That permanently mutates the snapshot, so the next client can no
longer start from the *exact same* block, and re-extracting the snapshot is
slow / often impossible on a space-constrained disk.

This module stacks a Linux ``overlay`` mount on top of the pristine snapshot:

    lowerdir = the snapshot          (read-only; never touched)
    upperdir = <scratch>/upper       (all writes land here)
    workdir  = <scratch>/work        (overlay bookkeeping)
    merged   = <scratch>/merged      (what the container actually mounts)

The container reads/writes through ``merged``; the snapshot underneath stays
pristine. Restoring it is just ``umount merged`` + deleting the scratch dirs.

Mirrors execution-payloads-benchmarks' ``OverlaySnapshotService``. The pure
helpers (:func:`parse_overlay_config`, :func:`build_mount_command`,
:func:`build_umount_command`) are unit-tested; the side-effecting CLI actions
shell out to ``mount`` / ``umount`` / ``rm``.

CLI (config lives in the benchmark YAML under a top-level ``overlay:`` block)::

    PYTHONPATH=src python -m orchestrator.overlay up     --config config.yml
    PYTHONPATH=src python -m orchestrator.overlay env     --config config.yml
    PYTHONPATH=src python -m orchestrator.overlay status  --config config.yml
    PYTHONPATH=src python -m orchestrator.overlay down    --config config.yml

``up`` restores *first* (unmount any stale overlay + wipe scratch) and then
mounts fresh, so every run starts from the pristine snapshot regardless of how
the previous run ended — that is the "restore before each test start" model.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

# Log to stderr so `eval "$(... env)"` only captures the export line on stdout.
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] overlay: %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

# Same options the Go reference uses: redirect_dir/metacopy keep copy-ups cheap,
# volatile skips syncs (fine — the overlay is discarded after the run).
DEFAULT_MOUNT_OPTIONS = ["redirect_dir=on", "metacopy=on", "volatile"]


@dataclass
class OverlayConfig:
    lowerdir: str
    scratch_dir: str
    db_path_env: str
    name: str = "benchmark-overlay"
    mount_options: List[str] = field(default_factory=lambda: list(DEFAULT_MOUNT_OPTIONS))
    sudo: bool = True

    @property
    def upper_dir(self) -> str:
        return os.path.join(self.scratch_dir, "upper")

    @property
    def work_dir(self) -> str:
        return os.path.join(self.scratch_dir, "work")

    @property
    def merged_dir(self) -> str:
        return os.path.join(self.scratch_dir, "merged")


def parse_overlay_config(cfg: Optional[Dict[str, Any]]) -> Optional[OverlayConfig]:
    """Build an OverlayConfig from the YAML ``overlay`` dict, or None if disabled.

    Returns None when the section is missing or ``enabled`` is falsy, so callers
    can treat "no overlay" and "overlay off" identically. Raises ValueError when
    enabled but missing a required field.
    """
    if not cfg or not cfg.get("enabled"):
        return None

    lowerdir = cfg.get("lowerdir")
    if not lowerdir:
        raise ValueError("overlay.enabled is true but overlay.lowerdir is not set")
    db_path_env = cfg.get("db_path_env")
    if not db_path_env:
        raise ValueError("overlay.enabled is true but overlay.db_path_env is not set")

    lowerdir = os.path.abspath(os.path.expanduser(str(lowerdir)))
    name = str(cfg.get("name") or "benchmark-overlay")

    scratch = cfg.get("scratch_dir")
    if scratch:
        scratch_dir = os.path.abspath(os.path.expanduser(str(scratch)))
    else:
        # Default beside the snapshot so scratch lands on the same (big, xattr-
        # capable) filesystem overlayfs requires for upperdir.
        scratch_dir = os.path.join(os.path.dirname(lowerdir), f".overlay-{name}")

    mount_options = cfg.get("mount_options")
    if mount_options:
        mount_options = [str(o) for o in mount_options]
    else:
        mount_options = list(DEFAULT_MOUNT_OPTIONS)

    return OverlayConfig(
        lowerdir=lowerdir,
        scratch_dir=scratch_dir,
        db_path_env=str(db_path_env),
        name=name,
        mount_options=mount_options,
        sudo=bool(cfg.get("sudo", True)),
    )


def _sudo_prefix(cfg: OverlayConfig) -> List[str]:
    return ["sudo"] if cfg.sudo else []


def build_mount_command(cfg: OverlayConfig) -> List[str]:
    """`mount -t overlay <name> -o lowerdir=…,upperdir=…,workdir=…,<opts> <merged>`."""
    opts = ",".join(
        [
            f"lowerdir={cfg.lowerdir}",
            f"upperdir={cfg.upper_dir}",
            f"workdir={cfg.work_dir}",
            *cfg.mount_options,
        ]
    )
    return _sudo_prefix(cfg) + ["mount", "-t", "overlay", cfg.name, "-o", opts, cfg.merged_dir]


def build_umount_command(cfg: OverlayConfig) -> List[str]:
    return _sudo_prefix(cfg) + ["umount", cfg.merged_dir]


def is_mounted(cfg: OverlayConfig) -> bool:
    """True when the merged dir is currently an overlay mountpoint."""
    target = os.path.realpath(cfg.merged_dir)
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and os.path.realpath(parts[1]) == target and parts[2] == "overlay":
                    return True
    except FileNotFoundError:
        # No /proc (e.g. macOS) — fall back to the mountpoint syscall heuristic.
        return os.path.ismount(cfg.merged_dir)
    return False


def _run(cmd: List[str], check: bool) -> int:
    logger.info("running: %s", " ".join(cmd))
    return subprocess.run(cmd, check=check).returncode


def restore(cfg: OverlayConfig) -> None:
    """Unmount any existing overlay and wipe the scratch dirs.

    Tolerant of "already gone" so it is safe to call before every mount and as a
    standalone teardown. The upper dir may hold root-owned files written by the
    container, so removal goes through ``rm -rf`` (with sudo) rather than shutil.
    """
    # umount is non-fatal: it fails when nothing is mounted, which is fine.
    _run(build_umount_command(cfg), check=False)
    for path in (cfg.merged_dir, cfg.upper_dir, cfg.work_dir):
        _run(_sudo_prefix(cfg) + ["rm", "-rf", path], check=False)


def prepare_dirs(cfg: OverlayConfig) -> None:
    """Create the merged/upper/work dirs (world-writable, like the Go reference)."""
    for path in (cfg.merged_dir, cfg.upper_dir, cfg.work_dir):
        _run(_sudo_prefix(cfg) + ["mkdir", "-p", path], check=True)
        _run(_sudo_prefix(cfg) + ["chmod", "0777", path], check=True)


def up(cfg: OverlayConfig) -> None:
    """Restore (clean slate) then mount a fresh overlay. Mount failure is fatal."""
    if not os.path.isdir(cfg.lowerdir):
        raise FileNotFoundError(f"overlay lowerdir (snapshot) does not exist: {cfg.lowerdir}")
    logger.info("restoring before mount: %s", cfg.scratch_dir)
    restore(cfg)
    prepare_dirs(cfg)
    _run(build_mount_command(cfg), check=True)
    logger.info("overlay mounted: %s -> %s (lower=%s)", cfg.name, cfg.merged_dir, cfg.lowerdir)


def down(cfg: OverlayConfig) -> None:
    """Unmount + wipe scratch, restoring the pristine snapshot and freeing disk."""
    restore(cfg)
    logger.info("overlay torn down; snapshot %s is pristine", cfg.lowerdir)


def _load_overlay_section(config_path: str) -> Optional[Dict[str, Any]]:
    with open(config_path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("overlay")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Overlayfs snapshot isolation for benchmark runs")
    parser.add_argument("action", choices=["up", "down", "env", "status"])
    parser.add_argument("--config", required=True, help="Path to the benchmark YAML config")
    args = parser.parse_args(argv)

    cfg = parse_overlay_config(_load_overlay_section(args.config))
    if cfg is None:
        # Disabled / absent: env prints nothing, the rest are no-ops. This keeps
        # the bash `eval "$(... env)"` and runner calls inert when overlay is off.
        if args.action != "env":
            logger.info("overlay disabled in %s; nothing to do for '%s'", args.config, args.action)
        return 0

    # As root, sudo is unnecessary noise (and may not exist in minimal images).
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        cfg.sudo = False

    if args.action == "up":
        up(cfg)
    elif args.action == "down":
        down(cfg)
    elif args.action == "status":
        logger.info(
            "overlay %s: %s (merged=%s, lower=%s)",
            cfg.name,
            "MOUNTED" if is_mounted(cfg) else "not mounted",
            cfg.merged_dir,
            cfg.lowerdir,
        )
    elif args.action == "env":
        # Only stdout line — consumed by `eval "$(... env)"` in start_infra.sh.
        print(f"export {cfg.db_path_env}={cfg.merged_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
