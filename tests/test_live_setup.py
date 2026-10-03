import json
import os
import time

import pytest
import tomlkit

from navigator import live, projects, sessions, settings, setup


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_PLUGIN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("QWEN_HOME", str(tmp_path / "qwen"))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "gemini"))
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "copilot"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("QWEN_RUNTIME_DIR", raising=False)
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "navigator.toml").write_text("[hide]\npatterns = []\n", encoding="utf-8")
    settings.load.cache_clear()
    projects.clear_cache()
    yield
    settings.load.cache_clear()
    projects.clear_cache()


def jl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_activity_reports_last_tool_call(tmp_path):
    f = tmp_path / "t.jsonl"
    jl(f, [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "thinking out loud"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q", "description": "Run tests"}}]}},
    ])
    assert live.activity(str(f)) == "▸ Bash: Run tests"


def test_activity_codex_function_call(tmp_path):
    f = tmp_path / "r.jsonl"
    jl(f, [{"type": "response_item", "payload": {"type": "function_call", "name": "shell",
                                                   "arguments": json.dumps({"command": "ls"})}}])
    assert live.activity(str(f)) == "▸ shell: ls"


def test_registry_session_with_live_pid_and_subagent(tmp_path):
    proj = tmp_path / "Repo"
    (proj / ".git").mkdir(parents=True)
    tr = tmp_path / "claude" / "projects" / "Repo" / "S1.jsonl"
    jl(tr, [{"type": "user", "cwd": str(proj), "message": {"content": "hello"}}])
    sub = tr.with_suffix("") / "subagents" / "agent-x1.jsonl"
    jl(sub, [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": str(proj / "a.py")}}]}}])
    sub.with_suffix(".meta.json").write_text(json.dumps(
        {"name": "scout1", "description": "find things", "customAgentType": "scout", "model": "sonnet"}))
    old = tr.with_suffix("") / "subagents" / "agent-old.jsonl"
    jl(old, [{"type": "user"}])
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    jl(tmp_path / "claude" / "sessions" / "1.json", [])
    (tmp_path / "claude" / "sessions" / "1.json").write_text(json.dumps(
        {"pid": os.getpid(), "sessionId": "S1", "cwd": str(proj), "name": "LEAD", "status": "busy",
         "kind": "interactive"}))
    (tmp_path / "claude" / "sessions" / "2.json").write_text(json.dumps(
        {"pid": 2 ** 22 + 12345, "sessionId": "DEAD", "cwd": str(proj), "status": "idle"}))
    rr = live.running(sessions.load_sessions(include_hidden=True))
    assert [(r.session_id, r.name, r.status) for r in rr] == [("S1", "LEAD", "busy")]
    [sa] = rr[0].subagents
    assert (sa.name, sa.kind, sa.model, sa.activity) == ("scout1", "scout", "sonnet", "▸ Read: a.py")


def test_codex_child_thread_attaches_to_parent(tmp_path):
    proj = tmp_path / "P"
    (proj / ".git").mkdir(parents=True)
    day = tmp_path / "codex" / "sessions" / "2026" / "09" / "29"
    jl(day / "rollout-p.jsonl", [{"type": "session_meta", "payload": {"id": "P1", "cwd": str(proj)}},
                                 {"type": "event_msg", "payload": {"type": "user_message", "message": "lead"}}])
    jl(day / "rollout-c.jsonl", [{"type": "session_meta", "payload": {
        "id": "C1", "cwd": str(proj), "thread_source": "subagent", "parent_thread_id": "P1"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "child task"}}])
    rr = live.running(sessions.load_sessions(include_hidden=True))
    [r] = [r for r in rr if r.cli == "codex"]
    assert r.session_id == "P1" and [s.description for s in r.subagents] == ["child task"]


def test_setup_merge_keeps_user_settings_and_uninstalls_cleanly():
    user = tomlkit.parse(
        '[ui]\nstatus_indicators = "dots"\ntab_bar_right = [{ type = "hostname" }]\n\n'
        '[keys]\nnext_tab = "prefix+m"\n\n[[keys.command]]\nkey = "prefix+alt+g"\ntype = "popup"\n'
        'command = "lazygit"\n')
    before = tomlkit.dumps(user)
    added: dict = {}
    setup.install(user, added)
    setup.install(user, added)  # idempotent
    doc = tomlkit.parse(tomlkit.dumps(user))
    assert doc["ui"]["status_indicators"] == "dots"            # user value kept
    assert doc["keys"]["next_tab"] == ["prefix+m", "ctrl+alt+n"]  # chord added beside it
    cmds = doc["keys"]["command"]
    assert sum(1 for c in cmds if str(c.get("description", "")).startswith("Navigator:")) == len(setup.POPUPS) + len(setup.MENUS) + len(setup.SHELLS) + len(setup.ACTIONS)
    assert any(c["command"] == "lazygit" for c in cmds)
    bar = doc["ui"]["tab_bar_right"]
    assert [e["type"] for e in bar] == ["hostname", "command"]
    setup.uninstall(user, added)
    after = tomlkit.parse(tomlkit.dumps(user))
    assert after["keys"]["next_tab"] == "prefix+m"
    assert [c["command"] for c in after["keys"]["command"]] == ["lazygit"]
    assert [e["type"] for e in after["ui"]["tab_bar_right"]] == ["hostname"]
    assert "sidebar" not in after["ui"]
    assert tomlkit.parse(before)["ui"]["status_indicators"] == after["ui"]["status_indicators"]


def test_unix_style_paths_resolve(tmp_path):
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    wt = repo / ".claude" / "worktrees" / "feat"
    p = projects.resolve(str(wt))
    assert (p.name, p.worktree) == ("r", "feat")
    assert p.wt_path == os.path.normpath(str(wt))


def test_session_name_prefers_user_name_then_provider_title():
    from navigator import model
    r = live.Running(cli="claude", session_id="s", name="QUALITY-FIX", cwd="", status="idle", user_named=True)
    assert model.session_name(r, None, "Some auto title") == "QUALITY-FIX"
    r.user_named = False  # derived name like "mauri-b6": the session title is more telling
    s = sessions.Session("claude", "s", "", "Ledger rework", "", "", 0, "", named=True)
    assert model.session_name(r, s, "term title") == "Ledger rework"
    assert model.session_name(None, None, "term title") == "term title"
    codex = live.Running(cli="codex", session_id="c", name="Named thread", cwd="", status="active")
    assert model.session_name(codex, None, "") == "Named thread"


def test_external_status_mapping_covers_shell():
    from navigator import model
    assert model.EXTERNAL_STATUS["shell"] == "working"
    assert model.EXTERNAL_STATUS["idle"] == "idle"


def test_pane_neighbor_by_geometry():
    from navigator import panes
    lay = panes.TabLayout("t", "w", (100, 40), False, [
        panes.PaneBox("a", 0, 0, 50, 40, True, "a"),
        panes.PaneBox("b", 50, 0, 50, 20, False, "b"),
        panes.PaneBox("c", 50, 20, 50, 20, False, "c"),
    ])
    assert panes.neighbor(lay, "a", "right") in ("b", "c")
    assert panes.neighbor(lay, "b", "down") == "c"
    assert panes.neighbor(lay, "c", "up") == "b"
    assert panes.neighbor(lay, "b", "left") == "a"
    assert panes.neighbor(lay, "a", "left") is None


def test_presets_are_valid_bsp_trees():
    from navigator import panes

    def leaves(n):
        return 1 if n["type"] == "pane" else leaves(n["first"]) + leaves(n["second"])
    counts = {name: leaves(fn("/x")) for name, fn in panes.PRESETS.items()}
    assert counts == {"2 columns": 2, "3 columns": 3, "2 rows": 2, "2×2 grid": 4, "main + 2 stacked": 3}


def test_mirror_feed_reads_claude_and_codex(tmp_path):
    from navigator import mirror
    f = tmp_path / "c.jsonl"
    jl(f, [
        {"type": "user", "message": {"content": "please fix the ledger"}},
        {"type": "user", "message": {"content": "<command-name>/model</command-name>"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "On it."},
                                                       {"type": "tool_use", "name": "Edit", "input": {"file_path": "/r/ledger.py"}}]}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "codex ask"}},
    ])
    lines = [t.plain for t in mirror.feed(str(f))]
    assert lines == ["👤 please fix the ledger", "💬 On it.", "▸ Edit: ledger.py", "👤 codex ask"]


