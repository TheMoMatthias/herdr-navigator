"""Context fill for the CLIs beyond Claude and Codex, on fixtures shaped like each CLI's own
records (formats checked against their sources: anomalyco/opencode + Kilo-Org/kilocode
packages/schema/src/v1/session.ts, badlogic/pi-mono packages/ai/src/types.ts + coding-agent
session-manager.ts, QwenLM/qwen-code chatRecordingService.ts, google-gemini/gemini-cli
chatRecordingService.ts)."""
import json
import sqlite3

import pytest

from navigator import insight, settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_PLUGIN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "ghome"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "navigator.toml").write_text("[hide]\npatterns = []\n", encoding="utf-8")
    settings.load.cache_clear()
    yield
    settings.load.cache_clear()


def jsonl(path, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return str(path)


# --- OpenCode / Kilo ---------------------------------------------------------------------

def opencode_db(path, msgs):
    """msgs: (session id, time, data dict) rows of the `message` table."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INT,"
                " time_updated INT, data TEXT)")
    for i, (sid, t, data) in enumerate(msgs):
        con.execute("INSERT INTO message VALUES (?,?,?,?,?)", (f"msg_{i:03}", sid, t, t, json.dumps(data)))
    con.commit()
    con.close()
    return str(path)


def oc_assistant(inp, read=0, write=0, out=50, model="claude-sonnet-4-5", summary=None):
    d = {"role": "assistant", "modelID": model, "providerID": "anthropic", "mode": "build", "cost": 0,
         "tokens": {"input": inp, "output": out, "reasoning": 0, "cache": {"read": read, "write": write}}}
    if summary:
        d["summary"] = True
    return d


@pytest.mark.parametrize("cli", ["opencode", "kilo"])
def test_opencode_family_reads_its_session_from_the_shared_db(tmp_path, cli):
    db = opencode_db(tmp_path / f"{cli}.db", [
        ("ses_a", 1, {"role": "user"}),
        ("ses_a", 2, oc_assistant(1_000, read=50_000, write=2_000)),
        ("ses_a", 3, oc_assistant(0, out=0)),                 # in flight: zero tokens, skipped
        ("ses_b", 4, oc_assistant(5, read=900_000, model="gpt-5")),
    ])
    c = insight.context(cli, db, "ses_a")
    assert c.used == 53_000 and c.window == 200_000
    c = insight.context(cli, db, "ses_b")
    assert c.used == 900_005 and c.window == 400_000 and c.pct == 100
    assert insight.context(cli, db, "ses_none") is None
    assert insight.context(cli, db, "") is None
    assert insight.context(cli, str(tmp_path / "missing.db"), "ses_a") is None


def test_opencode_summary_is_the_context_after_compaction(tmp_path):
    db = opencode_db(tmp_path / "opencode.db", [
        ("s", 1, oc_assistant(10, read=180_000)),
        ("s", 2, oc_assistant(180_000, out=4_000, summary=True)),
    ])
    assert insight.context("opencode", db, "s").used == 4_000


def test_opencode_unknown_model_shows_tokens_only_and_a_foreign_db_is_none(tmp_path):
    db = opencode_db(tmp_path / "opencode.db", [("s", 1, oc_assistant(7_000, model="mystery-1"))])
    c = insight.context("opencode", db, "s")
    assert c.used == 7_000 and c.window == 0 and c.pct == 0 and c.bar() == "▱▱▱▱"
    bad = tmp_path / "other.db"
    sqlite3.connect(bad).close()
    assert insight.context("opencode", str(bad), "s") is None


def test_opencode_reading_is_cached_until_the_db_changes(tmp_path, monkeypatch):
    db = opencode_db(tmp_path / "opencode.db", [("s", 1, oc_assistant(1_000))])
    assert insight.context("opencode", db, "s").used == 1_000
    calls = []
    real = sqlite3.connect
    monkeypatch.setattr(insight.sqlite3, "connect", lambda *a, **k: calls.append(a) or real(*a, **k))
    assert insight.context("opencode", db, "s").used == 1_000 and not calls
    con = real(db)
    con.execute("INSERT INTO message VALUES ('msg_z','s',9,9,?)", (json.dumps(oc_assistant(2_000)),))
    con.commit()
    con.close()
    assert insight.context("opencode", db, "s").used == 2_000 and len(calls) == 1


def test_context_windows_from_settings_win_over_the_built_in_table(tmp_path):
    (tmp_path / "cfg" / "navigator.toml").write_text('[context]\n"gpt-5" = 272000\n', encoding="utf-8")
    settings.load.cache_clear()
    db = opencode_db(tmp_path / "opencode.db", [("s", 1, oc_assistant(1_000, model="gpt-5-codex"))])
    assert insight.context("opencode", db, "s").window == 272_000


# --- pi / oh-my-pi -------------------------------------------------------------------------

def pi_msg(inp, read=0, write=0, model="claude-opus-4-5"):
    return {"type": "message", "id": "e", "parentId": None, "timestamp": "2026-10-09T08:00:00Z",
            "message": {"role": "assistant", "provider": "anthropic", "model": model, "content": [],
                        "usage": {"input": inp, "output": 300, "cacheRead": read, "cacheWrite": write,
                                  "totalTokens": inp + read + write + 300}, "stopReason": "stop"}}


def test_pi_context_and_reserve_tokens(tmp_path):
    p = jsonl(tmp_path / "s.jsonl", [{"type": "session", "id": "x", "cwd": "/w"},
                                     pi_msg(100, read=60_000, write=400),
                                     {"type": "message", "message": {"role": "user", "content": "go"}}])
    c = insight.context("pi", p)
    assert c.used == 60_500 and c.window == 200_000 - 16_384
    (tmp_path / "pi").mkdir()
    (tmp_path / "pi" / "settings.json").write_text('{"compaction": {"reserveTokens": 50000}}', encoding="utf-8")
    p2 = jsonl(tmp_path / "s2.jsonl", [pi_msg(100, read=60_000, write=400)])
    assert insight.context("pi", p2).window == 150_000
    (tmp_path / "pi" / "settings.json").write_text('{"compaction": {"enabled": false}}', encoding="utf-8")
    p3 = jsonl(tmp_path / "s3.jsonl", [pi_msg(100)])
    assert insight.context("pi", p3).window == 200_000


def test_pi_and_omp_compaction_replace_the_context(tmp_path):
    p = jsonl(tmp_path / "pi.jsonl", [pi_msg(100, read=150_000),
                                      {"type": "compaction", "summary": "...", "firstKeptEntryId": "e",
                                       "tokensBefore": 150_100}])
    assert insight.context("pi", p).used == 1             # pi records no size after: unknown until the next answer
    o = jsonl(tmp_path / "omp.jsonl", [pi_msg(100, read=150_000),
                                       {"type": "compaction", "summary": "...", "firstKeptEntryId": "e",
                                        "tokensBefore": 150_100, "tokensAfter": 9_000}])
    c = insight.context("omp", o)
    assert c.used == 9_000 and c.window == 200_000       # omp: window only, no compaction setting read
    assert insight.context("pi", jsonl(tmp_path / "e.jsonl", [{"type": "session", "id": "x"}])) is None


# --- Qwen Code -----------------------------------------------------------------------------

def qwen_rec(prompt, model="qwen3-coder-plus", window=None, side=False):
    r = {"uuid": "u", "parentUuid": None, "sessionId": "q", "timestamp": "2026-10-09T08:00:00Z",
         "type": "assistant", "cwd": "/w", "model": model, "isSidechain": side,
         "message": {"role": "model", "parts": [{"text": "ok"}]},
         "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": 20,
                           "cachedContentTokenCount": prompt // 2, "totalTokenCount": prompt + 20}}
    if window:
        r["contextWindowSize"] = window
    return r


def test_qwen_context_window_and_compaction_ladder(tmp_path):
    p = jsonl(tmp_path / "q.jsonl", [qwen_rec(120_000, window=1_000_000), qwen_rec(999_999, side=True)])
    c = insight.context("qwen", p)
    assert c.used == 120_000 and c.window == 850_000          # 85% of a large window
    p = jsonl(tmp_path / "q2.jsonl", [qwen_rec(50_000, window=128_000)])
    assert insight.context("qwen", p).window == 128_000 - 33_000   # small window: the absolute ceiling
    p = jsonl(tmp_path / "q4.jsonl", [qwen_rec(50_000, model="qwen3-coder")])
    assert insight.context("qwen", p).window == round(0.85 * 262_144)  # window by model name
    p = jsonl(tmp_path / "q3.jsonl", [qwen_rec(150_000, window=1_000_000),
                                      {"type": "system", "subtype": "chat_compression", "sessionId": "q",
                                       "systemPayload": {"info": {"originalTokenCount": 150_000,
                                                                  "newTokenCount": 12_000}}}])
    assert insight.context("qwen", p).used == 12_000


# --- Gemini CLI ----------------------------------------------------------------------------

def gem_msg(inp, model="gemini-2.5-pro", mid="m1"):
    return {"id": mid, "timestamp": "2026-10-09T08:00:00Z", "type": "gemini", "content": "ok", "model": model,
            "tokens": {"input": inp, "output": 30, "cached": inp // 3, "thoughts": 0, "tool": 0, "total": inp + 30}}


def test_gemini_jsonl_legacy_json_and_threshold(tmp_path):
    chats = tmp_path / "ghome" / ".gemini" / "tmp" / "proj" / "chats"
    p = jsonl(chats / "session-1.jsonl", [
        {"sessionId": "g", "projectHash": "h", "startTime": "t"},
        {"id": "m0", "type": "user", "content": "hi"},
        gem_msg(200_000, mid="m1"),
        {"id": "m2", "type": "gemini", "content": "thinking", "model": "gemini-2.5-pro"},  # tokens not in yet
        {"$set": {"lastUpdated": "t2"}},
    ])
    c = insight.context("gemini", p)
    assert c.used == 200_000 and c.window == 524_288          # compresses at 0.5 of 1M by default
    (tmp_path / "ghome" / ".gemini" / "settings.json").write_text('{"model": {"compressionThreshold": 0.7}}',
                                                                   encoding="utf-8")
    legacy = chats / "session-0.json"
    legacy.write_text(json.dumps({"sessionId": "g", "messages": [gem_msg(300_000), gem_msg(0, mid="m2")]},
                                 indent=2), encoding="utf-8")
    c = insight.context("gemini", str(legacy))
    assert c.used == 300_000 and c.window == round(1_048_576 * 0.7)
    rw = jsonl(chats / "session-2.jsonl", [{"$set": {"messages": [gem_msg(40_000), gem_msg(41_000, mid="m2")]}}])
    assert insight.context("gemini", rw).used == 41_000


def test_unknown_cli_or_empty_transcript_is_none(tmp_path):
    assert insight.context("copilot", jsonl(tmp_path / "c.jsonl", [{"type": "x"}])) is None
    assert insight.context("gemini", jsonl(tmp_path / "g.jsonl", [{"type": "user"}])) is None
    assert insight.context("qwen", "") is None
