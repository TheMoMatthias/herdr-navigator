import json
import os
import time

import pytest

from navigator import keys, projects, sessions, settings


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
    monkeypatch.setenv("FACTORY_HOME_OVERRIDE", str(tmp_path / "droid"))
    monkeypatch.setenv("CLINE_DB_DATA_DIR", str(tmp_path / "cline"))
    from navigator import sessions
    monkeypatch.setattr(sessions, "_cursor_home", lambda: tmp_path / "cursor")
    monkeypatch.delenv("QWEN_RUNTIME_DIR", raising=False)
    # pytest's tmp_path is under %TEMP%, which the default config hides
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "navigator.toml").write_text("[hide]\npatterns = []\n", encoding="utf-8")
    settings.load.cache_clear()
    projects.clear_cache()
    yield
    settings.load.cache_clear()
    projects.clear_cache()


def make_repo(root):
    (root / ".git").mkdir(parents=True)
    return root


def test_subdir_resolves_to_repo_root(tmp_path):
    repo = make_repo(tmp_path / "AlgoTrader")
    (repo / "src" / "deep").mkdir(parents=True)
    p = projects.resolve(str(repo / "src" / "deep"))
    assert p.name == "AlgoTrader" and p.worktree == "" and p.path == str(repo)


def test_linked_worktree_folds_into_main_repo(tmp_path):
    repo = make_repo(tmp_path / "Masterpager")
    wt = tmp_path / "Masterpager-a2"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {repo.as_posix()}/.git/worktrees/a2\n")
    p = projects.resolve(str(wt))
    assert p.name == "Masterpager" and p.worktree == "Masterpager-a2"
    assert p.root == projects.key(str(repo))


def test_deleted_claude_worktree_still_folds(tmp_path):
    repo = make_repo(tmp_path / "AlgoTrader")
    gone = repo / ".claude" / "worktrees" / "GOV-1"
    p = projects.resolve(str(gone))
    assert p.name == "AlgoTrader" and p.worktree == "GOV-1"


@pytest.mark.skipif(os.name != "nt", reason="paths are case-sensitive outside Windows")
def test_case_insensitive_grouping_keeps_display_case(tmp_path):
    repo = make_repo(tmp_path / "KettenSteuer")
    a = projects.resolve(str(repo))
    b = projects.resolve(str(repo).upper())
    assert a.root == b.root and a.name == "KettenSteuer"


def test_pinned_project_name_wins(tmp_path):
    cfg = tmp_path / "cfg"
    (cfg / "navigator.toml").write_text(f"[projects]\n'{tmp_path / 'x'}' = \"Pinned\"\n", encoding="utf-8")
    settings.load.cache_clear()
    assert projects.resolve(str(tmp_path / "x" / "y")).name == "Pinned"


def test_hidden_patterns_match_temp_dirs(tmp_path):
    (tmp_path / "cfg" / "navigator.toml").unlink()  # back to the shipped defaults
    settings.load.cache_clear()
    assert projects.is_hidden(r"C:\Users\me\AppData\Local\Temp\claude\x\scratchpad")
    assert not projects.is_hidden(r"C:\Users\me\Documents\AlgoTrader")


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_claude_session_uses_custom_title_and_launch_cwd(tmp_path):
    proj = tmp_path / "work" / "Proj"
    make_repo(proj)
    f = tmp_path / "claude" / "projects" / "C--work-Proj" / "abc.jsonl"
    write_jsonl(f, [
        {"type": "user", "cwd": str(proj), "gitBranch": "main", "entrypoint": "cli",
         "message": {"role": "user", "content": "<command-name>/model</command-name>"}},
        {"type": "user", "cwd": str(proj), "message": {"role": "user", "content": "fix the ledger"}},
        {"type": "user", "cwd": str(tmp_path), "message": {"role": "user", "content": "later cd"}},
        {"type": "custom-title", "customTitle": "LEDGER", "sessionId": "abc"},
    ])
    [s] = sessions.load_sessions()
    assert (s.cli, s.id, s.title, s.cwd) == ("claude", "abc", "LEDGER", str(proj))
    assert s.resume_command() == "claude --resume abc"


