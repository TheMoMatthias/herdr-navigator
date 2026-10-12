"""Sessions and Usage tabs after the declutter: one Logon menu, a lean legend per tab, a Total row."""
import asyncio
import time
from types import SimpleNamespace as NS

import pytest
from textual.app import App

from navigator import autostart, projects, settings, startup, usage
from navigator.sessions import Session

DAY = 86400


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_PLUGIN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "navigator.toml").write_text("[hide]\npatterns = []\n", encoding="utf-8")
    settings.load.cache_clear()
    projects.clear_cache()
    yield
    settings.load.cache_clear()
    projects.clear_cache()


def repo(tmp_path, name="proj"):
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    return root


def sess(cwd, sid, days_ago, title=None):
    return Session("claude", sid, str(cwd), title or sid, "", "", time.time() - days_ago * DAY, "", named=True)


def plain(t) -> str:
    return t.plain if hasattr(t, "plain") else str(t)


# ---- the legend ---------------------------------------------------------------------------------

def test_legend_for_gives_each_tab_only_its_own_symbols():
    from rich.text import Text
    from navigator.startup_ui import LEGEND, legend_for
    tabs = {t: legend_for(t).plain for t in ("projects", "agents", "sessions", "layout", "usage", "settings")}
    assert all(isinstance(legend_for(t), Text) for t in tabs)
    assert "☑" in tabs["sessions"] and "☐" in tabs["sessions"]
    assert "☑" not in tabs["agents"] and "☑" not in tabs["usage"]
    assert "↳" in tabs["agents"] and "↳" not in tabs["sessions"] and "↳" not in tabs["projects"]
    assert "◀" in tabs["layout"] and "◀" not in tabs["agents"]
    assert "⎇" in tabs["projects"] and "⎇" in tabs["usage"] and "⚠" not in tabs["usage"]
    assert "▲" in tabs["agents"]
    assert all(0 < len(v) < len(LEGEND.plain) for v in tabs.values())
    assert legend_for("panes").plain == tabs["layout"]          # the Layout TabPane's real id
    assert legend_for("nonsense").plain == LEGEND.plain         # unknown tab: everything


def test_help_screen_shows_the_legend_of_its_tab():
    from navigator.startup_ui import HelpScreen, legend_for

    class Host(App):
        pass

    async def run():
        app = Host()
        async with app.run_test(size=(120, 40)) as pilot:
            await app.push_screen(HelpScreen("t", "body", "usage"))
            await pilot.pause()
            assert app.screen.query_one("#hp-legend").render().plain == legend_for("usage").plain

    asyncio.run(run())


# ---- the Sessions bar -----------------------------------------------------------------------------

class Host(App):
    """Just enough of the Navigator around a StartupPane."""

    def __init__(self, pane):
        super().__init__()
        self.pane = pane
        self.relaunched = []

    def compose(self):
        yield self.pane

    def open_relaunch(self, cli="", panes=None):
        self.relaunched.append((cli, panes))

    def load_world(self):
        pass


async def settle(app, pilot):
    """Let the workers finish and their call_from_thread updates land."""
    for _ in range(3):
        try:
            await app.workers.wait_for_complete()
        except Exception:  # an exclusive worker replaced by its successor
            pass
        await pilot.pause()


def _world(tmp_path):
    root = repo(tmp_path)
    ss = [sess(root, "a", 0.1, "Alpha"), sess(root, "b", 0.2, "Beta"), sess(root, "c", 20, "Old")]
    return NS(sessions=ss, live_sessions={"a": NS(in_herdr=True, question=None, pane_id="w:p")})


def test_sessions_bar_is_one_logon_menu(tmp_path, monkeypatch):
    from navigator.startup_ui import ContextMenu, StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: True)
    pane = StartupPane()
    world = _world(tmp_path)

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane.show(world)
            await pilot.pause()
            ids = {w.id for w in pane.query("Button")}
            assert ids >= {"st-logon", "st-fold"}
            assert not ids & {"st-open", "st-ticks", "st-autostart", "st-relaunch"}
            assert not pane.query("#start-sum")
            label = str(pane.query_one("#st-logon").label)
            assert label.startswith("⏻ Logon: on") and label.endswith("▾") and "2" in label   # a, b auto-ticked
            assert "search title, prompt, branch  /" in pane.query_one("#st-search").placeholder
            await pilot.click("#st-logon")
            await pilot.pause()
            assert isinstance(app.screen, ContextMenu)
            await pilot.press("escape")
            await pilot.pause()
            monkeypatch.setattr(autostart, "installed", lambda: False)
            pane.sync_autostart()
            assert "OFF" in str(pane.query_one("#st-logon").label)

    asyncio.run(run())


