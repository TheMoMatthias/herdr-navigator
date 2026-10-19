"""Settings › System: herdr's version, update, stop and restart (navigator/herdrctl.py).

The detached runs are exercised for real on Windows, against a fake `herdr` program, so the
quoting of paths with spaces and the way the log ends are proven, not assumed.
"""
import asyncio
import os
import subprocess
import time

import pytest
from textual.widgets import Button

from navigator import herdrctl

from test_settings_ui import Host, all_text, isolated, settle, text_of  # noqa: F401  (isolated: autouse fixture)

STATUS_STALE = """client:
  version: 0.9.5
  channel: stable
  protocol: 22
  endpoint_protocol_generation: 1

server:
  status: running
  version: 0.9.3
  endpoint_compatible: yes

update:
  restart_needed: no
  server_binary_stale: yes
"""

STATUS_STOPPED = """client:
  version: 0.9.5
  channel: stable

server:
  status: stopped
"""


def test_status_of_a_running_server_behind_the_installed_version():
    st = herdrctl.parse_status(STATUS_STALE)
    assert (st.ok, st.client, st.server, st.running, st.stale) == (True, "0.9.5", "0.9.3", True, True)


def test_status_without_a_server_has_no_server_version():
    st = herdrctl.parse_status(STATUS_STOPPED)
    assert (st.client, st.server, st.running, st.stale) == ("0.9.5", "", False, False)


def test_status_of_garbage_is_not_ok():
    assert herdrctl.parse_status("boom").ok is False


def test_newer_compares_numbers_not_text():
    assert herdrctl.newer("0.9.10", "0.9.5")
    assert not herdrctl.newer("0.9.5", "0.9.5")
    assert not herdrctl.newer("", "0.9.5") and not herdrctl.newer("0.9.6", "")


def test_update_progress_reads_the_log(tmp_path):
    log = herdrctl.update_log()
    assert herdrctl.update_progress() == (False, False, "starting…")
    log.write_text("checking stable channel...\ndownloading 0.9.5...\n", encoding="utf-8")
    assert herdrctl.update_progress() == (False, False, "downloading 0.9.5...")
    log.write_text("downloading 0.9.5...\n==> Installing\ninstalled 0.9.5\n__DONE ok\n", encoding="utf-8")
    assert herdrctl.update_progress() == (True, True, "installed 0.9.5")
    log.write_text("update failed: no network\n__DONE failed\n", encoding="utf-8")
    assert herdrctl.update_progress() == (True, False, "update failed: no network")


def test_commands_name_the_newest_binary_and_the_log(monkeypatch):
    seen = []
    monkeypatch.setattr(herdrctl, "_detached", lambda c: seen.append(c) or True)
    monkeypatch.setattr(herdrctl, "binary", lambda: r"C:\a b\herdr.exe")
    assert herdrctl.start_update() and herdrctl.stop()
    assert r'"C:\a b\herdr.exe" update' in seen[0] and "herdr-update.log" in seen[0]
    assert r'"C:\a b\herdr.exe" server stop' in seen[1]


@pytest.mark.skipif(os.name != "nt", reason="the detached runs use Windows' process service")
class TestOnWindows:
    SCRIPT = ("@echo off\r\n"
              "if \"%1\"==\"update\" (echo checking...& echo installed 9.9.9& exit /b CODE)\r\n"
              "if \"%1\"==\"server\" (echo stopping>> \"%~dp0calls.txt\"& exit /b 0)\r\n"
              "if \"%1\"==\"status\" (echo status: stopped& exit /b 0)\r\n")

    @pytest.fixture
    def make(self, tmp_path, monkeypatch):
        """A fake herdr whose `update` exits with the given code. The code is written into the
        script: a process Windows' process service creates does not inherit this test's
        environment (that is the point of using it)."""
        def build(code=0):
            d = tmp_path / f"a folder with spaces {code}"
            d.mkdir()
            exe = d / "herdr.cmd"
            exe.write_text(self.SCRIPT.replace("CODE", str(code)), encoding="utf-8")
            monkeypatch.setattr(herdrctl, "binary", lambda: str(exe))
            return exe
        return build

    @pytest.fixture
    def fake(self, make):
        return make(0)

    def wait(self, fn, seconds=25):
        end = time.time() + seconds
        while time.time() < end:
            r = fn()
            if r:
                return r
            time.sleep(0.4)
        return None

    def test_update_runs_detached_and_the_log_ends_ok(self, fake):
        assert herdrctl.start_update()
        done = self.wait(lambda: herdrctl.update_progress()[0])
        assert done, herdrctl.update_log().read_text(errors="replace") if herdrctl.update_log().exists() else "no log"
        finished, ok, said = herdrctl.update_progress()
        assert ok and said == "installed 9.9.9"

    def test_a_failing_update_ends_failed(self, make):
        make(3)
        assert herdrctl.start_update()
        assert self.wait(lambda: herdrctl.update_progress()[0])
        assert herdrctl.update_progress()[1] is False

    def test_stop_runs_detached(self, fake):
        assert herdrctl.stop()
        calls = fake.parent / "calls.txt"
        assert self.wait(calls.exists)

    def test_restart_stops_waits_then_opens_a_window(self, fake, tmp_path):
        opener = fake.parent / "opener.cmd"
        opener.write_text("@echo off\r\necho opened %*> \"%~dp0opened.txt\"\r\n", encoding="utf-8")
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                            str(herdrctl.ROOT / "navigator" / "herdr_restart.ps1"), "-Herdr", str(fake),
                            "-Terminal", str(opener)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        assert (fake.parent / "calls.txt").exists()                   # the server was told to stop
        assert self.wait(lambda: (fake.parent / "opened.txt").exists())  # and herdr opened again after it
        assert "herdr.cmd" in (fake.parent / "opened.txt").read_text(errors="replace")