def test_claude_first_prompt_skips_command_noise(tmp_path):
    proj = tmp_path / "P"
    make_repo(proj)
    write_jsonl(tmp_path / "claude" / "projects" / "d" / "s1.jsonl", [
        {"type": "user", "cwd": str(proj), "message": {"content": "<command-name>/model</command-name>"}},
        {"type": "user", "cwd": str(proj), "message": {"content": [{"type": "text", "text": "real ask"}]}},
    ])
    [s] = sessions.load_sessions()
    assert s.title == "real ask"


def test_codex_subagents_hidden_and_thread_name_used(tmp_path):
    proj = tmp_path / "P"
    make_repo(proj)
    day = tmp_path / "codex" / "sessions" / "2026" / "09" / "29"
    write_jsonl(day / "rollout-a.jsonl", [
        {"type": "session_meta", "payload": {"id": "T1", "cwd": str(proj), "thread_source": "user"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "first ask"}},
    ])
    write_jsonl(day / "rollout-b.jsonl", [
        {"type": "session_meta", "payload": {"id": "T2", "cwd": str(proj), "thread_source": "subagent"}},
    ])
    write_jsonl(tmp_path / "codex" / "session_index.jsonl", [{"id": "T1", "thread_name": "Named thread"}])
    ss = sessions.load_sessions()
    assert [(s.id, s.title) for s in ss] == [("T1", "Named thread")]
    assert ss[0].resume_command() == "codex resume T1"


def test_cache_is_reused_and_invalidated(tmp_path):
    proj = tmp_path / "P"
    make_repo(proj)
    f = tmp_path / "claude" / "projects" / "d" / "s.jsonl"
    write_jsonl(f, [{"type": "user", "cwd": str(proj), "message": {"content": "one"}}])
    assert sessions.load_sessions()[0].title == "one"
    write_jsonl(f, [{"type": "user", "cwd": str(proj), "message": {"content": "two"}}])
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert sessions.load_sessions()[0].title == "two"


def test_old_sessions_are_excluded(tmp_path):
    proj = tmp_path / "P"
    make_repo(proj)
    f = tmp_path / "claude" / "projects" / "d" / "old.jsonl"
    write_jsonl(f, [{"type": "user", "cwd": str(proj), "message": {"content": "x"}}])
    old = time.time() - 400 * 86400
    os.utime(f, (old, old))
    assert sessions.load_sessions() == []


@pytest.mark.parametrize("raw,want", [
    ("prefix+shift+n", "Ctrl+B › Shift+N"),
    ("ctrl+alt+h", "Ctrl+Alt+H"),
    ("prefix+minus", "Ctrl+B › -"),
    ("alt+1..9", "Alt+1..9"),
    ("f1", "F1"),
])
def test_pretty_keys(raw, want):
    assert keys.pretty(raw, "ctrl+b") == want


def test_pi_sessions_listed_named_and_sorted_with_others(tmp_path):
    proj = make_repo(tmp_path / "work")
    now = time.time()
    pdir = tmp_path / "pi" / "sessions" / "--work--"
    write_jsonl(pdir / "2026-10-01T08-00-00-000Z_P1.jsonl", [
        {"type": "session", "version": 3, "id": "P1", "cwd": str(proj)},
        {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "first ask"}]}},
        {"type": "session_info", "name": "MORNING-WORK"},
        {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "latest ask"}]}},
    ])
    write_jsonl(pdir / "2026-10-01T09-00-00-000Z_P2.jsonl", [
        {"type": "session", "version": 3, "id": "P2", "cwd": str(proj)},
        {"type": "session_info", "name": "function-tester#bae7ea42"},
    ])
    cdir = tmp_path / "claude" / "projects" / "work"
    write_jsonl(cdir / "C1.jsonl", [{"type": "user", "cwd": str(proj), "message": {"content": "claude ask"}}])
    os.utime(pdir / "2026-10-01T08-00-00-000Z_P1.jsonl", (now, now))
    os.utime(cdir / "C1.jsonl", (now - 3600, now - 3600))
    ss = sessions.load_sessions()
    assert [(s.cli, s.id) for s in ss] == [("pi", "P1"), ("claude", "C1")]
    p = ss[0]
    assert p.title == "MORNING-WORK" and p.named
    assert p.last_prompt == "latest ask"
    assert p.resume_command() == "pi --session P1"