def test_logon_menu_keeps_every_old_action(tmp_path, monkeypatch):
    from navigator.startup_ui import StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: True)
    pane = StartupPane()
    world = _world(tmp_path)
    opened, toggled = [], []

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane.show(world)
            items = dict(pane.logon_items())
            assert {"t-open", "t-running", "t-only-running", "t-none", "t-auto", "t-rules", "t-logon",
                    "t-relaunch"} <= set(items)
            assert "off" in items["t-logon"].lower()          # logon is on: the entry turns it off
            monkeypatch.setattr(pane, "open_ticked", lambda: opened.append(1))
            pane.on_ticks("t-open")
            assert opened == [1]
            pane.on_ticks("t-relaunch")
            assert app.relaunched == [("", None)]
            monkeypatch.setattr(autostart, "uninstall", lambda: toggled.append("off") or "off")
            monkeypatch.setattr(autostart, "install", lambda: toggled.append("on") or "on")
            pane.on_ticks("t-logon")
            await settle(app, pilot)
            assert toggled == ["off"]
            pane.on_ticks("t-none")
            assert not startup.selected(world.sessions)
            pane.on_ticks("t-auto")
            assert {r.session.id for r in startup.selected(world.sessions)} == {"a", "b"}
            pane.on_ticks("t-only-running")
            assert {r.session.id for r in startup.selected(world.sessions)} == {"a"}

    asyncio.run(run())


def test_rows_say_it_once(tmp_path, monkeypatch):
    from navigator.startup_ui import StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: True)
    pane = StartupPane()
    world = _world(tmp_path)
    startup.set_tick("claude:b", True)                       # b is your own tick, a is the auto-tick's

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane.show(world)
            t = pane.query_one("StartTable")
            proj = t.get_row("P|" + world.sessions[0].project.root)
            assert plain(proj[4]).split("   ")[1].startswith("2/3")
            assert "running" in plain(proj[4]) and "ticked" not in plain(proj[4]) and "in all" not in plain(proj[4])
            auto = t.get_row(f"S|{startup.skey(world.sessions[0])}")
            mine = t.get_row(f"S|{startup.skey(world.sessions[1])}")
            assert plain(auto[1]) == "☑ auto" and plain(mine[1]) == "☑"
            assert "bold" in str(auto[1].style) or all("bold" in str(sp.style) for sp in auto[1].spans)
            assert "auto" not in plain(auto[4]) and "options" not in plain(auto[4])

    asyncio.run(run())


def test_relaunch_sheet_running_mark_is_not_the_reply_hourglass():
    from navigator.startup_ui import RelaunchSheet
    assert RelaunchSheet.MARK["running"][0] == "◐"
    assert all(m[0] != "⏳" for m in RelaunchSheet.MARK.values())


def test_context_menu_labels_use_open(tmp_path, monkeypatch):
    from navigator.startup_ui import StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: True)
    pane = StartupPane()
    world = _world(tmp_path)

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane.show(world)
            live = dict(pane.menu_items(f"S|{startup.skey(world.sessions[0])}"))
            idle = dict(pane.menu_items(f"S|{startup.skey(world.sessions[1])}"))
            assert live["s-goto"].endswith("Open") and idle["s-open"].endswith("Open")

    asyncio.run(run())


# ---- Usage ----------------------------------------------------------------------------------------

def test_usage_total_is_the_first_bold_row(tmp_path):
    from datetime import date
    from navigator.startup_ui import UsagePane
    root = repo(tmp_path)
    s = sess(root, "a", 0.1, "Alpha")
    day = date.today().isoformat()
    su = usage.SessionUsage("claude", "a", {day: usage.Tally(fresh=1000, cached=5000, out=200)})
    pane = UsagePane()

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane._show(NS(sessions=[s]), {("claude", "a"): su})
            t = pane.query_one("#usage-table")
            first = t.get_row_at(0)
            assert plain(first[0]).startswith("Total")
            assert all("bold" in str(c.style) for c in first if plain(c))
            assert plain(first[2]) == usage.human(1200) and plain(first[3]) == usage.human(1200)
            assert plain(first[4]) == usage.human(200) and plain(first[5]) == usage.human(5000)
            assert plain(t.get_row_at(1)[0]) == "proj"
            assert not pane.query_one("#usage-sum").display
            assert "cheap" not in str(pane.query_one("#usage-sum").render())

    asyncio.run(run())