def test_history_is_mru_and_deduplicated():
    from navigator import history
    for p in ("w1:p1", "w2:p1", "w1:p1", "w3:p2"):
        history.record(p)
    assert [e["pane_id"] for e in history.load()] == ["w3:p2", "w1:p1", "w2:p1"]
    assert [e["pane_id"] for e in history.recent({"w1:p1", "w2:p1"})] == ["w1:p1", "w2:p1"]


def test_history_reads_event_json(monkeypatch):
    from navigator import history
    monkeypatch.setenv("HERDR_PLUGIN_EVENT_JSON", json.dumps({"type": "pane.focused", "data": {"pane_id": "w9:p4"}}))
    history.record()
    assert history.load()[0]["pane_id"] == "w9:p4"


def test_attention_queue_blocked_first_then_oldest():
    from navigator import attention
    snap = {"agents": [
        {"pane_id": "a", "agent_status": "done", "state_change_seq": 1},
        {"pane_id": "b", "agent_status": "blocked", "state_change_seq": 9},
        {"pane_id": "c", "agent_status": "working", "state_change_seq": 0},
        {"pane_id": "d", "agent_status": "blocked", "state_change_seq": 3},
    ]}
    assert [a["pane_id"] for a in attention.queue(snap)] == ["d", "b", "a"]


