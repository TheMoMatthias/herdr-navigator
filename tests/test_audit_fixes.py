"""Audit fixes: settings follow the file's mtime, toast_reply default, pane action failures, pane menu with no pane."""
import asyncio
import os
import tomllib

import pytest

from navigator import herdr, paneact, panemenu, settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_PLUGIN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "cfg").mkdir()
    settings.load.cache_clear()
    yield
    settings.load.cache_clear()


def test_load_rereads_a_changed_file_without_cache_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "RECHECK", 0)  # in the daemon an edit shows within RECHECK seconds
    path = tmp_path / "cfg" / "navigator.toml"
    path.write_text("[sessions]\nmax_age_days = 11\n", encoding="utf-8")
    assert settings.load().max_age_days == 11
    assert settings.load() is settings.load()  # unchanged file: cached
    st = path.stat()
    path.write_text("[sessions]\nmax_age_days = 22\n", encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert settings.load().max_age_days == 22


def test_default_toml_has_toast_reply():
    assert tomllib.loads(settings.DEFAULT_TOML)["alerts"]["toast_reply"] is True
    assert settings.load().alerts["toast_reply"] is True


def test_even_without_a_tab_reports_instead_of_calling_equalize(monkeypatch):
    calls = []
    monkeypatch.setattr(paneact, "target", lambda: ("w1:p2", ""))
    monkeypatch.setattr(paneact.panes, "equalize", lambda t: calls.append(t) or "ok")
    monkeypatch.setattr("navigator.compact._note_context", lambda ctx: None)
    assert paneact.run("even").startswith("✗")
    assert calls == []


def test_arrange_failure_is_reported(monkeypatch):
    monkeypatch.setattr(paneact, "target", lambda: ("w1:p2", "w1:t1"))
    monkeypatch.setattr("navigator.compact._note_context", lambda ctx: None)

    def boom(*a, **k):
        raise herdr.HerdrError("no such plugin")
    monkeypatch.setattr(paneact.herdr, "run", boom)
    msg = paneact.run("arrange")
    assert msg.startswith("✗") and "no such plugin" in msg
    monkeypatch.setattr(paneact.herdr, "run", lambda *a, **k: {})
    assert paneact.run("arrange") == ""


def _options(pane, agent=""):
    async def go():
        app = panemenu.PaneMenu(pane, agent)
        async with app.run_test() as pilot:
            await pilot.pause()
            ol = app.query_one(panemenu.OptionList)
            return [(o.id, o.disabled) for o in ol.options if o.id]
    return asyncio.run(go())


def test_pane_menu_with_no_pane_disables_every_option():
    opts = _options("")
    assert opts and all(dis for _, dis in opts)


def test_pane_menu_with_a_pane_keeps_options_enabled():
    opts = dict(_options("w1:p2", "claude"))
    assert opts["split-right"] is False and opts["compact"] is False
