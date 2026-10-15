import subprocess

import pytest
import tomlkit

from navigator import setup


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HERDR_PLUGIN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HERDR_CONFIG_PATH", str(tmp_path / "config.toml"))
    (tmp_path / "state").mkdir()
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    # herdr itself is not under test: accept the config, reload nothing
    real = subprocess.run
    monkeypatch.setattr(setup.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "ok", "")
                        if "herdr" in str(cmd[0]).lower() or cmd[0] == "herdr" else real(cmd, **kw))
    monkeypatch.setenv("HERDR_BIN_PATH", "herdr")


def f1(tmp_path):
    doc = tomlkit.parse((tmp_path / "config.toml").read_text(encoding="utf-8"))
    return [c for c in doc["keys"]["command"] if "f1" in list(c["key"])][0]


def test_stale_popup_binding_is_replaced_without_asking(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[[keys.command]]\nkey = ["prefix+space", "f1"]\ntype = "popup"\ncommand = "old app projects"\n'
        'description = "Navigator: projects, worktrees, agents"\n', encoding="utf-8")
    assert setup.ensure() == "setup: key bindings refreshed"
    assert f1(tmp_path)["type"] == "shell" and "opennav" in f1(tmp_path)["command"]


def test_current_config_is_left_alone(tmp_path):
    setup.ensure()
    cfg = (tmp_path / "config.toml").read_text(encoding="utf-8")
    (tmp_path / "config.toml").write_text(cfg + "\n# my own note\n", encoding="utf-8")
    assert setup.ensure() == ""
    assert "# my own note" in (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_new_plugin_code_or_place_applies_again(tmp_path, monkeypatch):
    setup.ensure()
    monkeypatch.setattr(setup, "ROOT", tmp_path / "elsewhere")  # reinstalled into another folder
    assert setup.ensure() == "setup: key bindings refreshed"


def test_uninstall_is_respected_until_install_is_run_again(tmp_path):
    setup.ensure()
    assert setup.main(["uninstall"]) == 0
    assert setup.ensure() == ""
    assert "navigator" not in (tmp_path / "config.toml").read_text(encoding="utf-8").lower()
    assert setup.main(["install"]) == 0
    assert (tmp_path / "state" / "setup-off").exists() is False


def test_a_test_never_reaches_the_real_config(monkeypatch):
    monkeypatch.delenv("HERDR_CONFIG_PATH")
    assert setup.ensure() == ""