def test_drop_zones():
    from navigator import panes
    assert panes.zone(0.05, 0.5) == "left"
    assert panes.zone(0.95, 0.5) == "right"
    assert panes.zone(0.5, 0.05) == "up"
    assert panes.zone(0.5, 0.95) == "down"
    assert panes.zone(0.5, 0.5) == "center"


def test_layout_roundtrip_keeps_shape_and_agents():
    from navigator import layouts
    saved = {"type": "split", "direction": "right", "ratio": 0.6,
             "first": {"type": "pane", "cwd": "/r", "agent": {"cli": "claude", "session": "S", "name": "LEAD"}},
             "second": {"type": "pane", "cwd": "/r/wt", "label": "tests"}}
    applied = layouts._for_apply(saved)
    assert applied["ratio"] == 0.6 and "agent" not in applied["first"]
    assert applied["second"]["label"] == "tests"
    assert [l.get("agent", {}).get("name") for l in layouts._leaves(saved)] == ["LEAD", None]


def test_sidebar_width_edits_config_and_rolls_back_on_refusal(tmp_path, monkeypatch):
    from navigator import uiwidth
    cfg = tmp_path / "config.toml"
    cfg.write_text('[ui]\nsidebar_width = 30\nsidebar_max_width = 36\n# keep me\n', encoding="utf-8")
    monkeypatch.setattr(uiwidth, "herdr_config_path", lambda: cfg)

    class R:
        def __init__(self, out):
            self.stdout, self.stderr = out, ""
    calls = []
    monkeypatch.setattr(uiwidth.subprocess, "run",
                        lambda args, **kw: calls.append(args) or R('{"status":"applied"}'))
    assert uiwidth.set_width("+12") == 42
    doc = tomlkit.parse(cfg.read_text(encoding="utf-8"))
    assert doc["ui"]["sidebar_width"] == 42 and doc["ui"]["sidebar_max_width"] == 42
    assert "# keep me" in cfg.read_text(encoding="utf-8")
    assert uiwidth.set_width("-100") == uiwidth.LOW
    monkeypatch.setattr(uiwidth.subprocess, "run", lambda args, **kw: R('{"error":"bad"}'))
    before = cfg.read_text(encoding="utf-8")
    with pytest.raises(RuntimeError):
        uiwidth.set_width("+6")
    assert cfg.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("kind,n", [(k, n) for k in ("columns", "rows", "grid", "main") for n in (2, 3, 4, 5)])
