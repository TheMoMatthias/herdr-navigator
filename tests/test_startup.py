import json
import os
import time

import pytest

from navigator import asks, autostart, jsonfile, projects, settings, startup
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


def test_autostart_entry_is_a_user_logon_entry():
    p = str(autostart.entry_path()).replace("\\", "/").lower()
    if os.name == "nt":
        assert p.endswith("task scheduler/herdr navigator restore")
    else:
        assert "launchagents" in p or "autostart" in p


def _login_cfg(tmp_path, cli="claude"):
    creds = tmp_path / "home" / ".claude" / ".credentials.json"
    acct = tmp_path / "home" / ".claude.json"
    creds.parent.mkdir(parents=True)
    cfg = tmp_path / "cfg" / "navigator.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + f"""
[login.{cli}]
files = ['{creds.as_posix()}']
json_keys = {{ '{acct.as_posix()}' = ["oauthAccount"] }}
""", encoding="utf-8")
    settings.load.cache_clear()
    return creds, acct


def test_profiles_save_switch_and_keep_rotated_tokens(tmp_path):
    from navigator import profiles
    creds, acct = _login_cfg(tmp_path)
    creds.write_text('{"token": "A1"}', encoding="utf-8")
    acct.write_text(json.dumps({"oauthAccount": {"email": "a@x"}, "other": 1}), encoding="utf-8")
    assert profiles.save_as("claude", "work", "a@x").startswith("💾")
    # sign in to another account, save it too
    creds.write_text('{"token": "B1"}', encoding="utf-8")
    acct.write_text(json.dumps({"oauthAccount": {"email": "b@x"}, "other": 2}), encoding="utf-8")
    profiles.save_as("claude", "private")
    assert profiles.active("claude") == "private" and profiles.names("claude") == ["private", "work"]
    creds.write_text('{"token": "B2"}', encoding="utf-8")       # private's token rotated meanwhile
    assert profiles.switch("claude", "work").startswith("⇄")
    assert json.loads(creds.read_text())["token"] == "A1"
    data = json.loads(acct.read_text())
    assert data["oauthAccount"]["email"] == "a@x" and data["other"] == 2   # only the named key swapped
    profiles.switch("claude", "private")
    assert json.loads(creds.read_text())["token"] == "B2"                  # the rotated token survived
    assert profiles.switch("claude", "private").endswith("already active")
    assert profiles.switch("claude", "nope").startswith("✗")


def test_profile_needs_a_login_and_a_name(tmp_path):
    from navigator import profiles
    _login_cfg(tmp_path)
    assert profiles.save_as("claude", "x").startswith("✗")                 # nothing signed in
    assert profiles.save_as("claude", "  ").startswith("✗")


def test_new_command_names_and_options(tmp_path):
    assert startup.new_command("claude", "Lead 1", {"model": "sonnet", "remote_control": True}) == \
        'claude -n "Lead 1" --remote-control "Lead 1" --model "sonnet"'
    assert startup.new_command("codex", "x", {"args": "--full-auto"}) == "codex --full-auto"


def test_pending_options_move_to_the_session_once_it_has_an_id(tmp_path):
    startup.remember_for_pane("w1:p3", {"model": "opus", "effort": ""})
    startup.claim_pending("w1:p3", "claude:abc")
    assert startup.prefs_of("claude:abc") == {"model": "opus"}
    startup.claim_pending("w1:p3", "claude:other")   # claimed once only
    assert startup.prefs_of("claude:other") == {}


def test_record_panes_survives_a_restart_and_marks_real_quits(tmp_path, monkeypatch):
    from navigator import restore
    from navigator.model import Agent
    p = projects.Project("r", "r")
    a = Agent("claude", "idle", p, name="LEAD", session_id="s1", pane_id="w1:p2")
    world = type("W", (), {"agents": [a]})()
    snap = {"panes": [{"pane_id": "w1:p2", "cwd": "C:/r", "agent": "claude"}, {"pane_id": "w1:p3", "cwd": "C:/r"}]}
    empty = type("W", (), {"agents": []})()
    restore.record_panes(world, snap)
    assert restore.load_panes()["w1:p2"]["name"] == "LEAD"
    # herdr just restarted: the pane is back but empty, and this server has not resumed yet.
    # The 2026-10-03 boot lost every record exactly here.
    monkeypatch.setattr(restore, "server_id", lambda: "new:1")
    restore.record_panes(empty, {"panes": [{"pane_id": "w1:p2", "cwd": "C:/r"}]})
    assert restore.load_panes()["w1:p2"].get("quit") is None
    # resume ran for this server a while ago: an empty pane now means you quit the agent
    jsonfile.write(tmp_path / "state" / "panes-resumed-default.json", {"server": "new:1", "at": time.time() - 120})
    restore.record_panes(empty, {"panes": [{"pane_id": "w1:p2", "cwd": "C:/r"}]})
    assert restore.load_panes()["w1:p2"]["quit"] == "s1"
    # the agent comes back: the quit mark goes
    restore.record_panes(world, snap)
    assert "quit" not in restore.load_panes()["w1:p2"]


def _claude_file(tmp_path, sid, conversation=True):
    f = tmp_path / f"{sid}.jsonl"
    lines = ['{"type":"custom-title","customTitle":"X"}']
    if conversation:
        lines.append('{"type":"user","message":{"content":"hi"}}')
    f.write_text(chr(10).join(lines), encoding="utf-8")
    return str(f)


def test_resume_panes_uses_herdrs_own_session_and_skips_quit_and_empty(tmp_path, monkeypatch):
    from navigator import restore
    from navigator.sessions import Session
    sess = [Session("claude", "s-ok", str(tmp_path), "LEAD-3", "", "", 1.0, _claude_file(tmp_path, "s-ok"), named=True),
            Session("claude", "s-empty", str(tmp_path), "LEAD-4", "", "", 1.0,
                    _claude_file(tmp_path, "s-empty", conversation=False), named=True),
            Session("claude", "s-quit", str(tmp_path), "OLD", "", "", 1.0, _claude_file(tmp_path, "s-quit"), named=True)]
    ref = lambda sid: {"agent": "claude", "kind": "id", "value": sid}  # noqa: E731
    snap = {"panes": [
        {"pane_id": "wT:p2", "cwd": str(tmp_path), "agent": None, "agent_session": ref("s-ok")},
        {"pane_id": "w17:p1", "cwd": str(tmp_path), "agent": None, "agent_session": ref("s-empty")},
        {"pane_id": "w9:p3", "cwd": str(tmp_path), "agent": None, "agent_session": ref("s-quit")},
        {"pane_id": "w1:pF", "cwd": str(tmp_path), "agent": "claude", "agent_session": ref("live")},
        {"pane_id": "w6:p1", "cwd": str(tmp_path), "agent": None, "agent_session": None}]}
    restore.save_panes({"w9:p3": {"cli": "claude", "sid": "s-quit", "name": "OLD", "cwd": str(tmp_path),
                                  "at": time.time(), "quit": "s-quit"}})
    ran = []
    monkeypatch.setattr(restore.herdr, "snapshot", lambda: snap)
    monkeypatch.setattr(restore.herdr, "pane_run", lambda pane, cmd: ran.append((pane, cmd)))
    monkeypatch.setattr(restore, "_foreground", lambda pane: [])
    monkeypatch.setattr(restore, "claude_needs_refresh", lambda: False)
    monkeypatch.setattr("navigator.sessions.load_sessions", lambda include_hidden=False: sess)
    monkeypatch.setattr(restore.time, "sleep", lambda s: None)
    monkeypatch.setattr(restore, "server_id", lambda: "srv:1")
    assert restore.resume_panes() == "resumed 1 in their panes"
    assert [p for p, _ in ran] == ["wT:p2"] and "s-ok" in ran[0][1] and "LEAD-3" in ran[0][1]
    assert restore._resumed_this_server()
    # running = a live agent only: the restored-but-empty panes are not "already running"
    assert restore.live_ids(snap) >= {"live"} and not ({"s-ok", "s-empty", "s-quit"} & restore.live_ids(snap))


def test_has_conversation(tmp_path):
    from navigator import restore
    from navigator.sessions import Session
    ok = Session("claude", "a", "", "", "", "", 1.0, _claude_file(tmp_path, "a"))
    empty = Session("claude", "b", "", "", "", "", 1.0, _claude_file(tmp_path, "b", conversation=False))
    assert restore.has_conversation(ok) and not restore.has_conversation(empty)
    assert restore.has_conversation(None)


def test_decode_project_dir_finds_the_real_folder(tmp_path):
    import re
    from navigator.sessions import _decode_project_dir
    real = tmp_path / "Trading Bot" / "Algo.Trader" / ".claude" / "worktrees" / "lead-4"
    real.mkdir(parents=True)
    (tmp_path / "Trading-Bot").mkdir()  # a decoy that encodes the same way but goes nowhere
    name = re.sub(r"[^A-Za-z0-9]", "-", str(real))
    assert _decode_project_dir(name) == str(real)
    assert _decode_project_dir(name + "-gone") == ""


def test_set_ticks_and_reset(tmp_path, monkeypatch):
    from navigator import settings, startup
    monkeypatch.setattr(settings, "state_dir", lambda: tmp_path)
    startup.set_ticks({"claude:a": True, "claude:b": False}, ["/p"])
    data = startup.load()
    assert data["sessions"] == {"claude:a": {"tick": True}, "claude:b": {"tick": False}}
    assert data["projects"] == {"/p": {"on": True}}
    startup.set_prefs("claude:a", {"model": "opus"})
    startup.reset_all()
    data = startup.load()
    assert data["projects"] == {} and data["sessions"] == {"claude:a": {"prefs": {"model": "opus"}}}


def test_logon_waits_for_this_servers_resume_not_an_old_one(tmp_path, monkeypatch):
    from navigator import restore
    monkeypatch.setattr(restore.time, "sleep", lambda s: None)
    monkeypatch.setattr(restore, "server_id", lambda: "12160:abc")
    (tmp_path / "state").mkdir(exist_ok=True)
    jsonfile.write(tmp_path / "state" / "panes-resumed-default.json", {"server": "999:old", "at": time.time()})
    assert not restore._wait_panes_resumed(timeout=0.05)  # yesterday's server: keep waiting
    jsonfile.write(tmp_path / "state" / "panes-resumed-default.json", {"server": "12160:abc", "at": time.time()})
    assert restore._wait_panes_resumed(timeout=0.05)


def test_guarantee_names_what_is_not_running(tmp_path, monkeypatch):
    from navigator import restore
    s = Session("claude", "s1", str(tmp_path), "LEAD-3", "", "", time.time(), "", named=True)
    monkeypatch.setattr("navigator.sessions.load_sessions", lambda include_hidden=False: [s])
    monkeypatch.setattr(restore.startup, "selected", lambda sessions: [startup.Row(s, True, True)])
    monkeypatch.setattr(restore, "live_ids", lambda snap=None: set())
    want, missing = restore.guarantee(timeout=0)
    assert want == {"s1": "LEAD-3"} and missing == ["LEAD-3"]
    monkeypatch.setattr(restore, "live_ids", lambda snap=None: {"s1"})
    assert restore.guarantee(timeout=0)[1] == []


def test_guarantee_says_when_a_session_waits_on_the_folder_trust_question(monkeypatch):
    from navigator import restore
    snap = {"tabs": [{"tab_id": "w1:t2", "label": "RAM-UPGRADE"}],
            "panes": [{"pane_id": "w1:p2", "tab_id": "w1:t2"}, {"pane_id": "w1:p1", "tab_id": "w1:t1", "agent": "claude"}]}
    monkeypatch.setattr(restore.herdr, "snapshot", lambda: snap)
    monkeypatch.setattr(restore.herdr, "run", lambda *a, **k: {"raw": "Do you trust the files in this folder?\n 1. Yes"})
    assert restore._trust_waits() == ["RAM-UPGRADE"]
    monkeypatch.setattr(restore, "live_ids", lambda *a: set())
    monkeypatch.setattr(restore, "load_panes", lambda: {"w1:p2": {"sid": "s1", "name": "RAM-UPGRADE", "resumed_at": time.time()}})
    monkeypatch.setattr(restore.startup, "selected", lambda rows: [])
    monkeypatch.setattr("navigator.sessions.load_sessions", lambda *a, **k: [])
    want, missing = restore.guarantee(timeout=0)
    assert missing == ["RAM-UPGRADE (waits for you to trust its folder)"]


def _relaunch_env(monkeypatch, *, exits_on_ctrl_c, prompt_line="PS C:/> "):
    from navigator import restore
    state = {"running": True, "keys": [], "ran": [], "closed": [], "killed": []}
    monkeypatch.setattr(restore.startup, "launch_command", lambda *a: "claude --resume S1 -n X --remote-control X")
    monkeypatch.setattr(restore.startup, "prefs_of", lambda k: {})
    monkeypatch.setattr(restore, "_foreground", lambda pane: [{"pid": 7, "name": "claude.exe"}] if state["running"] and pane == "w1:p1" else [])

    def request(method, params, timeout=10):
        if method == "pane.send_keys":
            state["keys"] += params["keys"]
            if exits_on_ctrl_c and state["keys"].count("ctrl+c") >= 2:
                state["running"] = False
            return {}
        if method == "pane.split":
            return {"pane": {"pane_id": "w1:p9"}}
        if method == "pane.close":
            state["closed"].append(params["pane_id"])
            return {}
        raise AssertionError(method)
    monkeypatch.setattr(restore.herdr, "request", request)
    monkeypatch.setattr(restore.herdr, "snapshot", lambda: {"panes": [{"pane_id": p, "agent": "claude", "cwd": "C:/x"}
                                                                      for p in ("w1:p1", "w1:p9")]})
    monkeypatch.setattr(restore.herdr, "run", lambda *a, **k: {"raw": prompt_line})
    monkeypatch.setattr(restore.herdr, "pane_run", lambda pane, cmd: state["ran"].append(pane))
    monkeypatch.setattr(restore, "_kill", lambda pid: state.__setitem__("running", False) or state["killed"].append(pid))
    monkeypatch.setattr(restore.time, "sleep", lambda s: None)
    return restore, state


def test_relaunch_stops_the_cli_its_own_way_and_resumes_in_the_same_pane(monkeypatch):
    restore, st = _relaunch_env(monkeypatch, exits_on_ctrl_c=True)
    assert restore.relaunch_in_place("w1:p1", "claude", "S1", "X") == "↻ X"
    assert st["keys"] == ["ctrl+c", "ctrl+c"] and not st["killed"]
    assert st["ran"] == ["w1:p1"] and not st["closed"]


def test_relaunch_after_a_forced_stop_uses_a_fresh_pane(monkeypatch):
    # a killed CLI leaves the pane's keys encoded ([13u for Enter): the old pane can't run the command
    restore, st = _relaunch_env(monkeypatch, exits_on_ctrl_c=False, prompt_line="PS C:/> [27u")
    assert restore.relaunch_in_place("w1:p1", "claude", "S1", "X") == "↻ X"
    assert st["killed"] == [7]
    assert st["closed"] == ["w1:p1"] and st["ran"] == ["w1:p9"]