def test_restart_is_only_offered_where_it_can_open_a_window(monkeypatch):
    monkeypatch.setattr(herdrctl.os, "name", "posix")
    assert herdrctl.can_restart() is False and herdrctl.restart() is False


# ---- the System section ------------------------------------------------------------------------

def run(coro_fn, mocked=None, size=(150, 50)):
    async def go():
        app = Host("system")
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await settle(app, pilot)
            await coro_fn(app, pilot, app.query_one("#sp"))

    asyncio.run(go())


def test_system_shows_both_versions_when_they_match():
    async def body(app, pilot, pane):
        blob = all_text(pane.query_one("#system"))
        assert "Installed 0.9.5" in blob and "server ● running 0.9.5" in blob and "latest 0.9.5" in blob
        assert "to use the new version" not in blob and "is available" not in blob
        assert pane.query_one("#sy-update", Button).disabled is True
    run(body)


def test_system_stale_server_says_restart(monkeypatch):
    monkeypatch.setattr(herdrctl, "status", lambda: herdrctl.Status(True, "", "0.9.5", "0.9.3", True, True))

    async def body(app, pilot, pane):
        blob = all_text(pane.query_one("#system"))
        assert "running 0.9.3" in blob and "restart it to use the new version" in blob
        assert pane.query_one("#sy-update", Button).disabled is True      # nothing newer to install
        assert pane.query_one("#sy-restart", Button).disabled is (os.name != "nt")
        assert pane.query_one("#sy-stop", Button).disabled is False
    run(body)


def test_system_offers_the_update_when_one_exists(monkeypatch):
    monkeypatch.setattr(herdrctl, "latest", lambda timeout=8: "0.9.6")

    async def body(app, pilot, pane):
        assert "0.9.6 is available" in all_text(pane.query_one("#system"))
        assert pane.query_one("#sy-update", Button).disabled is False
    run(body)


def test_system_without_a_server_cannot_stop_or_restart(monkeypatch):
    monkeypatch.setattr(herdrctl, "status", lambda: herdrctl.Status(True, "", "0.9.5", "", False, False))

    async def body(app, pilot, pane):
        assert "not running" in all_text(pane.query_one("#system"))
        assert pane.query_one("#sy-stop", Button).disabled and pane.query_one("#sy-restart", Button).disabled
    run(body)


def test_stop_asks_first_and_says_how_many_agents_end(monkeypatch):
    called = []
    monkeypatch.setattr(herdrctl, "stop", lambda: called.append("stop") or True)

    async def body(app, pilot, pane):
        pane.query_one("#sy-stop", Button).press()
        await pilot.pause()
        from navigator.startup_ui import ContextMenu
        menu = app.screen
        assert isinstance(menu, ContextMenu)
        labels = [str(menu.query_one("OptionList").get_option_at_index(i).prompt)
                  for i in range(menu.query_one("OptionList").option_count)]
        assert "3 agents end, 1 working now." in labels
        assert called == []                                   # nothing happened yet
        await pilot.press("escape")
        await pilot.pause()
        assert called == []                                   # cancelling does nothing
        pane.query_one("#sy-stop", Button).press()
        await pilot.pause()
        menu = app.screen
        menu.dismiss("stop")
        await pilot.pause()
        assert called == ["stop"]
    run(body)


def test_restart_runs_only_after_confirming(monkeypatch):
    if os.name != "nt":
        pytest.skip("restart is Windows-only")
    called = []
    monkeypatch.setattr(herdrctl, "restart", lambda terminal="": called.append("restart") or True)

    async def body(app, pilot, pane):
        pane.query_one("#sy-restart", Button).press()
        await pilot.pause()
        assert called == []
        app.screen.dismiss("restart")
        await pilot.pause()
        assert called == ["restart"]
    run(body)


def test_update_button_installs_and_shows_the_result(monkeypatch):
    monkeypatch.setattr(herdrctl, "latest", lambda timeout=8: "0.9.6")
    started = []
    monkeypatch.setattr(herdrctl, "start_update", lambda: started.append(1) or True)
    monkeypatch.setattr(herdrctl, "update_progress", lambda: (True, True, "installed 0.9.6"))

    async def body(app, pilot, pane):
        pane.query_one("#sy-update", Button).press()
        await pilot.pause()
        await settle(app, pilot)
        assert started == [1]
        assert text_of(pane.query_one("#sy-progress")) == "✓ installed 0.9.6"
    run(body)


def test_system_buttons_fit_on_a_narrow_terminal():
    async def body(app, pilot, pane):
        right = max(b.region.right for b in pane.query_one("#system").query(Button))
        assert right <= pane.region.right, (right, pane.region.right)
        assert len({b.region.y for b in pane.query_one("#system").query(Button)}) == 1  # still one row

    async def go():
        app = Host("system")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await settle(app, pilot)
            await body(app, pilot, app.query_one("#sp"))

    asyncio.run(go())