def test_arrange_shapes_have_one_leaf_per_pane(kind, n):
    from navigator import arrange

    def leaves(t):
        return [t["i"]] if t["type"] == "pane" else leaves(t["first"]) + leaves(t["second"])
    tree = arrange.shape(kind, n)
    assert sorted(leaves(tree)) == list(range(n))


def test_arrange_move_plan_builds_grid():
    from navigator import arrange
    plan = []
    arrange._moves(arrange.shape("grid", 4), plan)
    # 4 panes, 3 moves; every moved pane is placed next to an already-placed anchor
    placed = {0}
    for moving, anchor, _ in plan:
        assert anchor in placed and moving not in placed
        placed.add(moving)
    assert placed == {0, 1, 2, 3}


def test_columns_are_equal_thirds():
    from navigator import arrange
    ratios = []
    arrange._paths(arrange.shape("columns", 3), [], ratios)
    assert [round(r, 3) for _, r in ratios] == [0.333, 0.5]


def test_session_lines_nest_sessions_under_their_space():
    from navigator import model, sync
    p = projects.Project("/r", "R")

    def ag(name, status, pane="p1"):
        return model.Agent("claude", status, p, name=name, pane_id=pane)
    # a second tab's session shows under the primary Space, urgent first, tree-drawn
    lines = sync.session_lines("AlgoTrader", [ag("ZED", "idle"), ag("STORAGE", "reply")])
    assert lines == ["├─ ? STORAGE", "└─ ○ ZED"]
    # a worktree Space already named after its only session repeats nothing
    assert sync.session_lines("QUALITY-FIX", [ag("QUALITY-FIX", "idle")]) == []
    # sessions in other windows are marked, and long lists end in "+N more"
    assert sync.session_lines("x", [ag("OUT", "idle", pane="")]) == ["└─ ↗ OUT"]
    many = sync.session_lines("x", [ag(f"S{i}", "idle") for i in range(12)])
    assert len(many) == sync.SESSION_ROWS and many[-1] == "└─ +5 more"


def test_sidebar_rows_fit_herdr_limits():
    assert len(setup.SPACE_ROWS) <= 16 and all(len(r) <= 16 for r in setup.SPACE_ROWS)
    assert sum("$s" in str(r) for r in setup.SPACE_ROWS) == 8


def test_error_watch_sees_only_new_failures():
    from navigator import watch
    before = ["$ pytest", "collected 3 items", "FAILED test_old - boom", "1 failed"]
    # the old failure scrolled up; only what came after the last lines we saw counts
    now = before[1:] + ["$ pytest", "3 passed"]
    assert watch.failures(watch.new_lines(before, now)) == []
    now2 = now + ["Traceback (most recent call last):", "ValueError: bad", "npm ERR! code 1"]
    got = watch.failures(watch.new_lines(now, now2))
    assert got == ["Traceback (most recent call last):", "ValueError: bad", "npm ERR! code 1"]
    assert watch.new_lines([], ["FAILED x"]) == []          # the first read is the baseline
    assert watch.failures(["all good", "0 errors"]) == []


