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


# ---- background terminals and sub-agents (sidebar ⟳ / ↳) -------------------------------------------

def _tool(ts, kind, cid, **kw):
    return {"timestamp": ts, "type": "response_item", "payload": {"type": kind, "call_id": cid, **kw}}


def _exec_out(cid, ts="2026-10-09T10:00:00.000Z", sid=None, exit_code=None, mode="code"):
    body = {"chunk_id": "c", "wall_time_seconds": 1.0, "output": "x"}
    if sid is not None:
        body["session_id"] = sid
    if exit_code is not None:
        body["exit_code"] = exit_code
    if mode == "plain":   # the non-code-mode text form
        txt = f"Process running with session ID {sid}\nOutput:\nx" if sid is not None else f"Process exited with code {exit_code}\nOutput:\nx"
        return _tool(ts, "function_call_output", cid, output=txt)
    return _tool(ts, "custom_tool_call_output", cid, output=[
        {"type": "input_text", "text": "Script completed\nWall time 1.0 seconds\nOutput:\n"},
        {"type": "input_text", "text": json.dumps(body)}])


def _start(cid, ts="2026-10-09T10:00:00.000Z"):
    return _tool(ts, "custom_tool_call", cid, name="exec", input='text(await tools.exec_command({cmd:"pytest"}));')


def _poll(cid, sid, ts="2026-10-09T10:01:00.000Z"):
    return _tool(ts, "custom_tool_call", cid, name="exec",
                 input=f'text(await tools.write_stdin({{session_id:{sid},chars:"",yield_time_ms:1000}}));')


NOW = datetime.fromisoformat("2026-10-09T10:05:00+00:00").timestamp()


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append(path, *recs):
    with open(path, "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")


def test_codex_job_runs_until_a_poll_returns_an_exit_code(tmp_path):
    p = tmp_path / "r.jsonl"
    jl(p, [_start("a"), _exec_out("a", sid=111), _start("b"), _exec_out("b", sid=222),
           _start("c"), _exec_out("c", exit_code=0)])                       # c finished at once: never a job
    assert live.codex_jobs(str(p), NOW) == 2
    _append(p, _poll("d", 111), _exec_out("d", sid=111))                   # still running when polled: unchanged
    assert live.codex_jobs(str(p), NOW) == 2
    _append(p, _poll("e", 111), _exec_out("e", exit_code=0))               # an exit code on the poll ends it
    assert live.codex_jobs(str(p), NOW) == 1


def test_codex_job_in_plain_function_call_form(tmp_path):
    start = _tool("2026-10-09T10:00:00.000Z", "function_call", "a", name="exec_command", arguments=json.dumps({"cmd": "npm run dev"}))
    poll = _tool("2026-10-09T10:01:00.000Z", "function_call", "b", name="write_stdin", arguments=json.dumps({"session_id": 9, "chars": ""}))
    p = jl(tmp_path / "r.jsonl", [start, _exec_out("a", sid=9, mode="plain")])
    assert live.codex_jobs(p, NOW) == 1
    _append(p, poll, _exec_out("b", exit_code=0, mode="plain"))
    assert live.codex_jobs(p, NOW) == 0


def test_codex_job_ignores_quoted_text_and_stale_jobs(tmp_path):
    said = {"timestamp": "2026-10-09T10:00:00.000Z", "type": "response_item", "payload": {
        "type": "message", "call_id": "q", "role": "assistant", "content": [{"type": "output_text", "text": 'the log said Process running with session ID 5'}]}}
    p = jl(tmp_path / "r.jsonl", [said, _start("a"), _exec_out("a", sid=7)])
    assert live.codex_jobs(p, NOW) == 1
    assert live.codex_jobs(p, NOW + live.CODEX_JOB_SECONDS + 60) == 0     # its process died with the session long ago
    assert live.codex_jobs(str(tmp_path / "missing.jsonl"), NOW) == 0


