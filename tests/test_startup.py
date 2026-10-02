import json
import os
import time

import pytest

from navigator import asks, autostart, projects, settings, startup
from navigator.sessions import Session

DAY = 86400


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_PLUGIN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "navigator.toml").write_text("[hide]\npatterns = []\n", encoding="utf-8")
    settings.load.cache_clear()
    projects.clear_cache()
    yield
    settings.load.cache_clear()
    projects.clear_cache()


def repo(tmp_path, name="proj"):
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    return root


def sess(cwd, sid, days_ago, cli="claude", title=None, named=True, now=None):
    now = now or time.time()
    return Session(cli, sid, str(cwd), title or sid, "", "", now - days_ago * DAY, "", named=named)


def ticked_ids(groups):
    return {r.session.id for g in groups for r in g.ticked}


def test_auto_tick_newest_three_per_lane_within_window(tmp_path):
    root = repo(tmp_path)
    wt = root / ".claude" / "worktrees" / "feat"
    wt.mkdir(parents=True)
    now = time.time()
    ss = [sess(root, f"m{i}", i * 0.5, now=now) for i in range(5)]          # main: 0, .5, 1, 1.5, 2 days
    ss += [sess(wt, f"w{i}", i, now=now) for i in range(2)]                 # worktree lane has its own budget
    ss += [sess(root, "old", 5, now=now)]                                   # outside the 3-day window
    got = ticked_ids(startup.plan(ss, {"projects": {}, "sessions": {}}, now=now))
    assert got == {"m0", "m1", "m2", "w0", "w1"}


def test_explicit_choices_win_and_use_the_lane_budget(tmp_path):
    root = repo(tmp_path)
    now = time.time()
    ss = [sess(root, f"m{i}", i * 0.1, now=now) for i in range(5)] + [sess(root, "old", 20, now=now)]
    data = {"projects": {}, "sessions": {"claude:old": {"tick": True}, "claude:m0": {"tick": False}}}
    groups = startup.plan(ss, data, now=now)
    # "old" is ticked by hand (uses one of three), m0 is unticked by hand: auto fills m1, m2
    assert ticked_ids(groups) == {"old", "m1", "m2"}
    pinned = {r.session.id for g in groups for r in g.rows if r.pinned}
    assert pinned == {"old", "m0"}


def test_project_switch_and_recency(tmp_path):
    a, b = repo(tmp_path, "a"), repo(tmp_path, "b")
    now = time.time()
    ss = [sess(a, "a1", 0.1, now=now), sess(b, "b1", 30, now=now)]
    groups = {g.project.name: g for g in startup.plan(ss, {"projects": {}, "sessions": {"claude:b1": {"tick": True}}}, now=now)}
    assert groups["a"].on and not groups["b"].on        # b not worked in for 30 days: off by default
    assert ticked_ids(groups.values()) == {"a1"}       # so its hand tick does not restore
    groups = startup.plan(ss, {"projects": {projects.resolve(str(b)).root: {"on": True}},
                               "sessions": {"claude:b1": {"tick": True}}}, now=now)
    assert ticked_ids(groups) == {"a1", "b1"}


def test_auto_tick_off_keeps_only_hand_ticks(tmp_path):
    root = repo(tmp_path)
    now = time.time()
    ss = [sess(root, "x", 0.1, now=now), sess(root, "y", 0.2, now=now)]
    data = {"projects": {projects.resolve(str(root)).root: {"auto": False}}, "sessions": {"claude:y": {"tick": True}}}
    assert ticked_ids(startup.plan(ss, data, now=now)) == {"y"}


def test_set_tick_round_trip_and_back_to_auto(tmp_path):
    startup.set_tick("claude:abc", False)
    assert startup.load()["sessions"]["claude:abc"] == {"tick": False}
    startup.set_tick("claude:abc", None)
    assert "claude:abc" not in startup.load()["sessions"]
    startup.set_prefs("claude:abc", {"model": "opus", "effort": ""})
    assert startup.prefs_of("claude:abc") == {"model": "opus"}


def test_launch_command_claude_flags_and_quoting(tmp_path):
    cmd = startup.launch_command("claude", "ID", 'My "risky" $name`', {
        "model": "opus", "effort": "high", "permission_mode": "plan", "remote_control": True, "args": "--verbose"})
    assert cmd == ('claude --resume ID -n "My risky name" --remote-control "My risky name" '
                   '--model "opus" --effort high --permission-mode plan --verbose')
    assert startup.launch_command("claude", "ID") == "claude --resume ID"
    assert startup.launch_command("codex", "T", "name", {"model": "x"}) == "codex resume T"
    assert startup.launch_command("nope", "T") == ""


def test_import_from_session_restore_registry(tmp_path):
    root = repo(tmp_path)
    reg = {"version": 3, "directories": [
        {"path": str(root), "enabled": True, "sessions": [
            {"sessionId": "s1", "enabled": True, "pinned": True, "prefs": {"model": "opus", "remoteControl": False}},
            {"sessionId": "s2", "enabled": False, "pinned": True},
            {"sessionId": "s3", "enabled": True, "pinned": False},
            {"sessionId": "s4", "enabled": True, "pinned": True, "gone": True}]},
        {"path": str(repo(tmp_path, "shelf")), "shelved": True, "sessions": []}]}
    f = tmp_path / "reg.json"
    f.write_text(json.dumps(reg), encoding="utf-8-sig")
    assert startup.import_session_restore(str(f)) == "imported 2 ticks and 1 switched-off projects"
    data = startup.load()
    assert data["sessions"]["claude:s1"] == {"tick": True, "prefs": {"model": "opus", "remote_control": False}}
    assert data["sessions"]["claude:s2"] == {"tick": False}
    assert "claude:s3" not in data["sessions"] and "claude:s4" not in data["sessions"]


def _jsonl(path, recs):
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return str(path)


def test_pending_claude_question_and_answered(tmp_path):
    ask = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "AskUserQuestion",
           "input": {"questions": [{"question": "Which DB?", "multiSelect": False,
                                    "options": [{"label": "Postgres"}, {"label": "SQLite"}]}]}}]}}
    p = _jsonl(tmp_path / "a.jsonl", [ask])
    q = asks.pending("claude", p)
    assert q and q.text == "Which DB?" and q.options == ["Postgres", "SQLite"] and not q.multi
    ans = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}}
    assert asks.pending("claude", _jsonl(tmp_path / "b.jsonl", [ask, ans])) is None
    assert asks.pending("claude", str(tmp_path / "missing.jsonl")) is None


def test_pending_codex_question(tmp_path):
    call = {"type": "response_item", "payload": {"type": "function_call", "name": "request_user_input", "call_id": "c1",
            "arguments": json.dumps({"questions": [{"question": "Ship it?", "options": [{"label": "yes"}]}]})}}
    assert asks.pending("codex", _jsonl(tmp_path / "c.jsonl", [call])).text == "Ship it?"
    out = {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": "yes"}}
    assert asks.pending("codex", _jsonl(tmp_path / "d.jsonl", [call, out])) is None


def test_autostart_entry_lives_in_the_user_startup_location():
    p = str(autostart.entry_path()).replace("\\", "/").lower()
    if os.name == "nt":
        assert p.endswith("start menu/programs/startup/herdr navigator restore.lnk")
    else:
        assert "launchagents" in p or "autostart" in p