def test_manifest_has_worktree_events_and_new_here_action():
    s = (setup.ROOT / "herdr-plugin.toml").read_text(encoding="utf-8")
    doc = tomlkit.parse(s)
    ons = {e["on"] for e in doc["events"]}
    assert {"worktree.created", "worktree.removed", "worktree.opened"} <= ons
    assert any(a["id"] == "new-here" for a in doc["actions"])


def test_agent_panel_tree_groups_by_project_most_urgent_first():
    from navigator import model, sync
    a_root, b_root = projects.Project("/a", "Alpha"), projects.Project("/b", "Beta")
    wt = projects.Project("/b", "Beta", "lead-3", "/b", "/b/.wt/lead-3")

    def ag(name, status, p, pane):
        return model.Agent("claude", status, p, name=name, pane_id=pane)
    agents = [ag("A1", "idle", a_root, "p1"), ag("A2", "working", a_root, "p4"), ag("B1", "idle", b_root, "p2"),
              ag("B2", "reply", wt, "p3"), ag("OUT", "blocked", a_root, "")]   # not in herdr: not in its panel
    rows = [(x[0].display,) + tuple(x[1:]) for x in sync.agent_tree(agents)]
    assert [r[0] for r in rows] == ["A2", "A1", "B2", "B1"]   # Alpha first: it holds the agent that runs
    assert rows[0][2].startswith("▾ Alpha  ◐1 ○1") and rows[1][2] == "" and rows[2][2].startswith("▾ Beta")
    assert rows[0][3] == "├─ ◐ A2" and rows[1][3] == sync.PAD * 2 + "└─ ○ A1"
    assert rows[2][3] == "├─ ? B2" and rows[3][3] == sync.PAD * 2 + "└─ ○ B1"
    assert rows[2][4] == "" and rows[3][4] == ""  # no worktree row: it read as a duplicate name
    assert [r[1] for r in rows] == ["0000", "0001", "0002", "0003"]
    assert all(ch not in "".join(r[2] + r[3] + r[4] for r in rows) for ch in "⏳✔⚠⎇")   # no wide glyphs
    # folded: only who needs you stays, else the first agent carries the heading
    f = {r[0]: r for r in [(x[0].display,) + tuple(x[1:]) for x in sync.agent_tree(agents, {"/b": True, "/a": True})]}
    assert not f["B2"][5] and f["B1"][5] and f["B2"][2].startswith("▸ Beta") and "+1 folded" in f["B2"][2]
    assert f["B2"][3] == "└─ ? B2"
    assert not f["A2"][5] and f["A1"][5] and f["A2"][2].startswith("▸ Alpha")


def test_rank_puts_running_then_needs_you_then_most_recent():
    from navigator import model
    p = projects.Project("/r", "R")
    ag = lambda n, st, seq, pane="p": model.Agent("claude", st, p, name=n, pane_id=pane, seq=seq)  # noqa: E731
    xs = [ag("old-idle", "idle", 3), ag("new-idle", "idle", 9), ag("run", "working", 1), ag("ask", "blocked", 2),
          ag("out", "idle", 99, pane="")]
    assert [a.name for a in sorted(xs, key=model.rank)] == ["run", "ask", "new-idle", "old-idle", "out"]


def test_folded_space_keeps_only_who_needs_you():
    from navigator import model, sync
    p = projects.Project("/r", "R")
    ag = lambda n, st: model.Agent("claude", st, p, name=n, pane_id="p")  # noqa: E731
    lines = sync.session_lines("R", [ag("A", "idle"), ag("B", "reply"), ag("C", "working")], folded=True)
    assert lines == ["├─ ? B", "└─ +2 folded"]