def test_sessions_fold_toggles_once_per_click(tmp_path, monkeypatch):
    """A click on ▸/▾ used to run twice (ours, then DataTable's own select): fold, then unfold."""
    from navigator.startup_ui import StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: True)
    pane = StartupPane()
    world = _world(tmp_path)

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane.show(world)
            await pilot.pause()
            t = pane.query_one("StartTable")
            n = t.row_count
            seen = []
            for _ in range(4):
                await pilot.click("StartTable", offset=(1, 1))
                await pilot.pause(0.2)
                seen.append(t.row_count)
            assert len(set(seen)) == 2 and seen[0] == seen[2] and seen[1] == seen[3], seen
            assert n in seen

    asyncio.run(run())


# ---- audited bugs ---------------------------------------------------------------------------------

def _two_project_world(tmp_path):
    r1, r2 = repo(tmp_path, "one"), repo(tmp_path, "two")
    ss = [sess(r1, "a", 0.1, "Alpha"), sess(r1, "b", 0.2, "Beta"), sess(r2, "d", 0.1, "Delta")]
    return NS(sessions=ss, live_sessions={"a": NS(in_herdr=True, question=None, pane_id="w:p")}), ss[0].project.root


def test_usage_collect_failure_is_shown_not_fatal(tmp_path, monkeypatch):
    from navigator.startup_ui import UsagePane

    def boom():
        raise OSError("disk gone")
    monkeypatch.setattr(usage, "collect", boom)
    pane = UsagePane()

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane.load(NS(sessions=[]))
            await settle(app, pilot)
            sm = pane.query_one("#usage-sum")
            assert sm.display and "Couldn't read transcripts: disk gone" in str(sm.render())

    asyncio.run(run())


def test_usage_keys_survive_same_sid_in_two_clis_and_same_folder_name_in_two_repos(tmp_path):
    from datetime import date
    from navigator.startup_ui import UsagePane
    day = date.today().isoformat()
    ra, rb = repo(tmp_path / "x", "proj"), repo(tmp_path / "y", "proj")
    s1, s2 = sess(ra, "same", 0.1, "One"), sess(rb, "same", 0.1, "Two")
    s2.cli = "codex"
    data = {(c, "same"): usage.SessionUsage(c, "same", {day: usage.Tally(fresh=10, out=1)}) for c in ("claude", "codex")}
    pane = UsagePane()

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane._show(NS(sessions=[s1, s2]), data)         # a duplicate key used to raise DuplicateKey
            t = pane.query_one("#usage-table")
            keys = [k.value for k in t.rows]
            assert "S|claude|same" in keys and "S|codex|same" in keys
            heads = [k for k in keys if k.startswith("P|")]
            assert sorted(heads) == sorted([f"P|{s1.project.root}", f"P|{s2.project.root}"]) and len(heads) == 2
            assert [plain(t.get_row(h)[0]) for h in heads] == ["proj", "proj"]

    asyncio.run(run())


def test_usage_recollects_when_shown_again_after_a_minute(tmp_path, monkeypatch):
    from navigator.startup_ui import UsagePane
    calls = []
    monkeypatch.setattr(usage, "collect", lambda: calls.append(1) or {})
    pane = UsagePane()

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            w = NS(sessions=[])
            pane.load(w)
            await settle(app, pilot)
            pane.load(w)                                     # right away: still fresh
            await settle(app, pilot)
            assert len(calls) == 1
            pane.collected = time.time() - 61
            pane.load(w)
            await settle(app, pilot)
            assert len(calls) == 2

    asyncio.run(run())


def test_menu_goto_and_relaunch_of_a_session_that_stopped(tmp_path, monkeypatch):
    from navigator.startup_ui import StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: True)
    pane = StartupPane()
    world = _world(tmp_path)
    key = f"S|{startup.skey(world.sessions[0])}"             # "a" is running when the menu opens

    async def run():
        app = Host(pane)
        notes, finished = [], []
        app.finish = lambda *a, **k: finished.append(a)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(app, "notify", lambda msg, **k: notes.append(msg))
            pane.show(world)
            world.live_sessions.clear()                      # ...and has stopped before the choice
            for choice in ("s-goto", "s-relaunch"):
                pane.on_menu(key, choice)
            assert notes == ["It is no longer running."] * 2
            assert not finished and not app.relaunched

    asyncio.run(run())


