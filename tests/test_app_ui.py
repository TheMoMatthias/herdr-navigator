"""The Navigator's own screen (navigator/app.py): declutter pass of 2026-10-12. A fake world is
mounted with Textual's pilot, so no herdr is needed."""
import asyncio
from pathlib import Path

import pytest
from textual.widgets import Button, DataTable, Static

from navigator import asks, insight, model, projects, settings


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


def fake_world(tmp_path):
    root = tmp_path / "proj"
    wt_dir = tmp_path / "proj-wt"
    wt_dir.mkdir(parents=True)
    main = projects.Project(projects.key(str(root)), "proj", "", str(root))
    wt_proj = projects.Project(main.root, "proj", "feat", str(root), str(wt_dir))
    ask = model.Agent(cli="claude", status="blocked", project=main, name="asker", pane_id="w1:p1",
                      question=asks.Question("Proceed?", ["Yes", "No"]), context=insight.Context(120_000, 200_000),
                      waiting_since=1.0)
    on_wt = model.Agent(cli="codex", status="idle", project=wt_proj, name="on-wt", pane_id="w1:p2")
    outside = model.Agent(cli="claude", status="working", project=main, name="elsewhere", pid=4)
    wt = model.Worktree("feat", str(wt_dir), agents=[on_wt])
    view = model.ProjectView(main, agents=[ask, outside], worktrees={"feat": wt}, in_sidebar=True)
    view.worktrees["feat"].agents = [on_wt]
    return model.World([view], [ask, on_wt, outside], [], [], {}, "", {})