def test_codex_job_scan_is_incremental(tmp_path, monkeypatch):
    p = jl(tmp_path / "r.jsonl", [_start("a"), _exec_out("a", sid=1)])
    assert live.codex_jobs(p, NOW) == 1
    seen = []
    real = live._cj_scan
    monkeypatch.setattr(live, "_cj_scan", lambda st, data: (seen.append(len(data)), real(st, data)))
    assert live.codex_jobs(p, NOW) == 1 and seen == []                    # unchanged: nothing read
    before = os.path.getsize(p)
    _append(p, _start("b"))
    live.codex_jobs(p, NOW)
    assert seen == [os.path.getsize(p) - before]                          # only the appended line


def _child(day, cid, parent, nick, role, markers, proj, age=0):
    meta = {"type": "session_meta", "payload": {
        "id": cid, "cwd": str(proj), "thread_source": "subagent", "parent_thread_id": parent,
        "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent, "agent_nickname": nick, "agent_role": role}}},
        "agent_nickname": nick, "agent_role": role}}
    f = jl(day / f"rollout-{cid}.jsonl", [meta, ev("user_message", message=f"task of {nick}")] + markers)
    if age:
        os.utime(f, (time.time() - age, time.time() - age))
    return f


def _proj_day(tmp_path):
    proj = tmp_path / "P"
    (proj / ".git").mkdir(parents=True)
    return proj, tmp_path / "codex" / "sessions" / "2026" / "10" / "09"


def _parent(day, proj, markers, age=0):
    f = jl(day / "rollout-p.jsonl", [{"type": "session_meta", "payload": {"id": "P1", "cwd": str(proj)}},
                                     ev("user_message", message="lead")] + markers)
    if age:
        os.utime(f, (time.time() - age, time.time() - age))
    return f


def test_running_codex_gets_subagents_by_turn_and_jobs(tmp_path):
    proj, day = _proj_day(tmp_path)
    _parent(day, proj, [ev("task_started"), _start("a"), _exec_out("a", ts=_utcnow(), sid=5)])
    # a long quiet tool call (written 400 s ago) still counts while its turn is open; a finished turn does not
    _child(day, "C1", "P1", "Hilbert", "explorer", [ev("task_started")], proj, age=400)
    _child(day, "C2", "P1", "Noether", "worker", [ev("task_started"), ev("task_complete")], proj)
    _child(day, "C3", "P1", "Gauss", "worker", [ev("task_started")], proj, age=7200)   # long dead
    _child(day, "C4", "OTHER", "Euler", "worker", [ev("task_started")], proj)
    rr = live.running(sessions.load_sessions(include_hidden=True))
    [r] = [r for r in rr if r.session_id == "P1"]
    assert [(s.name, s.kind, s.description) for s in r.subagents] == [("Hilbert", "explorer", "task of Hilbert")]
    assert r.jobs == 1 and r.workflows == 0
    assert {x.session_id for x in rr} == {"P1"}                            # children are never top-level rows


def test_codex_work_for_a_pane_running_no_longer_lists(tmp_path):
    proj, day = _proj_day(tmp_path)
    _parent(day, proj, [ev("task_started"), ev("task_complete"), _start("a"), _exec_out("a", ts=_utcnow(), sid=5)], age=3000)
    _child(day, "C1", "P1", "Hilbert", "explorer", [ev("task_started")], proj)    # idle far past the 15 min listing window
    allses = sessions.load_sessions(include_hidden=True)
    assert [r for r in live.running(allses) if r.cli == "codex"] == []
    [s] = [s for s in allses if s.id == "P1"]
    subs, jobs = live.codex_work(s, live.codex_children(allses, time.time()), time.time())
    assert [x.name for x in subs] == ["Hilbert"] and jobs == 1


