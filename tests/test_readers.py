"""Readers for Droid, Cursor Agent and Cline, against fixtures shaped like their real stores."""
import hashlib
import json
import os
import sqlite3
import time

import pytest

from navigator import projects, sessions, settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k, d in [("HERDR_PLUGIN_CONFIG_DIR", "cfg"), ("HERDR_PLUGIN_STATE_DIR", "state"),
                 ("CLAUDE_CONFIG_DIR", "claude"), ("CODEX_HOME", "codex"), ("PI_CODING_AGENT_DIR", "pi"),
                 ("XDG_DATA_HOME", "xdg"), ("QWEN_HOME", "qwen"), ("GEMINI_CLI_HOME", "gemini"),
                 ("COPILOT_HOME", "copilot"), ("HERMES_HOME", "hermes"), ("FACTORY_HOME_OVERRIDE", "fhome"),
                 ("CLINE_DIR", "cline")]:
        monkeypatch.setenv(k, str(tmp_path / d))
    for k in ("QWEN_RUNTIME_DIR", "CLINE_DATA_DIR", "CLINE_DB_DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sessions, "_cursor_home", lambda: tmp_path / "cursor")
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "navigator.toml").write_text("[hide]\npatterns = []\n", encoding="utf-8")
    settings.load.cache_clear()
    projects.clear_cache()
    yield
    settings.load.cache_clear()
    projects.clear_cache()