def run_app(tmp_path, monkeypatch, scenario):
    import navigator.app as A
    world = fake_world(tmp_path)
    monkeypatch.setattr(model, "build", lambda *a, **k: world)
    monkeypatch.setattr(A.Navigator, "ensure_daemon", lambda self: None)
    monkeypatch.setattr(model.herdr, "snapshot", lambda: (_ for _ in ()).throw(OSError("no herdr")))
    monkeypatch.setattr(model.herdr, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no herdr")))
    monkeypatch.setattr(A.sync, "spawn_background", lambda *a, **k: None)

    async def go():
        app = A.Navigator("projects")
        async with app.run_test(size=(150, 46)) as pilot:
            await pilot.pause(0.5)
            await scenario(app, pilot)
    asyncio.run(go())


def test_every_tab_and_its_help_open(tmp_path, monkeypatch):
    async def scenario(app, pilot):
        for tab in ("projects", "agents", "sessions", "panes", "usage", "settings"):
            app.action_tab(tab)
            await pilot.pause(0.3)
            await pilot.press("question_mark")
            await pilot.pause(0.3)
            assert type(app.screen).__name__ == "HelpScreen", tab
            await pilot.press("escape")
            await pilot.pause(0.2)
        await app.open_settings("keys")
        await pilot.pause(0.3)
        keys = str(app.query_one("#keys-body").render())
        assert "right-click pane / space" in keys          # the herdr mouse rows stay
        assert "Sessions: right-click" not in keys          # the per-tab repeats are in ? help
    run_app(tmp_path, monkeypatch, scenario)


def test_projects_tab_is_decluttered_but_keeps_every_action(tmp_path, monkeypatch):
    async def scenario(app, pilot):
        ids = {b.id for b in app.query(Button)}
        assert {"btn-go", "btn-resume", "btn-sidebar", "btn-layout", "btn-wt-finish"}.isdisjoint(ids)
        assert "btn-new" in ids
        assert app.query_one("#proj-filter").placeholder == "filter  /"
        assert [str(c.label) for c in app.query_one("#proj-table", DataTable).ordered_columns][1] == ""
        table = app.query_one("#proj-table", DataTable)
        assert "●" in str(table.get_row_at(0)[1])                 # in sidebar: a dot, not a box
        assert "◀ here" not in str(table.get_row_at(0)[2])
        # every removed button stays in the row menu, on a project row and on a worktree row
        for key, expect in ((app.world.projects[0].project.root, {"open", "sessions", "sidebar", "layout", "new"}),
                            (app.world.projects[0].project.root + "|wt|feat", {"open", "sessions", "sidebar", "layout", "finish"})):
            app.selected_key = key
            assert expect <= {k for k, _ in app.project_menu_items()}
        # the keys that replace the buttons are bound
        bound = {b.action for b in app.BINDINGS}
        assert {"open", "resume_project", "toggle_sidebar", "restore_layout", "finish_worktree"} <= bound
        info = str(app.query_one("#proj-info", Static).render())
        assert "Click a row twice" not in info
        assert info.splitlines()[0].replace("\\", "/").endswith("proj")  # the path leads; no name above it
    run_app(tmp_path, monkeypatch, scenario)


def test_agents_tab_single_bottom_row_and_menus(tmp_path, monkeypatch):
    async def scenario(app, pilot):
        app.action_tab("agents")
        await pilot.pause(0.3)
        ids = {b.id for b in app.query(Button)}
        assert "btn-relaunch-all" not in ids and {"act-compact", "act-prompts", "act-handoff", "mark-clear"}.isdisjoint(ids)
        row = [w.id for w in app.query("#agent-send > *")]
        assert row == ["agent-msg", "send-msg", "act-more"]
        assert not app.query("#agent-keys") and app.query_one("#agent-msg").placeholder == "Message  m"
        t = app.query_one("#agent-table", DataTable)
        assert str(t.ordered_columns[0].label) == ""
        a = next(a for a in app.world.agents if a.name == "asker")
        # Compact, Hand off and Prompts are in the agent menu as well as under the ☰ ▾ button
        keys = {k for k, _ in app.agent_menu_items(a)}
        assert {"compact", "handoff", "prompts", "relaunch", "goto"} <= keys
        assert all("☐" not in lbl and "☑" not in lbl for k, lbl in app.agent_menu_items(a) if k == "mark")
        # selecting for Send shows a dot, not a box
        app.marked = {"w1:p1"}
        app.fill_agents()
        cells = [str(t.get_row_at(i)[0]) for i in range(t.row_count)]
        assert "●" in cells and not any(c in ("☑", "☐") for c in cells)
        assert str(app.query_one("#send-msg", Button).label) == "Send to 1"
        # Where says only which linked worktree; "main" is not worth a word
        assert str(t.get_row("w1:p1")[6]) == "" and str(t.get_row("w1:p2")[6]) == "⎇ feat"
        assert "worktree" not in str(app.query_one("#agent-bar").render())
        await pilot.press("question_mark")
        await pilot.pause(0.3)
        await pilot.press("escape")
    run_app(tmp_path, monkeypatch, scenario)


def test_topbar_shows_only_project_and_what_needs_you(tmp_path, monkeypatch):
    async def scenario(app, pilot):
        text = str(app.query_one("#topbar", Static).render())
        assert "⚠ 1 need you" in text
        for gone in ("working", "parked", "other windows", "in the background", "filling up"):
            assert gone not in text
    run_app(tmp_path, monkeypatch, scenario)


def test_layout_tab_drops_swap_row_and_pane_ids(tmp_path, monkeypatch):
    async def scenario(app, pilot):
        app.action_tab("panes")
        await pilot.pause(0.3)
        ids = {b.id for b in app.query(Button)}
        assert not any(i.startswith("pop-swap-") for i in ids) and "pop-resize-left" in ids
        labels = {b.id: str(b.label) for b in app.query(Button)}
        assert labels["layout-save"] == "Save" and labels["layout-delete"] == "Delete"
        assert labels["pane-watch"] == "⚠ Watch errors" and labels["pop-focus"].startswith("⇥ Open")
        # swapping stays on the keyboard (Shift+arrows) and on the map (drag)
        assert {b.key for b in app.BINDINGS if b.action.startswith("pane_op('swap'")} == {
            "shift+left", "shift+right", "shift+up", "shift+down"}
    run_app(tmp_path, monkeypatch, scenario)


def test_help_texts_are_short_and_name_the_current_ui():
    import navigator.app as A
    words = sum(len(body.split()) for _, body in A.HELP.values())
    assert words <= 330                                  # was 543
    assert "Ticks ▾" not in A.HELP["sessions"][1] and "▦ Layout" not in A.HELP["panes"][1]
    assert "yellow 70 %, red 85 %" in A.HELP["agents"][1]
    assert "Click a row to select, click again or Enter" in A.HELP["projects"][1]
    # one name for Enter, one for the state
    assert all(b.description != "Go" for b in A.Navigator.BINDINGS)
    assert not any(b.key == "d" for b in A.Navigator.BINDINGS)


def test_agents_group_folds_and_unfolds_on_every_click(tmp_path, monkeypatch):
    async def scenario(app, pilot):
        app.action_tab("agents")
        await pilot.pause(0.4)
        t = app.query_one("#agent-table", DataTable)
        full = t.row_count
        seen = []
        for _ in range(4):
            await pilot.click("#agent-table", offset=(1, 1))
            await pilot.pause(0.3)
            seen.append(t.row_count)
        assert seen == [seen[0], full, seen[0], full] and seen[0] < full, seen
    run_app(tmp_path, monkeypatch, scenario)


# ---- audit fixes (2026-10-12) ------------------------------------------------------------------
def _spy_notify(app):
    seen = []
    app.notify = lambda msg, *a, **k: seen.append(str(msg))
    return seen


def _pick(app, item_id):
    from textual.widgets import OptionList
    ol = app.screen.query_one(OptionList)
    ol.highlighted = ol.get_option_index(item_id)


def test_esc_in_empty_pane_rename_box_hides_it_not_the_navigator(tmp_path, monkeypatch):
    import navigator.app as A
    exits = []
    monkeypatch.setattr(A.Navigator, "exit", lambda self, *a, **k: exits.append(1))

    async def scenario(app, pilot):
        app.action_tab("panes")
        await pilot.pause(0.3)
        app.action_pane_rename()
        await pilot.pause(0.2)
        inp = app.query_one("#pane-rename")
        assert inp.has_class("show") and app.focused is inp
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not exits and not inp.has_class("show") and inp.value == ""
        assert app.focused is app.query_one("#pane-map")
    run_app(tmp_path, monkeypatch, scenario)


def test_watch_errors_label_and_press_use_one_live_source_and_only_error_watches(tmp_path, monkeypatch):
    from navigator import panes, watch
    done = {"kind": "done", "pane": "w1:p1"}
    errs = {"kind": "errors", "pane": "w1:p1"}
    live = {"d1": done}
    monkeypatch.setattr(watch, "load", lambda: {"d1": done, "e1": errs})  # stale file: e1 is dead
    monkeypatch.setattr(watch, "active", lambda: dict(live))
    cancelled, started = [], []
    monkeypatch.setattr(watch, "cancel", lambda k: cancelled.append(k))
    monkeypatch.setattr(watch, "start", lambda *a, **k: started.append(a) or "started")
    lay = panes.TabLayout("t1", "w1", (80, 24), False, [panes.PaneBox("w1:p1", 0, 0, 80, 24, True, "shell")])

    async def scenario(app, pilot):
        app.action_tab("panes")
        await pilot.pause(0.3)
        app.show_panes(lay)
        btn = app.query_one("#pane-watch")
        assert str(btn.label) == "⚠ Watch errors"  # a "done" watch is not an error watch
        live.update({"e1": errs})
        app.show_panes(lay)
        assert str(btn.label) == "✕ Stop error watch"
        btn.press()
        await pilot.pause(0.2)
        assert cancelled == ["e1"] and not started  # the "done" watch d1 stays
    run_app(tmp_path, monkeypatch, scenario)


def test_second_click_in_another_cell_of_the_same_row_opens(tmp_path, monkeypatch):
    import navigator.app as A
    picked = []
    monkeypatch.setattr(A.ClickTwiceTable, "_post_selected_message", lambda self: picked.append(self.cursor_coordinate))

    async def scenario(app, pilot):
        app.action_tab("agents")
        await pilot.pause(0.4)
        t = app.query_one("#agent-table")
        await pilot.click("#agent-table", offset=(30, 1))
        await pilot.pause(0.2)
        first = t.cursor_coordinate
        assert not picked
        await pilot.click("#agent-table", offset=(70, 1))
        await pilot.pause(0.2)
        assert t.cursor_coordinate.row == first.row and t.cursor_coordinate.column != first.column
        assert len(picked) == 1, picked
    run_app(tmp_path, monkeypatch, scenario)


def test_close_pane_confirmation_expires_after_five_seconds(tmp_path, monkeypatch):
    import time
    import navigator.app as A
    ran = []
    monkeypatch.setattr(A.Navigator, "run_pane_op", lambda self, op, arg="": ran.append(op))

    async def scenario(app, pilot):
        seen = _spy_notify(app)
        app.pane_selected = "w1:p1"
        app.action_pane_op("close")
        assert ran == [] and len(seen) == 1
        app.action_pane_op("close")  # inside the window: confirms
        assert ran == ["close"]
        app.action_pane_op("close")  # armed again
        app.close_armed_at = time.monotonic() - 6  # the window passed
        app.action_pane_op("close")
        assert ran == ["close"] and len(seen) == 3, (ran, seen)
    run_app(tmp_path, monkeypatch, scenario)


def test_ctrl_c_asks_once_from_the_row_menu_and_from_the_more_menu(tmp_path, monkeypatch):
    import navigator.app as A
    from navigator.startup_ui import ContextMenu
    sent = []
    monkeypatch.setattr(A.Navigator, "run_send", lambda self, pane, name, text, key: sent.append((pane, key)))

    async def scenario(app, pilot):
        app.action_tab("agents")
        await pilot.pause(0.3)
        asker = next(a for a in app.world.agents if a.pane_id == "w1:p1")
        app.on_agent_menu(asker, "stop")  # row menu
        await pilot.pause(0.2)
        assert isinstance(app.screen, ContextMenu) and sent == []
        _pick(app, "go")
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert sent == [("w1:p1", "ctrl+c")]
        sent.clear()
        app.marked = {"w1:p1", "w1:p2"}  # the ☰ ▾ menu acts on all marked agents
        app.more_menu(app.query_one("#act-more"))
        await pilot.pause(0.2)
        _pick(app, "key:ctrl+c")
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert isinstance(app.screen, ContextMenu) and sent == []
        _pick(app, "go")
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert sorted(sent) == [("w1:p1", "ctrl+c"), ("w1:p2", "ctrl+c")]
    run_app(tmp_path, monkeypatch, scenario)


def test_fold_click_on_a_row_without_an_arrow_does_not_fold(tmp_path, monkeypatch):
    async def scenario(app, pilot):
        t = app.query_one("#proj-table")
        folds = []
        orig = t.post_message
        t.post_message = lambda m: (folds.append(m) if isinstance(m, type(t).Fold) else None) or orig(m)
        blank = next(i for i in range(t.row_count) if not str(t.get_row_at(i)[0]).strip())
        arrow = next(i for i in range(t.row_count) if str(t.get_row_at(i)[0]).strip())
        await pilot.click("#proj-table", offset=(1, 1 + blank))
        await pilot.pause(0.2)
        assert folds == []
        await pilot.click("#proj-table", offset=(1, 1 + arrow))
        await pilot.pause(0.2)
        assert len(folds) == 1
    run_app(tmp_path, monkeypatch, scenario)


def test_agent_filter_ignores_folded_groups(tmp_path, monkeypatch):
    from navigator import startup

    async def scenario(app, pilot):
        app.action_tab("agents")
        await pilot.pause(0.3)
        root = app.world.agents[0].project.root
        startup.set_ui("agents_folded", {root: True})
        app.fill_agents()
        assert not any(a.name == "elsewhere" for a, _ in app.agent_rows.values())  # folded: only needs-you rows
        app.action_filter("working")
        await pilot.pause(0.2)
        assert any(a.name == "elsewhere" for a, _ in app.agent_rows.values())
    run_app(tmp_path, monkeypatch, scenario)


def test_new_agent_menu_without_a_project_says_so(tmp_path, monkeypatch):
    from navigator.startup_ui import ContextMenu

    async def scenario(app, pilot):
        seen = _spy_notify(app)
        app.selected_key = None
        app.new_menu(app.query_one("#btn-new"))
        await pilot.pause(0.2)
        assert not isinstance(app.screen, ContextMenu) and seen == ["Select a project first."]
    run_app(tmp_path, monkeypatch, scenario)


def test_new_anywhere_starts_in_home_and_launches_in_the_folder_chosen(tmp_path, monkeypatch):
    import os
    import navigator.app as A
    from navigator.startup_ui import NewSession
    started = []
    monkeypatch.setattr(A.launch, "new_session", lambda world, p, folder, cli, name, prefs, branch, focus=True:
                        started.append((p.root, folder, cli, name)) or "ok")

    async def scenario(app, pilot):
        for bid in ("#btn-new-any", "#btn-new-any-a", "#btn-new-proj-a"):
            assert app.query(bid), bid
        app.new_anywhere()
        await pilot.pause(0.3)
        assert isinstance(app.screen, NewSession)
        assert app.screen.query_one("#ns-folder").value == os.path.expanduser("~")
        app.screen.query_one("#ns-folder").value = str(tmp_path)
        app.screen.query_one("#ns-name").value = "scratch"
        app.screen._go()
        await pilot.pause(0.5)
        assert started and started[0][1] == str(tmp_path) and started[0][3] == "scratch"
    run_app(tmp_path, monkeypatch, scenario)


def test_open_folder_opens_the_typed_project_as_a_space(tmp_path, monkeypatch):
    import navigator.app as A
    from navigator.startup_ui import Prompt
    opened = []
    monkeypatch.setattr(A.launch, "goto_project", lambda world, p, wt="": opened.append(p.root) or "ok")

    async def scenario(app, pilot):
        for bid in ("#btn-open-dir", "#btn-open-dir-a"):
            assert app.query(bid), bid
        seen = _spy_notify(app)
        app.open_folder()
        await pilot.pause(0.3)
        assert isinstance(app.screen, Prompt)
        app.screen.dismiss(str(tmp_path / "nope"))
        await pilot.pause(0.3)
        assert not opened and "is not a folder" in seen[0]
        app.open_folder()
        await pilot.pause(0.3)
        app.screen.dismiss(str(tmp_path))
        await pilot.pause(0.6)
        assert len(opened) == 1
    run_app(tmp_path, monkeypatch, scenario)


def test_saved_layouts_use_the_selected_project_and_a_first_click_does_not_restore(tmp_path, monkeypatch):
    import navigator.app as A
    monkeypatch.setattr(A.layouts, "saved", lambda p: {"two": {"root": {}, "saved_at": 1.0}})
    monkeypatch.setattr(A.layouts, "_leaves", lambda r: [1, 2])
    ops = []
    monkeypatch.setattr(A.Navigator, "layout_op", lambda self, op, name: ops.append((op, name)))

    async def scenario(app, pilot):
        app.action_tab("panes")
        await pilot.pause(0.3)
        app.selected_key = app.world.projects[0].project.root
        assert app.pane_layout is None and app.saved_project() is not None
        app.fill_saved()
        assert app.query_one("#saved-table").row_count == 1
        await pilot.pause(0.3)
        await pilot.click("#saved-table", offset=(2, 1))
        await pilot.pause(0.2)
        assert ops == []
        await pilot.click("#saved-table", offset=(2, 1))
        await pilot.pause(0.2)
        assert ops == [("restore", "two")]
    run_app(tmp_path, monkeypatch, scenario)


def test_herdr_menu_entries_open_the_matching_dialog(tmp_path, monkeypatch):
    from navigator import newhere
    from navigator.startup_ui import NewSession, Prompt
    calls = []
    monkeypatch.setattr(newhere.herdr, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w9")
    for mode in ("", "any", "open"):
        newhere.main(mode)
    assert [c[-1] for c in calls] == ["NAV_NEW=w9", "NAV_NEWANY=1", "NAV_OPENDIR=1"]
    manifest = (Path(newhere.__file__).resolve().parent.parent / "herdr-plugin.toml").read_text(encoding="utf-8")
    for needle in ('"newhere", "any"]', '"newhere", "open"]', "new session in any folder", "open a project folder"):
        assert needle in manifest, needle

    for var, screen in (("NAV_NEWANY", NewSession), ("NAV_OPENDIR", Prompt)):
        monkeypatch.setenv(var, "1")

        async def scenario(app, pilot, screen=screen):
            await pilot.pause(0.5)
            assert isinstance(app.screen, screen) and app.new_only
        run_app(tmp_path / var, monkeypatch, scenario)