def test_herdr_codex_pane_carries_subagents_and_jobs(tmp_path, monkeypatch):
    from navigator import model, sync
    proj, day = _proj_day(tmp_path)
    _parent(day, proj, [ev("task_started"), ev("task_complete"), _start("a"), _exec_out("a", ts=_utcnow(), sid=5)], age=3000)
    _child(day, "C1", "P1", "Hilbert", "explorer", [ev("task_started")], proj)
    pane = {"pane_id": "p1", "agent": "codex", "agent_status": "idle", "cwd": str(proj), "workspace_id": "w1",
            "tab_id": "t1", "agent_session": {"kind": "id", "value": "P1"}}
    monkeypatch.setattr(model, "live_state", lambda: ([], [pane], {}, ""))
    model.forget_sessions()
    agents = [a for v in model.build().projects for a in v.agents]
    [a] = [a for a in agents if a.pane_id == "p1"]
    assert [s.name for s in a.subagents] == ["Hilbert"] and a.jobs == 1
    assert sync.work_mark(a) == "↳1 ⟳1"


def test_work_counts_include_codex_sessions_the_daemon_has_seen(tmp_path):
    proj, day = _proj_day(tmp_path)
    p = _parent(day, proj, [ev("task_started")])
    c = _child(day, "C1", "P1", "Hilbert", "explorer", [ev("task_started")], proj)
    live.running(sessions.load_sessions(include_hidden=True))
    assert live.work_counts(time.time()) == [("P1", 1, 0, 0)]
    _append(c, ev("task_complete"))                                        # the child finishes: no herdr event, the daemon notices
    _append(p, _start("a"), _exec_out("a", ts=_utcnow(), sid=3))
    assert live.work_counts(time.time()) == [("P1", 0, 0, 1)]


def test_codex_job_one_script_polling_several_processes(tmp_path):
    two = _tool("2026-10-09T10:01:00.000Z", "custom_tool_call", "p", name="exec", input=(
        'text(await tools.write_stdin({session_id:11,chars:""})); text(await tools.write_stdin({session_id:22,chars:""}));'))
    out = _tool("2026-10-09T10:01:05.000Z", "custom_tool_call_output", "p", output=[
        {"type": "input_text", "text": "Script completed\n"},
        {"type": "input_text", "text": json.dumps({"chunk_id": "a", "exit_code": 0, "output": "done"})},
        {"type": "input_text", "text": json.dumps({"chunk_id": "b", "session_id": 22, "output": "..."})}])
    p = jl(tmp_path / "r.jsonl", [_start("a"), _exec_out("a", sid=11), _start("b"), _exec_out("b", sid=22), two, out])
    assert live.codex_jobs(p, NOW) == 1                                    # 11 ended (first result), 22 still runs (second)


def test_codex_job_poll_beside_an_instant_command_in_one_script(tmp_path):
    mixed = _tool("2026-10-09T10:01:00.000Z", "custom_tool_call", "p", name="exec", input=(
        'text(await tools.exec_command({cmd:"rg x"})); text(await tools.write_stdin({session_id:11,chars:""}));'))
    out = _tool("2026-10-09T10:01:05.000Z", "custom_tool_call_output", "p", output=[
        {"type": "input_text", "text": json.dumps({"chunk_id": "a", "exit_code": 0, "output": "hit"})},   # the rg, not the poll
        {"type": "input_text", "text": json.dumps({"chunk_id": "b", "exit_code": 0, "output": "done"})}])  # the poll: 11 ended
    p = jl(tmp_path / "r.jsonl", [_start("a"), _exec_out("a", sid=11), _start("b"), _exec_out("b", sid=22), mixed, out])
    assert live.codex_jobs(p, NOW) == 1
    quiet = _tool("2026-10-09T10:02:00.000Z", "custom_tool_call_output", "p2", output=[
        {"type": "input_text", "text": json.dumps({"chunk_id": "c", "exit_code": 0})}])
    _append(p, _tool("2026-10-09T10:02:00.000Z", "custom_tool_call", "p2", name="exec", input=(
        'text(await tools.write_stdin({session_id:22,chars:""})); text(await tools.exec_command({cmd:"b"}));')), quiet)
    assert live.codex_jobs(p, NOW) == 1                                    # a result count that fits no call list ends nothing
