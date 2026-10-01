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
