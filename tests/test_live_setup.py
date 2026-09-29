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
    assert live.activity(str(f)) == "⚙ Bash: Run tests"


def test_activity_codex_function_call(tmp_path):
    f = tmp_path / "r.jsonl"
    jl(f, [{"type": "response_item", "payload": {"type": "function_call", "name": "shell",
                                                   "arguments": json.dumps({"command": "ls"})}}])
    assert live.activity(str(f)) == "⚙ shell: ls"


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
    assert (sa.name, sa.kind, sa.model, sa.activity) == ("scout1", "scout", "sonnet", "⚙ Read: a.py")


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
    assert sum(1 for c in cmds if str(c.get("description", "")).startswith("Navigator:")) == len(setup.POPUPS)
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
    assert lines == ["👤 please fix the ledger", "💬 On it.", "⚙ Edit: ledger.py", "👤 codex ask"]