def test_space_order_moves_repo_blocks_and_keeps_worktrees_under_their_repo():
    from types import SimpleNamespace as NS
    from navigator import model, sync
    a, b = projects.Project("/a", "A"), projects.Project("/b", "B")
    bw = projects.Project("/b", "B", "x", "/b", "/b/.wt/x")
    ws = [NS(id="w1", number=1, project=a, linked_worktree=False),
          NS(id="w2", number=2, project=b, linked_worktree=False),
          NS(id="w3", number=3, project=bw, linked_worktree=True),
          NS(id="w4", number=4, project=projects.Project("/b", "B", "y", "/b", "/b/.wt/y"), linked_worktree=True)]
    agents = [model.Agent("claude", "idle", a, pane_id="w1:p", workspace_id="w1", seq=5),
              model.Agent("claude", "idle", bw, pane_id="w3:p", workspace_id="w3", seq=1),
              model.Agent("claude", "blocked", bw, pane_id="w4:p", workspace_id="w4", seq=2)]
    world = NS(workspaces=ws, agents=agents)
    # B needs you (w4), so its block leads; its own Space first, then w4 before the idle w3
    assert sync.space_order(world) == ["w2", "w4", "w3", "w1"]
    moved = []
    sync.herdr.request, real = (lambda m, p, **k: moved.append((p["workspace_id"], p["insert_index"]))), \
        sync.herdr.request
    try:
        assert sync._sort_spaces(world) == len(moved) and moved[0] == ("w2", 0)
    finally:
        sync.herdr.request = real


def test_bursts_of_sync_hooks_coalesce(tmp_path, monkeypatch):
    from navigator import sync
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(sync.settings, "state_dir", lambda: tmp_path)
    runs = []
    monkeypatch.setattr(sync, "sync", lambda force=False: runs.append(force))
    (tmp_path / "sync.lock").write_text("1")          # another hook is syncing right now
    sync.coalesced()
    assert runs == [] and (tmp_path / "sync.again").exists()
    (tmp_path / "sync.lock").unlink()                  # it finished; the next hook syncs
    sync.coalesced(force=True)
    assert runs == [True] and not (tmp_path / "sync.lock").exists() and not (tmp_path / "sync.again").exists()
    old = time.time() - sync.LOCK_STALE - 5            # a lock left by a crashed sync expires
    (tmp_path / "sync.lock").write_text("1")
    os.utime(tmp_path / "sync.lock", (old, old))
    sync.coalesced()
    assert runs == [True, False]


def test_font_size_writes_windows_terminal_defaults_with_a_backup(tmp_path, monkeypatch):
    from navigator import termfont
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"profiles": {"defaults": {}, "list": [{"name": "x", "font": {"size": 10}}, {"name": "y"}]}}))
    monkeypatch.setattr(termfont, "settings_file", lambda: f)
    assert termfont.get() == termfont.DEFAULT
    assert "14" in termfont.set_size(14)
    d = json.loads(f.read_text())
    assert d["profiles"]["defaults"]["font"]["size"] == 14 and d["profiles"]["list"][0]["font"]["size"] == 14
    assert "font" not in d["profiles"]["list"][1] and (tmp_path / "settings.json.navigator-backup").exists()
    termfont.set_size(99)
    assert termfont.get() == termfont.HIGH


def test_state_json_never_reads_a_broken_file_as_empty_silently(tmp_path):
    from navigator import jsonfile
    f = tmp_path / "startup.json"
    jsonfile.write(f, {"sessions": {"claude:1": {"tick": True}}}, indent=1)
    assert jsonfile.read(f, {})["sessions"]["claude:1"]["tick"] is True
    f.write_text('{"sessions": {"claude:1": {"ti', encoding="utf-8")   # caught mid-write, for good
    assert jsonfile.read(f, {}) == {}
    kept = list(tmp_path.glob("startup.json.unreadable-*"))
    assert kept and kept[0].read_text(encoding="utf-8").startswith('{"sessions"')   # nothing lost
    assert jsonfile.read(tmp_path / "missing.json", None) is None
    assert not list(tmp_path.glob("*.tmp"))   # no temp files left behind


