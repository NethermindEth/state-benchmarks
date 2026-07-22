"""Unit tests for src/orchestrator/overlay.py — pure helpers only.

The mount/umount side effects need a real Linux overlayfs + privileges, so they
are exercised in the on-VM smoke test, not here. These tests cover config
parsing, command construction, and the `env` CLI output that start_infra.sh
evals.
"""
import os

import pytest

import overlay


def _enabled_cfg(**overrides):
    cfg = {
        "enabled": True,
        "lowerdir": "/mnt/bigdata/snap/mainnet",
        "scratch_dir": "/mnt/bigdata/overlay/neth",
        "db_path_env": "NETHERMIND_DB_PATH",
        "name": "neth-bloatnet",
        "sudo": False,
    }
    cfg.update(overrides)
    return cfg


# ---------- parse_overlay_config ----------

def test_parse_returns_none_when_section_missing():
    assert overlay.parse_overlay_config(None) is None
    assert overlay.parse_overlay_config({}) is None


def test_parse_returns_none_when_disabled():
    assert overlay.parse_overlay_config({"enabled": False, "lowerdir": "/x", "db_path_env": "Y"}) is None


def test_parse_requires_lowerdir_and_db_path_env():
    with pytest.raises(ValueError):
        overlay.parse_overlay_config({"enabled": True, "db_path_env": "Y"})
    with pytest.raises(ValueError):
        overlay.parse_overlay_config({"enabled": True, "lowerdir": "/x"})


def test_parse_derives_scratch_subdirs():
    cfg = overlay.parse_overlay_config(_enabled_cfg())
    assert cfg.lowerdir == "/mnt/bigdata/snap/mainnet"
    assert cfg.scratch_dir == "/mnt/bigdata/overlay/neth"
    assert cfg.upper_dir == "/mnt/bigdata/overlay/neth/upper"
    assert cfg.work_dir == "/mnt/bigdata/overlay/neth/work"
    assert cfg.merged_dir == "/mnt/bigdata/overlay/neth/merged"


def test_parse_defaults_scratch_beside_lowerdir():
    cfg = overlay.parse_overlay_config(_enabled_cfg(scratch_dir=None, name="abc"))
    # Scratch lands next to the snapshot so it shares the snapshot's filesystem.
    assert cfg.scratch_dir == "/mnt/bigdata/snap/.overlay-abc"


def test_parse_defaults_mount_options_and_sudo():
    cfg = overlay.parse_overlay_config({"enabled": True, "lowerdir": "/x", "db_path_env": "Y"})
    assert cfg.mount_options == overlay.DEFAULT_MOUNT_OPTIONS
    assert cfg.sudo is True


# ---------- build_mount_command ----------

def test_build_mount_command_exact_form():
    cfg = overlay.parse_overlay_config(_enabled_cfg())
    assert overlay.build_mount_command(cfg) == [
        "mount", "-t", "overlay", "neth-bloatnet",
        "-o",
        "lowerdir=/mnt/bigdata/snap/mainnet,"
        "upperdir=/mnt/bigdata/overlay/neth/upper,"
        "workdir=/mnt/bigdata/overlay/neth/work,"
        "redirect_dir=on,metacopy=on,volatile",
        "/mnt/bigdata/overlay/neth/merged",
    ]


def test_build_mount_command_prefixes_sudo_when_enabled():
    cfg = overlay.parse_overlay_config(_enabled_cfg(sudo=True))
    assert overlay.build_mount_command(cfg)[0] == "sudo"


def test_build_mount_command_honors_custom_options():
    cfg = overlay.parse_overlay_config(_enabled_cfg(mount_options=["index=off"]))
    opts = overlay.build_mount_command(cfg)[5]
    assert opts.endswith("index=off")
    assert "volatile" not in opts


def test_build_umount_command():
    cfg = overlay.parse_overlay_config(_enabled_cfg())
    assert overlay.build_umount_command(cfg) == ["umount", "/mnt/bigdata/overlay/neth/merged"]
    cfg_sudo = overlay.parse_overlay_config(_enabled_cfg(sudo=True))
    assert overlay.build_umount_command(cfg_sudo) == ["sudo", "umount", "/mnt/bigdata/overlay/neth/merged"]


# ---------- CLI: env / disabled no-ops ----------

def _write_config(tmp_path, overlay_section):
    import yaml
    p = tmp_path / "config.yml"
    p.write_text(yaml.safe_dump({"overlay": overlay_section} if overlay_section is not None else {}))
    return str(p)


def test_cli_env_prints_export(tmp_path, capsys):
    config_path = _write_config(tmp_path, _enabled_cfg())
    rc = overlay.main(["env", "--config", config_path])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "export NETHERMIND_DB_PATH=/mnt/bigdata/overlay/neth/merged"


def test_cli_env_prints_nothing_when_disabled(tmp_path, capsys):
    config_path = _write_config(tmp_path, {"enabled": False, "lowerdir": "/x", "db_path_env": "Y"})
    rc = overlay.main(["env", "--config", config_path])
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_cli_up_is_noop_when_disabled(tmp_path):
    # No overlay section at all → up returns 0 without attempting any mount.
    config_path = _write_config(tmp_path, None)
    assert overlay.main(["up", "--config", config_path]) == 0


def test_cli_status_when_disabled(tmp_path):
    config_path = _write_config(tmp_path, {"enabled": False})
    assert overlay.main(["status", "--config", config_path]) == 0


# ---------- restore: busy-umount handling ----------

def _parsed(**overrides):
    return overlay.parse_overlay_config(_enabled_cfg(**overrides))


def test_restore_skips_umount_when_not_mounted(monkeypatch):
    cfg = _parsed()
    cmds = []
    monkeypatch.setattr(overlay, "_run", lambda cmd, check: cmds.append(cmd) or 0)
    monkeypatch.setattr(overlay, "is_mounted", lambda _cfg: False)
    overlay.restore(cfg)
    assert all("umount" not in cmd for cmd in cmds)
    assert sum(1 for cmd in cmds if "rm" in cmd) == 3


def test_restore_unmounts_then_wipes(monkeypatch):
    cfg = _parsed()
    cmds = []
    mounted = {"v": True}

    def fake_run(cmd, check):
        cmds.append(cmd)
        if "umount" in cmd:
            mounted["v"] = False
        return 0

    monkeypatch.setattr(overlay, "_run", fake_run)
    monkeypatch.setattr(overlay, "is_mounted", lambda _cfg: mounted["v"])
    monkeypatch.setattr(overlay.time, "sleep", lambda *_: None)
    overlay.restore(cfg)
    assert cmds[0] == overlay.build_umount_command(cfg)
    assert sum(1 for cmd in cmds if "rm" in cmd) == 3


def test_restore_busy_mount_raises_and_never_wipes(monkeypatch):
    """A persistently busy overlay must surface a real failure — rm -rf on a
    live multi-TB overlay would wreck the upper dir while reporting the
    snapshot 'pristine' and stack a second mount on the next run."""
    cfg = _parsed()
    cmds = []
    monkeypatch.setattr(overlay, "_run", lambda cmd, check: cmds.append(cmd) or 0)
    monkeypatch.setattr(overlay, "is_mounted", lambda _cfg: True)
    monkeypatch.setattr(overlay.time, "sleep", lambda *_: None)
    with pytest.raises(RuntimeError, match="busy"):
        overlay.restore(cfg)
    # Retried, fell back to lazy umount, and never ran rm -rf.
    assert any("-l" in cmd for cmd in cmds)
    assert all("rm" not in cmd for cmd in cmds)
