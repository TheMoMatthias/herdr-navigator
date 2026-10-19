import json

import pytest

from navigator import settings


@pytest.fixture
def home(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin" / ".herdr-dirs.json"
    plugin.parent.mkdir()
    monkeypatch.setattr(settings, "_dirs_files", lambda: [plugin, tmp_path / ".herdr-navigator-dirs.json"])
    monkeypatch.setenv("HERDR_PLUGIN_ID", settings.PLUGIN_ID)
    monkeypatch.delenv("HERDR_PLUGIN_STATE_DIR", raising=False)
    monkeypatch.delenv("HERDR_PLUGIN_CONFIG_DIR", raising=False)
    settings._REMEMBERED.clear()
    yield plugin
    settings._REMEMBERED.clear()


def test_folders_given_by_herdr_are_kept_in_both_places(home, tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    assert settings._look_up("STATE") == str(tmp_path / "state")
    assert json.loads(home.read_text())["STATE"] == str(tmp_path / "state")
    assert json.loads((tmp_path / ".herdr-navigator-dirs.json").read_text())["STATE"] == str(tmp_path / "state")


def test_a_replaced_plugin_folder_does_not_forget_them(home, tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    settings._look_up("STATE")
    home.unlink()  # a reinstall: fresh folder, no remembered file
    monkeypatch.delenv("HERDR_PLUGIN_STATE_DIR")
    assert settings._look_up("STATE") == str(tmp_path / "state")  # a key-bound command, no env
    assert home.exists() is False  # it only reads here; the next run with herdr's env rewrites it