def test_daemon_heartbeat_lock_and_event_routing(tmp_path, monkeypatch):
    from navigator import daemon, hook, jsonfile
    monkeypatch.setattr(daemon.settings, "state_dir", lambda: tmp_path)
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path))
    assert not daemon.alive() and not hook._alive() and not daemon.poke()
    jsonfile.write(tmp_path / "daemon.json", {"pid": 1, "at": time.time()})
    assert daemon.alive() and hook._alive() and daemon.poke() and (tmp_path / "daemon.poke").exists()
    jsonfile.write(tmp_path / "daemon.json", {"pid": 1, "at": time.time() - daemon.STALE - 1})
    assert not daemon.alive() and not hook._alive()
    held = daemon._lock()                      # one daemon per server: a second lock fails
    assert held is not None and daemon._lock() is None
    held.close()
    again = daemon._lock()
    assert again is not None
    again.close()
    d = daemon.Daemon()
    d.want_sync = d.want_status = False
    d.on_event("workspace_focused")            # herdr names events with underscores
    assert d.want_status and not d.want_sync and not d.resubscribe.is_set()
    d.on_event("pane_agent_status_changed")
    assert d.want_sync
    d.on_event("pane_created")                 # a new pane: subscribe to its agent state too
    assert d.resubscribe.is_set()


def test_compact_sends_saved_instructions_only_where_the_cli_takes_them(monkeypatch):
    from navigator import compact
    from types import SimpleNamespace
    fake = SimpleNamespace(compact={"instructions": "Keep the state,\n  findings and   next steps."})
    monkeypatch.setattr(compact, "settings", SimpleNamespace(load=lambda: fake))
    assert compact.command("claude") == "/compact Keep the state, findings and next steps."   # one line
    assert compact.command("codex") == "/compact"
    sent, notes = [], []

    def run(*args, **kw):
        (sent if args[:2] == ("agent", "prompt") else notes).append(args)
        return {}
    monkeypatch.setattr(compact.herdr, "run", run)
    monkeypatch.setattr(compact.herdr, "snapshot", lambda: {"focused_pane_id": "w1:p2", "agents": [
        {"pane_id": "w1:p2", "agent": "claude", "terminal_title_stripped": "LEAD"}]})
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    monkeypatch.delenv("HERDR_WORKSPACE_ID", raising=False)   # tests may run inside a herdr pane
    monkeypatch.setenv("HERDR_PLUGIN_CONTEXT_JSON", "{}")
    compact.main()                                   # the key binding: the focused agent pane
    assert sent == [("agent", "prompt", "w1:p2", "/compact Keep the state, findings and next steps.")]
    assert "LEAD" in notes[-1][2]
    monkeypatch.setenv("HERDR_PANE_ID", "w9:p9")     # a pane with no agent: a hint, nothing sent
    compact.main()
    assert len(sent) == 1 and "Nothing to compact" in notes[-1][2]


def test_right_click_compact_on_a_space_never_picks_another_spaces_pane(monkeypatch):
    from types import SimpleNamespace
    from navigator import compact
    fake = SimpleNamespace(compact={"instructions": "keep it"})
    monkeypatch.setattr(compact, "settings", SimpleNamespace(load=lambda: fake))
    monkeypatch.setattr(compact, "_note_context", lambda ctx: None)
    sent, notes = [], []
    monkeypatch.setattr(compact.herdr, "run", lambda *a, **k: (sent if a[:2] == ("agent", "prompt") else notes).append(a))
    agents = [{"pane_id": "w1:p1", "workspace_id": "w1", "agent": "claude"},
              {"pane_id": "w2:p1", "workspace_id": "w2", "agent": "codex"},
              {"pane_id": "w3:p1", "workspace_id": "w3", "agent": "claude"},
              {"pane_id": "w3:p2", "workspace_id": "w3", "agent": "claude"}]
    monkeypatch.setattr(compact.herdr, "snapshot", lambda: {"agents": agents, "focused_pane_id": "w1:p1"})
    monkeypatch.setenv("HERDR_PLUGIN_CONTEXT_JSON", "{}")
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w2")
    monkeypatch.setenv("HERDR_PANE_ID", "w1:p1")           # the globally focused pane, in another Space
    compact.main()
    assert sent == [("agent", "prompt", "w2:p1", "/compact")]   # w2's own (codex: plain) agent
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w3")
    monkeypatch.setenv("HERDR_PANE_ID", "w3:p9")           # a shell pane; two agents in the Space
    compact.main()
    assert len(sent) == 1 and "2 agents" in notes[-1][2]