def test_project_menu_opens_only_that_projects_ticked(tmp_path, monkeypatch):
    from navigator import startup_ui
    from navigator.startup_ui import StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: True)
    calls = []
    monkeypatch.setattr(startup_ui, "background", lambda *a: calls.append(a))
    pane = StartupPane()
    world, r1 = _two_project_world(tmp_path)

    async def run():
        app = Host(pane)
        notes = []
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(app, "notify", lambda msg, **k: notes.append(msg))
            pane.show(world)
            pane.on_menu(f"P|{r1}", "p-open")                # one: a runs, b is ticked; two/d must stay shut
            assert calls == [("navigator.restore", "now", "--only=claude:b")]
            assert notes[0].startswith("▶ opening 1 sessions")
            calls.clear()
            pane.on_ticks("t-open")                          # the logon menu still opens everything ticked
            assert calls == [("navigator.restore", "now")]
            assert notes[1].startswith("▶ opening 2 sessions")

    asyncio.run(run())


def test_restore_now_only_keeps_the_named_sessions(tmp_path, monkeypatch):
    import sys
    from navigator import restore
    r1, r2 = repo(tmp_path, "one"), repo(tmp_path, "two")
    ss = [sess(r1, "b", 0.2, "Beta"), sess(r2, "d", 0.1, "Delta")]
    monkeypatch.setattr(restore, "running_ids", lambda: set())
    monkeypatch.setattr(restore, "has_conversation", lambda s: True)
    assert {r.session.id for r in restore.todo(ss)[0]} == {"b", "d"}
    assert {r.session.id for r in restore.todo(ss, only={"claude:b"})[0]} == {"b"}
    seen = []
    monkeypatch.setattr(restore, "todo", lambda sessions=None, everything=False, only=None: (seen.append(only), ([], []))[1])
    monkeypatch.setattr(restore, "notify", lambda *a: None)
    for argv, want in ((["x", "now"], None), (["x", "now", "--only=claude:b,codex:z"], {"claude:b", "codex:z"})):
        monkeypatch.setattr(sys, "argv", argv)
        restore.main()
        assert seen[-1] == want


def test_menu_key_is_full_stop(tmp_path, monkeypatch):
    from navigator.startup_ui import ContextMenu, StartTable, StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: True)
    assert not [b for b in StartTable.BINDINGS if not isinstance(b, tuple) and b.key == "period"]
    pane = StartupPane()
    world = _world(tmp_path)

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane.show(world)
            pane.query_one(StartTable).focus()
            await pilot.pause()
            await pilot.press(".")
            await pilot.pause()
            assert isinstance(app.screen, ContextMenu)

    asyncio.run(run())


def test_logon_toggle_runs_off_the_ui_thread(tmp_path, monkeypatch):
    import threading
    from navigator.startup_ui import StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: False)
    seen = []
    monkeypatch.setattr(autostart, "install", lambda: seen.append(threading.get_ident()) or "✓ on")
    pane = StartupPane()

    async def run():
        app = Host(pane)
        notes = []
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(app, "notify", lambda msg, **k: notes.append(msg))
            pane.toggle_logon()
            await settle(app, pilot)
            assert seen and seen[0] != threading.get_ident() and notes == ["✓ on"]

    asyncio.run(run())


def test_settings_logon_checkbox_runs_off_the_ui_thread(tmp_path, monkeypatch):
    import threading
    from textual.widgets import Checkbox
    from navigator import accounts, termfont, updater
    from navigator.settings_ui import SettingsPane
    from navigator.startup_ui import StartupPane
    monkeypatch.setattr(autostart, "installed", lambda fresh=False: False)
    monkeypatch.setattr(accounts, "clis", lambda: {})
    monkeypatch.setattr(termfont, "get", lambda: None)
    monkeypatch.setattr(updater, "check", lambda fetch=True: updater.State(True, version="1.0", latest="1.0"))
    seen = []
    monkeypatch.setattr(autostart, "install", lambda: seen.append(threading.get_ident()) or "✓ on")

    class H(App):
        def compose(self):
            yield StartupPane()
            yield SettingsPane("logon")

        def load_world(self):
            pass

        def render_keys(self):
            pass

    async def run():
        app = H()
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            app.query_one("#lg-auto", Checkbox).value = True
            await settle(app, pilot)
            assert seen and seen[0] != threading.get_ident()

    asyncio.run(run())