def jsonl(path, *recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


def user(text):
    return {"type": "message", "id": "m", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def test_droid_session_titled_resumable_and_subagent_flagged(tmp_path):
    repo = str(tmp_path / "repo")
    d = tmp_path / "fhome" / ".factory" / "sessions" / "-tmp-repo"
    sid = "30b34828-e462-45c9-b63c-bfefb6bd178f"
    jsonl(d / f"{sid}.jsonl",
          {"type": "session_start", "id": sid, "title": "fix the bug", "sessionTitle": "Bug fix",
           "isSessionTitleManuallySet": True, "version": 2, "cwd": repo},
          user("<system-reminder>ctx</system-reminder>"), user("fix the bug"),
          {"type": "message", "id": "a", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
          user("now add a test"))
    jsonl(d / "sub.jsonl", {"type": "session_start", "id": "sub", "title": "t", "callingSessionId": sid, "cwd": repo},
          user("helper task"))
    jsonl(d / "broken.jsonl", {"type": "message"})  # no session_start: skipped, never fatal
    (d / f"{sid}.settings.json").write_text("{}", encoding="utf-8")
    by = {s.id: s for s in sessions.load_sessions(include_hidden=True)}
    s = by[sid]
    assert (s.cli, s.cwd, s.title, s.named, s.last_prompt, s.subagent) == \
        ("droid", repo, "Bug fix", True, "now add a test", False)
    assert s.resume_command() == f"droid --resume {sid}"
    assert by["sub"].subagent and by["sub"].title == "helper task"
    assert set(by) == {sid, "sub"}


def test_cursor_cwd_from_chats_meta_then_trusted_then_dirname(tmp_path):
    cur = tmp_path / "cursor"
    meta_cwd = str(tmp_path / "meta-repo")
    p1 = cur / "projects" / "whatever" / "agent-transcripts" / "c1.jsonl"
    jsonl(p1, {"role": "user", "message": {"content": [{"type": "text", "text":
              "<timestamp>Mon</timestamp>\n<user_query>\ncreate a file\n</user_query>"}]}},
          {"role": "assistant", "message": {"content": [{"type": "text", "text": "Created."}]}},
          {"role": "user", "message": {"content": "<user_query>\nand delete it\n</user_query>"}})
    key = hashlib.md5(meta_cwd.encode()).hexdigest()
    (cur / "chats" / key / "c1").mkdir(parents=True)
    (cur / "chats" / key / "c1" / "meta.json").write_text(json.dumps({"schemaVersion": 1, "cwd": meta_cwd}))

    trusted = str(tmp_path / "trusted-repo")
    p2 = cur / "projects" / "other" / "agent-transcripts" / "c2.jsonl"
    jsonl(p2, {"role": "user", "message": {"content": "hi"}})
    (p2.parent.parent / ".workspace-trusted").write_text(json.dumps({"workspacePath": trusted}))

    real = tmp_path / "Real Repo"
    real.mkdir()
    enc = sessions.re.sub(r"[^A-Za-z0-9]", "-", str(real).lstrip("/"))
    jsonl(cur / "projects" / enc / "agent-transcripts" / "c3.jsonl", {"role": "user", "message": {"content": "x"}})
    jsonl(cur / "projects" / "gone" / "agent-transcripts" / "c4.jsonl", {"role": "user", "message": {"content": "x"}})
    jsonl(cur / "projects" / enc / "agent-transcripts" / "ide" / "ide.jsonl", {"role": "user"})  # IDE layout

    by = {s.id: s for s in sessions.load_sessions(include_hidden=True)}
    assert set(by) == {"c1", "c2", "c3"}  # c4: cwd unknowable
    c1 = by["c1"]
    assert (c1.cli, c1.cwd, c1.title, c1.last_prompt) == ("cursor", meta_cwd, "create a file", "and delete it")
    assert c1.resume_command() == "cursor-agent --resume c1"
    assert by["c2"].cwd == trusted
    assert os.path.normcase(by["c3"].cwd) == os.path.normcase(str(real))


def make_cline_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE sessions (session_id TEXT PRIMARY KEY, source TEXT NOT NULL, pid INTEGER NOT NULL,
        started_at TEXT NOT NULL, ended_at TEXT, exit_code INTEGER, status TEXT NOT NULL,
        status_lock INTEGER NOT NULL DEFAULT 0, interactive INTEGER NOT NULL, provider TEXT NOT NULL,
        model TEXT NOT NULL, cwd TEXT NOT NULL, workspace_root TEXT NOT NULL, team_name TEXT,
        enable_tools INTEGER NOT NULL, enable_spawn INTEGER NOT NULL, enable_teams INTEGER NOT NULL,
        parent_session_id TEXT, parent_agent_id TEXT, agent_id TEXT, conversation_id TEXT,
        is_subagent INTEGER NOT NULL DEFAULT 0, prompt TEXT, metadata_json TEXT,
        transcript_path TEXT NOT NULL DEFAULT '', hook_path TEXT NOT NULL, messages_path TEXT,
        updated_at TEXT NOT NULL)""")
    return con


def add(con, sid, cwd, prompt, meta=None, sub=0, updated="2026-10-09T10:00:00.000Z"):
    con.execute("INSERT INTO sessions (session_id, source, pid, started_at, status, interactive, provider, model,"
                " cwd, workspace_root, enable_tools, enable_spawn, enable_teams, is_subagent, prompt,"
                " metadata_json, hook_path, updated_at) VALUES (?, 'cli', 1, ?, 'completed', 1, 'p', 'm', ?, ?,"
                " 1, 0, 0, ?, ?, ?, '', ?)",
                (sid, updated, cwd, cwd, sub, prompt, json.dumps(meta) if meta else None, updated))


def test_cline_sqlite_sessions(tmp_path, monkeypatch):
    repo = str(tmp_path / "repo")
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    con = make_cline_db(tmp_path / "cline" / "data" / "db" / "sessions.db")
    add(con, "s1", repo, "refactor parser", {"title": "Parser work"}, updated=now)
    add(con, "s2", repo, "child task", sub=1, updated=now)
    add(con, "old", repo, "ancient", updated="2001-01-01T00:00:00.000Z")
    con.commit()
    con.close()
    by = {s.id: s for s in sessions.load_sessions(include_hidden=True)}
    assert set(by) == {"s1", "s2"}  # past max_age_days: dropped
    s1 = by["s1"]
    assert (s1.cli, s1.cwd, s1.title, s1.named, s1.last_prompt, s1.subagent) == \
        ("cline", repo, "Parser work", True, "refactor parser", False)
    assert by["s2"].subagent and by["s2"].title == "child task"
    assert s1.resume_command() == "cline --id s1"
    # CLINE_DB_DATA_DIR wins over CLINE_DATA_DIR wins over CLINE_DIR
    monkeypatch.setenv("CLINE_DATA_DIR", str(tmp_path / "data2"))
    assert sessions._cline_db() == tmp_path / "data2" / "db" / "sessions.db"
    monkeypatch.setenv("CLINE_DB_DATA_DIR", str(tmp_path / "dbdir"))
    assert sessions._cline_db() == tmp_path / "dbdir" / "sessions.db"


def test_foreign_or_locked_stores_never_sink_the_scan(tmp_path):
    db = tmp_path / "cline" / "data" / "db" / "sessions.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"not a database")
    jsonl(tmp_path / "fhome" / ".factory" / "sessions" / "x" / "a.jsonl",
          {"type": "session_start", "id": "a", "cwd": str(tmp_path)})
    (tmp_path / "fhome" / ".factory" / "sessions" / "x" / "b.jsonl").write_bytes(b"\xff\xfe garbage")
    assert [s.id for s in sessions.load_sessions(include_hidden=True)] == ["a"]
