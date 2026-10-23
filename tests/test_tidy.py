import time
from types import SimpleNamespace as NS

from navigator import restore, startup, tidy

H = 3600


def agent(sid, status="idle", project="P", **kw):
    d = dict(in_herdr=True, session_id=sid, status=status, focused=False, subagents=[], jobs=0, workflows=0,
             cli="claude", pane_id=f"p-{sid}", tab_id=f"t-{sid}", display=sid, project=NS(root=project, name=project))
    return NS(**{**d, **kw})


def run(monkeypatch, agents, mtimes, started, panes=(), enabled=True, keep=0):
    now = time.time()
    calls, ticks = [], []
    monkeypatch.setattr(tidy, "config", lambda: (enabled, 10 * H, keep))
    monkeypatch.setattr(tidy, "server_started_at", lambda: now - started * H)
    monkeypatch.setattr(tidy, "load_sessions",
                        lambda include_hidden=True: [NS(id=k, mtime=now - v * H) for k, v in mtimes.items()])
    monkeypatch.setattr(tidy.herdr, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(startup, "set_tick", lambda key, on: ticks.append((key, on)))
    monkeypatch.setattr(restore, "log", lambda m: None)
    monkeypatch.setattr(restore, "notify", lambda *a: None)
    snap = {"panes": [{"pane_id": p, "tab_id": t} for p, t in panes] or
            [{"pane_id": a.pane_id, "tab_id": a.tab_id} for a in agents]}
    closed = tidy.sweep(NS(agents=agents), snap)
    return closed, calls, ticks


def test_closes_only_parked_sessions_past_the_limit_and_unticks_them(monkeypatch):
    agents = [agent("old"), agent("fresh"), agent("busy", "working"), agent("done", "done"),
              agent("kids", subagents=[1]), agent("here", focused=True)]
    mt = {a.session_id: 30 for a in agents} | {"fresh": 2}
    closed, calls, ticks = run(monkeypatch, agents, mt, started=40)
    assert closed == ["old"] and calls == [("tab", "close", "t-old")] and ticks == [("claude:old", False)]


def test_inactive_sessions_close_too(monkeypatch):
    # the plugin turns idle into "inactive" after an hour: nearly every parked session has that status
    assert run(monkeypatch, [agent("old", "inactive")], {"old": 30}, started=40)[0] == ["old"]


def test_the_newest_sessions_of_each_project_stay(monkeypatch):
    agents = [agent("a1", project="A"), agent("a2", project="A"), agent("a3", project="A"), agent("b1", project="B")]
    mt = {"a1": 30, "a2": 40, "a3": 50, "b1": 60}
    closed = run(monkeypatch, agents, mt, started=70, keep=2)[0]
    assert closed == ["a3"]                      # A keeps its 2 newest; B's only session is its newest


def test_a_morning_restart_closes_nothing_until_ten_hours_after_the_server_started(monkeypatch):
    agents = [agent("a"), agent("b")]
    mt = {"a": 20, "b": 15}                      # both wrote far over 10 h ago, the server started 1 h ago
    assert run(monkeypatch, agents, mt, started=1)[0] == []
    assert run(monkeypatch, agents, mt, started=11)[0] == ["a", "b"]   # 10 h later they are due


def test_a_tab_with_other_panes_only_loses_the_pane(monkeypatch):
    a = agent("old", tab_id="t1", pane_id="p1")
    _, calls, _ = run(monkeypatch, [a], {"old": 30}, started=40, panes=[("p1", "t1"), ("p2", "t1")])
    assert calls == [("pane", "close", "p1")]


def test_off_by_default_and_when_the_server_start_is_unknown(monkeypatch):
    assert run(monkeypatch, [agent("old")], {"old": 30}, started=40, enabled=False)[0] == []
    assert run(monkeypatch, [agent("old")], {"old": 30}, started=0)[0] == []


def test_review_recommends_with_a_reason_and_ignores_the_server_clock(monkeypatch):
    agents = [agent("old"), agent("new"), agent("mid")]
    run(monkeypatch, agents, {"old": 80, "new": 1, "mid": 5}, started=0, keep=1)
    items = {i.agent.session_id: i for i in tidy.review(NS(agents=agents), time.time())}
    assert (items["old"].close, items["old"].reason) == (True, "idle 3 days")
    assert (items["new"].close, "newest 1" in items["new"].reason) == (False, True)
    assert (items["mid"].close, items["mid"].reason) == (False, "idle only 5 h")