def test_older_sessions_row_folds_on_left_and_expands_on_right(tmp_path, monkeypatch):
    from navigator.startup_ui import StartTable, StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: True)
    pane = StartupPane()
    world = _world(tmp_path)
    root = world.sessions[0].project.root

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane.show(world)
            assert f"M|{root}" in [k.value for k in pane.query_one(StartTable).rows]
            pane.fold(f"M|{root}", True)
            assert root in pane.full and pane.open_now[root]
            pane.full.clear()
            pane.refresh_rows()
            pane._cursor_to(f"M|{root}")
            pane.query_one(StartTable).focus()
            await pilot.pause()
            await pilot.press("left")
            await pilot.pause()
            assert pane.open_now[root] is False and root not in pane.full
            assert startup.ui_state()["sessions_folded"][root] is True

    asyncio.run(run())


def test_triple_click_opens_once(tmp_path, monkeypatch):
    from navigator.startup_ui import StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: True)
    pane = StartupPane()
    world = _world(tmp_path)
    opened = []
    monkeypatch.setattr(pane, "open_session", lambda k: opened.append(k))

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane.show(world)
            await pilot.pause()
            for _ in range(3):
                await pilot.click("StartTable", offset=(40, 2))   # the first session row, Session column
            await pilot.pause()
            assert len(opened) == 1, opened

    asyncio.run(run())


def test_fold_all_is_disabled_while_searching(tmp_path, monkeypatch):
    from navigator.startup_ui import StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: True)
    pane = StartupPane()
    world = _world(tmp_path)

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            pane.show(world)
            fold = pane.query_one("#st-fold")
            assert not fold.disabled
            pane.query_one("#st-search").value = "alp"
            await pilot.pause()
            assert fold.disabled and "Clear the search" in str(fold.tooltip)
            before = startup.ui_state().get("sessions_folded", {})
            pane.fold_all()                                  # even called directly it saves nothing
            assert startup.ui_state().get("sessions_folded", {}) == before
            pane.query_one("#st-search").value = ""
            await pilot.pause()
            assert not fold.disabled and "Clear" not in str(fold.tooltip)

    asyncio.run(run())


def test_draw_reads_ui_state_once(tmp_path, monkeypatch):
    from navigator.startup_ui import StartupPane
    monkeypatch.setattr(autostart, "installed", lambda: True)
    pane = StartupPane()
    world, _ = _two_project_world(tmp_path)
    n = []
    orig = startup.ui_state
    monkeypatch.setattr(startup, "ui_state", lambda: n.append(1) or orig())

    async def run():
        app = Host(pane)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            n.clear()
            pane.show(world)
            assert len(n) == 1

    asyncio.run(run())


def _stub():
    from textual.widgets import Static
    return Static("")


def test_relaunch_sheet_switching_cli_resets_busy_and_refocuses(tmp_path, monkeypatch):
    from navigator import restore
    from navigator.startup_ui import RelaunchSheet
    proj = NS(label="proj")
    busy = NS(cli="claude", in_herdr=True, session_id="s1", display="Busy", project=proj, status="working")
    other = NS(cli="codex", in_herdr=True, session_id="s2", display="Other", project=proj, status="idle")
    world = NS(agents=[busy, other])
    nothing = {"restart": [], "busy": [], "elsewhere": [], "current": []}
    monkeypatch.setattr(restore, "relaunch_targets",
                        lambda cli, w, panes: nothing if cli == "codex" else {**nothing, "busy": [busy]})

    async def run():
        app = Host(_stub())
        async with app.run_test(size=(140, 40)) as pilot:
            sheet = RelaunchSheet(world)
            await app.push_screen(sheet)
            await pilot.pause()
            await pilot.click("#rls-busy")
            await pilot.pause()
            assert sheet.busy
            assert app.focused.id == "rls-busy"
            sheet.query_one("#rls-cli-codex").press()        # keyboard-style: the focus stays on "busy"
            await pilot.pause()
            assert not sheet.busy
            assert sheet.query_one("#rls-go").disabled and app.focused.id == "rls-cancel"

    asyncio.run(run())


def test_relaunch_sheet_with_nothing_to_do_focuses_cancel(tmp_path, monkeypatch):
    from navigator import restore
    from navigator.startup_ui import RelaunchSheet
    monkeypatch.setattr(restore, "relaunch_targets",
                        lambda *a: {"restart": [], "busy": [], "elsewhere": [], "current": []})

    async def run():
        app = Host(_stub())
        async with app.run_test(size=(140, 40)) as pilot:
            sheet = RelaunchSheet(NS(agents=[]))
            await app.push_screen(sheet)
            await pilot.pause()
            assert app.focused.id == "rls-cancel"

    asyncio.run(run())
