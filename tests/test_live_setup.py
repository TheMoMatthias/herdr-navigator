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
    assert sum(1 for c in cmds if str(c.get("description", "")).startswith("Navigator:")) == len(setup.POPUPS) + len(setup.SHELLS)
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
    assert lines == ["├ ⏳ STORAGE", "└ ○ ZED"]
    # a worktree Space already named after its only session repeats nothing
    assert sync.session_lines("QUALITY-FIX", [ag("QUALITY-FIX", "idle")]) == []
    # sessions in other windows are marked, and long lists end in "+N more"
    assert sync.session_lines("x", [ag("OUT", "idle", pane="")]) == ["└ ↗ OUT"]
    many = sync.session_lines("x", [ag(f"S{i}", "idle") for i in range(12)])
    assert len(many) == sync.SESSION_ROWS and many[-1] == "└ +5 more"


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
