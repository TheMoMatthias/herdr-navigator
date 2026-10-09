"""Codex rollouts: turn state, async questions, usage totals, last answer; Claude workflow-agent usage."""
import json
import os
import time
from datetime import datetime

import pytest

from navigator import asks, insight, live, projects, sessions, settings, usage, watch


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
    insight._ROLLOUTS.clear()
    yield
    settings.load.cache_clear()
    projects.clear_cache()


def jl(path, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in recs) + "\n", encoding="utf-8")
    return str(path)


def ev(kind, ts="2026-10-09T10:00:00.000Z", **kw):
    return {"timestamp": ts, "type": "event_msg", "payload": {"type": kind, **kw}}


def tokens(inp, cached, out, ts="2026-10-09T10:00:00.000Z"):
    t = {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out}
    return ev("token_count", ts, info={"total_token_usage": t, "last_token_usage": t})


# ---- turn state -------------------------------------------------------------------------------

def test_codex_turn_from_task_markers(tmp_path):
    filler = {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": []}}
    assert insight.codex_turn(jl(tmp_path / "a.jsonl", [ev("task_started"), filler])) == "working"
    assert insight.codex_turn(jl(tmp_path / "b.jsonl", [ev("task_started"), filler, ev("task_complete"), tokens(1, 0, 1)])) == "idle"
    assert insight.codex_turn(jl(tmp_path / "c.jsonl", [ev("task_started"), ev("turn_aborted", reason="interrupted")])) == "idle"
    assert insight.codex_turn(jl(tmp_path / "d.jsonl", [ev("task_complete"), ev("task_started")])) == "working"  # a new turn
    assert insight.codex_turn(jl(tmp_path / "e.jsonl", [filler])) == ""
    assert insight.codex_turn(str(tmp_path / "missing.jsonl")) == ""


def test_codex_turn_widens_past_a_long_turn(tmp_path):
    big = {"type": "response_item", "payload": {"type": "function_call_output", "output": "x" * 300_000}}
    assert insight.codex_turn(jl(tmp_path / "w.jsonl", [ev("task_started"), big, big])) == "working"
    assert insight.codex_turn(jl(tmp_path / "i.jsonl", [ev("task_started"), ev("task_complete"), big, big])) == "idle"


def test_marker_text_inside_a_message_is_not_a_marker(tmp_path):
    said = {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": 'grep "type":"task_started" in the log'}]}}
    assert insight.codex_turn(jl(tmp_path / "s.jsonl", [ev("task_complete"), said])) == "idle"


def test_agent_status_settles_codex_unknown_only(tmp_path):
    day = tmp_path / "codex" / "sessions" / "2026" / "10" / "09"
    jl(day / "rollout-2026-10-09T10-00-00-abc-123.jsonl", [ev("task_started"), ev("task_complete")])
    jl(day / "rollout-2026-10-09T10-00-01-def-456.jsonl", [ev("task_started")])

    def codex(sid, st="unknown"):
        return {"agent": "codex", "agent_status": st, "agent_session": {"value": sid}}
    assert insight.agent_status(codex("abc-123")) == "idle"
    assert insight.agent_status(codex("def-456")) == "working"
    assert insight.agent_status(codex("nope")) == "unknown"
    assert insight.agent_status(codex("def-456", "blocked")) == "blocked"          # herdr's own states win
    assert insight.agent_status({**codex("abc-123"), "agent": "claude"}) == "unknown"


def test_watch_polls_instead_of_herdr_wait_for_codex(tmp_path, monkeypatch):
    day = tmp_path / "codex" / "sessions" / "2026" / "10" / "09"
    f = jl(day / "rollout-2026-10-09T10-00-00-abc-123.jsonl", [ev("task_started")])
    agent = {"agent": "codex", "agent_status": "unknown", "agent_session": {"value": "abc-123"}}
    monkeypatch.setattr(watch.herdr, "run", lambda *a, **k: {"agent": agent})
    ticks = []

    def sleep(_):
        ticks.append(1)
        assert len(ticks) < 5, "polling never settled"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev("task_complete")) + "\n")
    monkeypatch.setattr(watch.time, "sleep", sleep)
    assert watch._wait("p", "idle", "done", "blocked") == "idle" and ticks == [1]


def test_running_lists_codex_by_turn_state(tmp_path):
    proj = tmp_path / "P"
    (proj / ".git").mkdir(parents=True)
    day = tmp_path / "codex" / "sessions" / "2026" / "10" / "09"

    def meta(i):
        return {"type": "session_meta", "payload": {"id": i, "cwd": str(proj)}}
    um = ev("user_message", message="go")
    old = time.time() - 400          # past the 150 s "recently written" window
    busy = jl(day / "rollout-b.jsonl", [meta("B"), um, ev("task_started")])
    idle = jl(day / "rollout-i.jsonl", [meta("I"), um, ev("task_started"), ev("task_complete")])
    gone = jl(day / "rollout-g.jsonl", [meta("G"), um, ev("task_started"), ev("task_complete")])
    for f in (busy, idle):
        os.utime(f, (old, old))
    os.utime(gone, (time.time() - 7200, time.time() - 7200))
    rr = {r.session_id: r.status for r in live.running(sessions.load_sessions(include_hidden=True)) if r.cli == "codex"}
    assert rr == {"B": "busy", "I": "idle"}       # a finished turn is idle, not "active" for 150 s then gone


# ---- questions --------------------------------------------------------------------------------

def _call(name, cid, **args):
    return {"type": "response_item", "payload": {"type": "function_call", "name": name, "call_id": cid,
                                                 "arguments": json.dumps(args)}}