def test_pane_menu_actions_map_to_herdr_operations(monkeypatch):
    from navigator import paneact
    calls = []
    monkeypatch.setattr(paneact, "target", lambda: ("w1:p2", "w1:t1"))
    monkeypatch.setattr(paneact.panes, "swap", lambda p, d: calls.append(("swap", p, d)) or "ok")
    monkeypatch.setattr(paneact.panes, "equalize", lambda t: calls.append(("even", t)) or "ok")
    monkeypatch.setattr(paneact.panes, "to_new_tab", lambda p: calls.append(("newtab", p)) or "ok")
    monkeypatch.setattr(paneact.herdr, "run", lambda *a, **k: calls.append(a) or {})
    monkeypatch.setattr("navigator.compact._note_context", lambda ctx: None)
    for op in ("move-left", "move-right", "move-up", "move-down", "even", "newtab", "arrange"):
        paneact.run(op)
    assert calls[:6] == [("swap", "w1:p2", "left"), ("swap", "w1:p2", "right"), ("swap", "w1:p2", "up"),
                         ("swap", "w1:p2", "down"), ("even", "w1:t1"), ("newtab", "w1:p2")]
    assert "NAV_PANE=w1:p2" in calls[6] and "NAV_TAB=panes" in calls[6]


def test_pane_menu_split_and_zoom_and_popup_target(monkeypatch):
    from navigator import paneact
    calls = []
    monkeypatch.setattr(paneact, "target", lambda: ("w1:p2", "w1:t1"))
    monkeypatch.setattr(paneact.panes, "split", lambda p, d: calls.append(("split", p, d)) or "ok")
    monkeypatch.setattr(paneact.panes, "zoom", lambda p: calls.append(("zoom", p)) or "ok")
    monkeypatch.setattr("navigator.compact._note_context", lambda ctx: None)
    for op in ("split-right", "split-down", "zoom"):
        paneact.run(op)
    assert calls == [("split", "w1:p2", "right"), ("split", "w1:p2", "down"), ("zoom", "w1:p2")]


def test_pane_menu_is_bound_and_lists_every_entry():
    from navigator import panemenu
    ops = [op for op, _ in panemenu.ENTRIES if op]
    assert {"compact", "split-right", "split-down", "zoom", "move-left", "even", "newtab", "arrange",
            "new-here", "fold"} <= set(ops)
    keys, mod, width, height, _ = setup.MENUS[0]
    assert mod == "panemenu" and "f5" in keys and height >= len(panemenu.ENTRIES) + 4


def test_tree_rows_use_one_style_for_every_branch():
    # every connector (├─ └─ │) must look the same: no per-state colour, weight or dim on tree tokens
    tree = [c for row in setup.AGENT_ROWS + setup.SPACE_ROWS for c in row
            if isinstance(c, dict) and c.get("token") in ("$line", "$lane", *[f"$s{i}" for i in range(1, 9)])]
    assert len(tree) == 9 and all(c == {"token": c["token"], **setup.TREE} for c in tree)


def test_state_words_have_distinct_colours():
    rules = setup.STATE_TEXT["rules"]
    fg = {r["contains"]: r["fg"] for r in rules}
    assert len({fg["working"], fg["idle"], fg["done"], fg["reply"]}) == 4
    assert [r["contains"] for r in rules].index("reply") < [r["contains"] for r in rules].index("working")