def test_opencode_and_kilo_sqlite_sessions(tmp_path):
    import sqlite3
    proj = make_repo(tmp_path / "work")
    now_ms = int(time.time() * 1000)
    for cli, ts in (("opencode", now_ms), ("kilo", now_ms - 60_000)):
        db = tmp_path / "xdg" / cli / f"{cli}.db"
        db.parent.mkdir(parents=True)
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE session (id text, directory text, title text, parent_id text,
                                  time_updated integer, time_archived integer);
            CREATE TABLE message (id text, session_id text, time_created integer, data text);
            CREATE TABLE part (id text, message_id text, session_id text, time_created integer, data text);
        """)
        con.execute("INSERT INTO session VALUES ('S', ?, 'Named', NULL, ?, NULL)", (str(proj), ts))
        con.execute("INSERT INTO session VALUES ('CHILD', ?, 'helper', 'S', ?, NULL)", (str(proj), ts))
        con.execute("INSERT INTO session VALUES ('OLD', ?, 'archived', NULL, ?, 1)", (str(proj), ts))
        for i, txt in enumerate(("first", "latest")):
            con.execute("INSERT INTO message VALUES (?, 'S', ?, ?)", (f"m{i}", i, json.dumps({"role": "user"})))
            con.execute("INSERT INTO part VALUES (?, ?, 'S', 0, ?)", (f"p{i}", f"m{i}", json.dumps({"type": "text", "text": txt})))
        con.commit()
        con.close()
    ss = sessions.load_sessions()
    assert [(s.cli, s.id) for s in ss] == [("opencode", "S"), ("kilo", "S")]
    assert ss[0].title == "Named" and ss[0].last_prompt == "latest"
    assert ss[1].resume_command() == "kilo --session S"


def test_qwen_gemini_copilot_hermes_sessions(tmp_path):
    import sqlite3
    proj = make_repo(tmp_path / "work")
    write_jsonl(tmp_path / "qwen" / "projects" / "work" / "chats" / "Q1.jsonl", [
        {"sessionId": "Q1", "cwd": str(proj), "type": "user", "message": {"role": "user", "parts": [{"text": "qwen ask"}]}},
        {"sessionId": "Q1", "type": "system", "subtype": "custom_title", "systemPayload": {"customTitle": "Qwen named"}},
    ])
    gproj = tmp_path / "gemini" / ".gemini" / "tmp" / "work"
    gproj.mkdir(parents=True)
    (gproj / ".project_root").write_text(str(proj), encoding="utf-8")
    write_jsonl(gproj / "chats" / "session-2026-10-01T08-00-G1.jsonl", [
        {"sessionId": "G1", "kind": "main", "summary": "Gemini named"},
        {"type": "user", "content": [{"text": "gemini ask"}]},
    ])
    write_jsonl(gproj / "chats" / "G1" / "G2.jsonl", [{"sessionId": "G2", "kind": "subagent"}])
    cdir = tmp_path / "copilot" / "session-state" / "C1"
    cdir.mkdir(parents=True)
    (cdir / "workspace.yaml").write_text(f"id: C1\ncwd: '{proj}'\nname: Copilot named\nbranch: main\n", encoding="utf-8")
    (tmp_path / "hermes").mkdir()
    con = sqlite3.connect(tmp_path / "hermes" / "state.db")
    con.executescript("CREATE TABLE sessions (id, source, title, cwd, started_at, ended_at);"
                      "CREATE TABLE messages (session_id, role, content, timestamp);")
    con.execute("INSERT INTO sessions VALUES ('H1', 'cli', 'Hermes named', ?, ?, NULL)", (str(proj), time.time()))
    con.execute("INSERT INTO sessions VALUES ('H2', 'telegram', 'chat', ?, ?, NULL)", (str(proj), time.time()))
    con.execute("INSERT INTO messages VALUES ('H1', 'user', 'hermes ask', ?)", (time.time(),))
    con.commit()
    con.close()
    by = {s.cli: s for s in sessions.load_sessions()}
    assert set(by) == {"qwen", "gemini", "copilot", "hermes"}
    assert by["qwen"].title == "Qwen named" and by["qwen"].last_prompt == "qwen ask"
    assert by["gemini"].id == "G1" and by["gemini"].title == "Gemini named"  # G2 is a hidden sub-agent
    assert by["copilot"].title == "Copilot named" and by["copilot"].branch == "main"
    assert by["hermes"].last_prompt == "hermes ask"
    assert by["hermes"].resume_command() == "hermes --resume H1"


def test_cli_commands_go_over_the_socket_and_fall_back_to_the_cli(monkeypatch):
    from navigator import herdr
    assert herdr._to_api(("agent", "read", "w1:p1", "--source", "recent-unwrapped", "--lines", "40"))[:2] == (
        "agent.read", {"target": "w1:p1", "source": "recent_unwrapped", "lines": 40})
    assert herdr._to_api(("pane", "process-info", "--pane", "w1:p2"))[:2] == ("pane.process_info", {"pane_id": "w1:p2"})
    assert herdr._to_api(("tab", "rename", "w1:t1", "my", "tab"))[:2] == ("tab.rename", {"tab_id": "w1:t1", "label": "my tab"})
    assert herdr._to_api(("notification", "show", "T", "--body", "B", "--sound", "request"))[:2] == (
        "notification.show", {"title": "T", "body": "B", "sound": "request"})
    assert herdr._to_api(("agent", "send-keys", "w1:p1", "Enter"))[:2] == ("agent.send_keys", {"target": "w1:p1", "keys": ["Enter"]})
    assert herdr._to_api(("pane", "move", "w1:p1", "--tab", "w1:t2")) is None      # layout moves: the CLI
    assert herdr._to_api(("pane", "list", "--bogus", "x")) is None                 # an unmapped option: the CLI
    calls = []
    monkeypatch.setattr(herdr, "request", lambda m, p, timeout=10: {"read": {"text": "\nhello\n"}})
    assert herdr.run("pane", "read", "w1:p1", "--source", "recent") == {"raw": "hello"}

    def refused(m, p, timeout=10):
        raise herdr.HerdrError('{"code": "invalid_request"}')
    monkeypatch.setattr(herdr, "request", refused)
    monkeypatch.setattr(herdr.subprocess, "run", lambda a, **k: calls.append(a) or
                        type("P", (), {"returncode": 0, "stdout": '{"result": {"ok": 1}}', "stderr": ""})())
    assert herdr.run("agent", "get", "w1:p1") == {"ok": 1} and calls      # refused: the CLI decides

    def silent(m, p, timeout=10):
        raise herdr.HerdrError("herdr did not answer agent.prompt within 10s")
    calls.clear()
    monkeypatch.setattr(herdr, "request", silent)
    assert herdr.run("agent", "prompt", "w1:p1", "hi", check=False) == {} and not calls  # may have acted: never twice


def test_herdr_default_keys_are_asked_once_per_binary(tmp_path, monkeypatch):
    from navigator import keys
    exe = tmp_path / "herdr.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(keys.herdr, "herdr_bin", lambda: str(exe))
    asked = []
    monkeypatch.setattr(keys, "_ask_defaults", lambda: asked.append(1) or {"goto": ["prefix+g"]})
    assert keys._defaults() == {"goto": ["prefix+g"]}
    assert keys._defaults() == {"goto": ["prefix+g"]}
    assert len(asked) == 1
    exe.write_bytes(b"xy")  # herdr updated: ask again
    keys._defaults()
    assert len(asked) == 2


def test_duplicate_plain_spaces_join_the_git_space():
    from types import SimpleNamespace as NS
    from navigator import merge
    repo = NS(id="w6", label="AlgoTrader", number=2, cwd="C:/Repo", linked_worktree=False, git=True, tabs=[])
    dup = NS(id="w1F", label="AlgoTrader", number=3, cwd="c:/repo/", linked_worktree=False, git=False, tabs=[])
    wt = NS(id="wT", label="LEAD", number=4, cwd="C:/Repo/.claude/worktrees/x", linked_worktree=True, git=True, tabs=[])
    home = NS(id="w1", label="home", number=1, cwd="C:/Users", linked_worktree=False, git=False, tabs=[])
    pairs = merge.duplicates(NS(workspaces=[home, dup, repo, wt]))
    assert [(d.id, k.id) for d, k in pairs] == [("w1F", "w6")]
    assert merge.duplicates(NS(workspaces=[home, repo, wt])) == []


def test_merge_moves_each_tab_and_closes_the_duplicate(monkeypatch):
    from types import SimpleNamespace as NS
    from navigator import herdr, merge
    repo = NS(id="w6", label="A", number=2, cwd="C:/Repo", linked_worktree=False, git=True, focused=False, tabs=[])
    dup = NS(id="w9", label="A", number=3, cwd="C:/Repo", linked_worktree=False, git=False, focused=True,
             tabs=[{"tab_id": "w9:t1", "label": "one"}, {"tab_id": "w9:t2", "label": "two"}])
    snap = {"panes": [{"pane_id": "w9:p1", "tab_id": "w9:t1", "workspace_id": "w9"},
                      {"pane_id": "w9:p2", "tab_id": "w9:t2", "workspace_id": "w9"},
                      {"pane_id": "w9:p3", "tab_id": "w9:t2", "workspace_id": "w9"}]}
    calls = []

    def req(method, params, timeout=0):
        calls.append((method, params))
        return {"pane": {"tab_id": "w6:t9"}} if method == "pane.get" else {}
    monkeypatch.setattr(herdr, "request", req)
    monkeypatch.setattr(herdr, "snapshot", lambda: {"panes": []})
    done = merge.merge(NS(workspaces=[repo, dup], snapshot=snap))
    moves = [(p["pane_id"], p["destination"]) for m, p in calls if m == "pane.move"]
    assert moves[0] == ("w9:p1", {"type": "new_tab", "workspace_id": "w6", "label": "one"})
    assert moves[2] == ("w9:p3", {"type": "tab", "tab_id": "w6:t9", "split": "right"})
    assert ("workspace.close", {"workspace_id": "w9"}) in calls and len(done) == 1


def test_user_paths_follow_relocated_cli_homes(monkeypatch, tmp_path):
    from pathlib import Path
    from navigator import keys, settings, setup
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert settings.user_path("~/.claude/.credentials.json") == tmp_path / "cc" / ".credentials.json"
    assert settings.user_path("~/.claude.json") == tmp_path / "cc" / ".claude.json"
    assert settings.user_path("~/.codex/auth.json") == Path.home() / ".codex" / "auth.json"
    monkeypatch.setenv("HERDR_CONFIG_PATH", str(tmp_path / "h.toml"))
    assert setup.herdr_config_path() == keys.config_path() == tmp_path / "h.toml"


def test_cli_compat_helpers(monkeypatch, tmp_path):
    from navigator import compact, model, sessions
    from navigator.sessions import Session
    # pi/omp report the session file: it maps to the id read from that file
    p = str(tmp_path / "s.jsonl")
    assert model.session_ref({"kind": "path", "value": p}, {os.path.normcase(p): "abc"}) == "abc"
    assert model.session_ref({"kind": "id", "value": "x1"}, {}) == "x1" and model.session_ref(None, {}) == ""
    # a shared database's mtime moves with every session: the session's own update counts
    db = Session(cli="opencode", id="o", cwd="", title="", last_prompt="", branch="", mtime=5.0, path=str(tmp_path / "o.db"))
    assert model.last_write(db, db.path) == 5.0
    # Gemini and Qwen compact with /compress; only Claude takes instructions
    monkeypatch.setattr(compact, "instructions", lambda: "keep it short")
    assert compact.command("gemini") == "/compress" and compact.command("codex") == "/compact"
    assert compact.command("claude") == "/compact keep it short"
    assert sessions._codex_request("# Context from my IDE\n## My request for Codex:\nfix it") == "\nfix it"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    (tmp_path / "opencode").mkdir()
    (tmp_path / "opencode" / "opencode-stable.db").write_bytes(b"")
    assert sessions._newest_db("opencode").name == "opencode-stable.db"
    assert sessions._newest_db("kilo").name == "kilo.db"