def _out(cid, out):
    return {"type": "response_item", "payload": {"type": "function_call_output", "call_id": cid, "output": out}}


def test_async_question_pends_until_next_turn(tmp_path):
    ask = _call("request_user_input_async", "c1", questions=[{"title": "Which DB?", "options": ["PG", "SQLite"]}])
    ack = _out("c1", '{"accepted":true}')
    q = asks.pending("codex", jl(tmp_path / "a.jsonl", [ev("task_started"), ask, ack, ev("task_complete")]))
    assert q and q.text == "Which DB?" and q.options == ["PG", "SQLite"]            # the immediate output does not answer it
    assert asks.pending("codex", jl(tmp_path / "b.jsonl", [ev("task_started"), ask, ack, ev("task_complete"), ev("task_started")])) is None
    assert asks.pending("codex", jl(tmp_path / "c.jsonl", [ask, ack, ev("user_message", message="PG")])) is None


def test_sync_question_still_ends_with_its_output_and_reads_title(tmp_path):
    ask = _call("request_user_input", "c1", questions=[{"question": "Ship?"}])
    assert asks.pending("codex", jl(tmp_path / "a.jsonl", [ask])).text == "Ship?"
    assert asks.pending("codex", jl(tmp_path / "b.jsonl", [ask, _out("c1", "yes")])) is None
    assert asks.pending("codex", jl(tmp_path / "c.jsonl", [_call("request_user_input", "c2", title="T?")])).text == "T?"


# ---- usage ------------------------------------------------------------------------------------

def test_codex_usage_counts_total_growth_not_repeated_events(tmp_path):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    f = tmp_path / "codex" / "sessions" / "2026" / "10" / "09" / "rollout-2026-10-09T10-00-00-abc.jsonl"
    jl(f, [tokens(100, 40, 10, ts), tokens(100, 40, 10, ts),          # the same event twice
           tokens(300, 100, 30, ts)])
    day = time.strftime("%Y-%m-%d")
    t = usage.collect()[("codex", "abc")].days[day]
    assert (t.fresh, t.cached, t.out) == (200, 100, 30)               # = the final total (300 in, 100 cached, 30 out)
    with f.open("a", encoding="utf-8") as fh:                           # incremental: the last total is remembered
        fh.write(json.dumps(tokens(300, 100, 30, ts)) + "\n" + json.dumps(tokens(350, 120, 35, ts)) + "\n")
    t = usage.collect()[("codex", "abc")].days[day]
    assert (t.fresh, t.cached, t.out) == (230, 120, 35)
    with f.open("a", encoding="utf-8") as fh:                           # a total that falls is a reset
        fh.write(json.dumps(tokens(50, 10, 5, ts)) + "\n")
    t = usage.collect()[("codex", "abc")].days[day]
    assert (t.fresh, t.cached, t.out) == (270, 130, 40)


def test_usage_cache_of_an_older_version_is_dropped(tmp_path):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    day = time.strftime("%Y-%m-%d")
    f = tmp_path / "codex" / "sessions" / "2026" / "10" / "09" / "rollout-2026-10-09T10-00-00-abc.jsonl"
    jl(f, [tokens(100, 40, 10, ts)])
    # a version-2 entry has no per-file running total and holds the double-counted sums
    usage._cache_path().parent.mkdir(parents=True, exist_ok=True)
    usage._cache_path().write_text(json.dumps({"v": 2, "files": {str(f): {
        "off": f.stat().st_size - 1, "ids": [], "days": {day: [9999, 9999, 9999]}}}}), encoding="utf-8")
    t = usage.collect()[("codex", "abc")].days[day]
    assert (t.fresh, t.cached, t.out) == (60, 40, 10)
    assert json.loads(usage._cache_path().read_text())["v"] == usage.CACHE_VERSION


def test_claude_workflow_agents_count_toward_the_session(tmp_path):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")

    def msg(i):
        return {"type": "assistant", "timestamp": ts, "message": {"id": i, "usage": {"input_tokens": 3, "output_tokens": 4}}}
    proj = tmp_path / "claude" / "projects" / "C--r"
    jl(proj / "sess1.jsonl", [msg("m1")])
    jl(proj / "sess1" / "subagents" / "workflows" / "wf_1" / "agent-a.jsonl", [msg("w1")])
    t = usage.collect()[("claude", "sess1")].days[time.strftime("%Y-%m-%d")]
    assert (t.fresh, t.out) == (6, 8)


# ---- last answer ------------------------------------------------------------------------------

def test_last_answer_at_codex_uses_task_complete_not_inter_agent_lines(tmp_path):
    inter = {"timestamp": "2026-10-09T12:00:00.000Z", "type": "response_item",
             "payload": {"type": "agent_message", "author": "/root/x", "content": [{"type": "input_text", "text": "hi"}]}}
    p = jl(tmp_path / "r.jsonl", [ev("task_complete", "2026-10-09T10:00:00.000Z", last_agent_message="All done."), inter])
    assert insight.last_answer_at("codex", p) == datetime.fromisoformat("2026-10-09T10:00:00+00:00").timestamp()
    assert insight.last_answer("codex", p) == "All done."
    assert insight.last_answer_at("codex", jl(tmp_path / "n.jsonl", [inter])) == 0.0


def test_last_answer_at_unknown_cli_is_zero(tmp_path):
    p = jl(tmp_path / "x.jsonl", [{"timestamp": "2026-10-09T10:00:00Z", "type": "assistant", "message": {"content": "hi"}}])
    assert insight.last_answer_at("pi", p) == 0.0
    assert insight.last_answer_at("claude", p) > 0
