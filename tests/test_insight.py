import json
import os
import subprocess
import time

import pytest

from navigator import alerts, digest, insight, projects, prompts, settings, usage, worktrees


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


def jsonl(path, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return str(path)


def claude_msg(mid, text, inp=10, cread=0, cwrite=0, out=5, model="claude-opus-5-5", ts="2026-10-02T08:00:00Z",
               side=False):
    return {"type": "assistant", "isSidechain": side, "timestamp": ts,
            "message": {"id": mid, "model": model, "content": [{"type": "text", "text": text}],
                        "usage": {"input_tokens": inp, "cache_read_input_tokens": cread,
                                  "cache_creation_input_tokens": cwrite, "output_tokens": out}}}


def test_claude_context_and_window_by_model(tmp_path):
    p = jsonl(tmp_path / "a.jsonl", [claude_msg("m1", "hi", inp=2, cread=300_000, cwrite=1000),
                                     claude_msg("m2", "side", inp=999_999, side=True)])
    c = insight.context("claude", p)
    assert c.used == 301_002 and c.window == 1_000_000 and c.pct == 30
    p = jsonl(tmp_path / "b.jsonl", [claude_msg("m1", "x", inp=150_000, model="claude-haiku-4-5")])
    assert insight.context("claude", p).window == 200_000
    p = jsonl(tmp_path / "c.jsonl", [claude_msg("m1", "x", inp=250_000, model="claude-haiku-4-5")])
    assert insight.context("claude", p).window == 1_000_000      # seen above its window: a 1M session


def test_codex_context_and_last_answers(tmp_path):
    p = jsonl(tmp_path / "r.jsonl", [
        {"payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 100_000, "output_tokens": 500},
                                                     "model_context_window": 258_400}}},
        {"payload": {"type": "agent_message", "message": "Done: tests pass."}}])
    c = insight.context("codex", p)
    assert c.used == 100_500 and c.window == 258_400
    assert insight.last_answer("codex", p) == "Done: tests pass."
    q = jsonl(tmp_path / "q.jsonl", [claude_msg("m1", "first"), claude_msg("m2", "second")])
    assert insight.last_answer("claude", q) == "second"


def test_digest_due_and_kinds(tmp_path):
    assert not digest.due(0) and not digest.due(time.time() - 60) and digest.due(time.time() - 3600)
    digest.mark_seen(123.0)
    assert digest.last_seen() == 123.0
    since = time.time() - 3600
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    done = jsonl(tmp_path / "d.jsonl", [claude_msg("m", "## Finished the migration\nall green", ts=now_ts)])
    resumed = jsonl(tmp_path / "r.jsonl", [claude_msg("m", "old answer", ts="2020-01-01T00:00:00Z")])
    from navigator.model import Agent
    from navigator.sessions import Session
    p = projects.Project("r", "r")
    asking = Agent("claude", "blocked", p, name="ASK", pane_id="w1:p1")
    finished = Agent("claude", "idle", p, name="DONE", pane_id="w1:p2", session_id="s2", transcript=done)
    old = Agent("claude", "idle", p, name="OLD", pane_id="w1:p3", session_id="s3",
                transcript=jsonl(tmp_path / "o.jsonl", [claude_msg("m", "x")]))
    os.utime(old.transcript, (since - 10, since - 10))
    ended = Session("claude", "s4", str(tmp_path), "ENDED", "", "", time.time(), done)
    just_resumed = Agent("claude", "idle", p, name="RESUMED", pane_id="w1:p4", session_id="s5", transcript=resumed)
    world = type("W", (), {"agents": [finished, asking, old, just_resumed], "sessions": [ended]})()
    got = [(i.kind, i.name, i.line) for i in digest.items(world, since)]
    assert got[0][:2] == ("asks", "ASK")
    assert ("finished", "DONE", "Finished the migration") in got
    assert ("ended", "ENDED", "Finished the migration") in got
    assert all(n not in ("OLD", "RESUMED") for _, n, _ in got)   # resumed without answering: not "done"


def test_alerts_once_per_wait_and_only_when_configured(tmp_path):
    snap = {"workspaces": [{"workspace_id": "w1", "label": "AlgoTrader"}],
            "agents": [{"pane_id": "w1:p1", "workspace_id": "w1", "agent_status": "blocked",
                        "terminal_title_stripped": "STORAGE"}]}
    sent = []
    assert alerts.check_waiting(snap, sender=lambda *a: sent.append(a)) == []      # off: no topic
    cfg = tmp_path / "cfg" / "navigator.toml"
    cfg.write_text(cfg.read_text() + '\n[alerts]\nntfy_topic = "t"\nblocked_minutes = 10\n', encoding="utf-8")
    settings.load.cache_clear()
    t0 = time.time()
    assert alerts.check_waiting(snap, now=t0, sender=lambda *a: sent.append(a)) == []
    assert alerts.check_waiting(snap, now=t0 + 601, sender=lambda *a: sent.append(a)) == ["STORAGE waits for you"]
    assert alerts.check_waiting(snap, now=t0 + 900, sender=lambda *a: sent.append(a)) == []   # once per wait
    assert sent[0][1] == "AlgoTrader: waiting 10 min"
    alerts.check_waiting({"agents": []}, now=t0 + 1000, sender=lambda *a: sent.append(a))   # wait over
    assert alerts.check_waiting(snap, now=t0 + 1001, sender=lambda *a: sent.append(a)) == []  # a new wait starts


def test_alert_send_posts_to_the_topic(tmp_path):
    import http.server
    import threading
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got["path"] = self.path
            got["title"] = self.headers.get("Title")
            got["body"] = self.rfile.read(int(self.headers["Content-Length"])).decode()
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    cfg = tmp_path / "cfg" / "navigator.toml"
    cfg.write_text(cfg.read_text() + f'\n[alerts]\nntfy_topic = "mytopic"\nntfy_server = "http://127.0.0.1:{srv.server_port}"\n',
                   encoding="utf-8")
    settings.load.cache_clear()
    assert alerts.send("Sessions restored", "opened 3 verified 3")
    srv.server_close()
    assert got == {"path": "/mytopic", "title": "Sessions restored", "body": "opened 3 verified 3"}


def test_usage_counts_dedupes_and_reads_incrementally(tmp_path):
    proj = tmp_path / "claude" / "projects" / "C--r"
    today = time.strftime("%Y-%m-%dT%H:%M:%S")
    f = proj / "sess1.jsonl"
    msg = claude_msg("m1", "a", inp=10, cread=100, cwrite=5, out=7, ts=today)
    jsonl(f, [msg, msg])                                    # one message written as two records
    jsonl(proj / "sess1" / "subagents" / "agent-1.jsonl", [claude_msg("s1", "b", inp=1, out=1, ts=today)])
    r = usage.collect()
    su = r[("claude", "sess1")]
    day = time.strftime("%Y-%m-%d")
    t = su.days[day]
    assert (t.fresh, t.cached, t.out) == (16, 100, 8)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(claude_msg("m2", "c", inp=1, out=1, ts=today)) + "\n")
    assert usage.collect()[("claude", "sess1")].days[day].out == 9
    assert usage.human(1_234_567) == "1.2M"


def test_saved_prompts_merge_with_settings():
    assert "Status" in prompts.all_()
    prompts.save("Ship", "Ship it.")
    assert prompts.all_()["Ship"] == "Ship it."


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                   check=True, capture_output=True)


def test_worktree_finish_refuses_dirty_and_removes_clean(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "feat", str(wt))
    _git(wt, "commit", "-q", "--allow-empty", "-m", "work")
    (wt / "draft.txt").write_text("x")
    st = worktrees.status(str(wt), str(repo), [], [])
    assert st.branch == "feat" and st.base == "main" and st.ahead == 1 and len(st.dirty) == 1
    assert not st.removable and worktrees.remove(st, str(repo)).startswith("✗")
    (wt / "draft.txt").unlink()
    st = worktrees.status(str(wt), str(repo), ["LEAD"], [])
    assert not st.removable and "agent" in st.blockers[0]
    st = worktrees.status(str(wt), str(repo), [], [])
    assert st.removable
    assert worktrees.remove(st, str(repo)).startswith("⎇ removed")
    assert not wt.exists()
    assert "feat" in subprocess.run(["git", "-C", str(repo), "branch"], capture_output=True, text=True).stdout


def _mock_server(responses):
    """A local HTTP server that records requests and answers GETs from `responses`."""
    import http.server
    import threading
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def _reply(self, body=b'{"ok": true}'):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            got.append(("POST", self.path, self.rfile.read(n).decode()))
            self._reply()

        def do_GET(self):
            got.append(("GET", self.path, ""))
            self._reply(responses.get(self.path.split("?")[0], b'{"ok": true}'))

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


def test_alert_channels_telegram_webhook_whatsapp_and_setup(tmp_path):
    upd = json.dumps({"ok": True, "result": [{"message": {"chat": {"id": 4242, "first_name": "Mo"}}}]}).encode()
    srv, got = _mock_server({"/botTOKEN/getUpdates": upd})
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        cfg = tmp_path / "cfg" / "navigator.toml"
        cfg.write_text(cfg.read_text() + f'\n[alerts]\ntelegram_api = "{base}"\nwhatsapp_api = "{base}/wa"\n',
                       encoding="utf-8")
        settings.load.cache_clear()
        assert not alerts.enabled()
        assert alerts.telegram_find_chat("TOKEN") == ("4242", "Mo")
        alerts.save({"telegram_bot_token": "TOKEN", "telegram_chat_id": "4242",
                     "webhook_url": f"{base}/hook/discord", "whatsapp_phone": "+491", "whatsapp_apikey": "K"})
        assert "telegram_api" in cfg.read_text()                     # save keeps the other keys
        assert alerts.channels() == {"telegram": True, "ntfy": False, "webhook": True, "whatsapp": True}
        assert alerts.send("T", "B") == {"telegram": True, "webhook": True, "whatsapp": True}
        posts = {path: body for m, path, body in got if m == "POST"}
        assert json.loads(posts["/botTOKEN/sendMessage"]) == {"chat_id": "4242", "text": "T\nB",
                                                               "disable_web_page_preview": True}
        assert json.loads(posts["/hook/discord"]) == {"content": "**T**\nB"}
        wa = next(path for m, path, _ in got if path.startswith("/wa"))
        assert "phone=%2B491" in wa and "apikey=K" in wa
    finally:
        srv.shutdown()
        srv.server_close()


def test_telegram_setup_explains_a_missing_start(tmp_path):
    srv, _ = _mock_server({"/botT/getUpdates": b'{"ok": true, "result": []}'})
    try:
        cfg = tmp_path / "cfg" / "navigator.toml"
        cfg.write_text(cfg.read_text() + f'\n[alerts]\ntelegram_api = "http://127.0.0.1:{srv.server_port}"\n',
                       encoding="utf-8")
        settings.load.cache_clear()
        chat, why = alerts.telegram_find_chat("T")
        assert chat == "" and "Start" in why
    finally:
        srv.shutdown()
        srv.server_close()


def test_preview_drops_the_cli_input_box_and_status_line():
    from navigator.app import conversation_lines
    raw = "\n".join(["● Done: tests pass.", "", "  ⎿ 81 passed", "─" * 40 + " NAME ─",
                     "❯ ", "  Model: Opus | [███░░] 36%", "  ⏵⏵ bypass permissions on"])
    assert conversation_lines(raw) == ["● Done: tests pass.", "  ⎿ 81 passed"]
    assert conversation_lines("just text") == ["just text"]


def test_prompt_options_reads_a_permission_prompt():
    from navigator.app import prompt_options
    raw = "\n".join(["Bash command", "  rm -rf build", "Do you want to proceed?",
                     "❯ 1. Yes", "  2. Yes, and don't ask again for rm commands", "  3. No, and tell Claude (esc)"])
    assert prompt_options(raw) == [("1", "Yes"), ("2", "Yes, and don't ask again for rm commands"),
                                   ("3", "No, and tell Claude (esc)")]
    assert prompt_options("1. only one line") == []
    assert prompt_options("step 1. first\nnothing else") == []


def test_preview_drops_both_rules_of_the_input_box():
    from navigator.app import conversation_lines
    raw = "\n".join(["● It asks: upgrade to 64 GB?", "─" * 40 + " STORAGE ─", "❯ ", "─" * 50,
                     "  Model: Opus | 36%"])
    assert conversation_lines(raw) == ["● It asks: upgrade to 64 GB?"]


def test_preview_keeps_a_rule_inside_the_answer():
    from navigator.app import conversation_lines
    raw = "\n".join(["Part one", "─" * 40, "Part two", "", "─" * 40 + " X ─", "❯ ", "─" * 40, "status"])
    assert conversation_lines(raw) == ["Part one", "Part two"]


def test_alerts_cover_agents_that_need_a_reply(tmp_path):
    import json as _json
    cfg = tmp_path / "cfg" / "navigator.toml"
    cfg.write_text(cfg.read_text() + '\n[alerts]\nntfy_topic = "t"\nblocked_minutes = 10\n', encoding="utf-8")
    settings.load.cache_clear()
    (settings.state_dir() / "replies.json").write_text(_json.dumps({
        "w1:p2": {"name": "STORAGE", "workspace_id": "w1", "why": "needs a reply"},
        "w1:p3": {"name": "GONE", "workspace_id": "w1", "why": "needs a reply"}}), encoding="utf-8")
    snap = {"workspaces": [{"workspace_id": "w1", "label": "AlgoTrader"}],
            "agents": [{"pane_id": "w1:p2", "workspace_id": "w1", "agent_status": "idle"},
                       {"pane_id": "w1:p3", "workspace_id": "w1", "agent_status": "working"}]}
    t0 = time.time()
    assert alerts.check_waiting(snap, now=t0, sender=lambda *a: None) == []
    # only the one still idle counts: the other was answered and works again
    assert alerts.check_waiting(snap, now=t0 + 601, sender=lambda *a: None) == ["STORAGE needs a reply"]


def test_alerts_cover_open_questions_and_unseen_done(tmp_path):
    import json as _json
    cfg = tmp_path / "cfg" / "navigator.toml"
    cfg.write_text(cfg.read_text() + '\n[alerts]\nntfy_topic = "t"\nblocked_minutes = 10\n', encoding="utf-8")
    settings.load.cache_clear()
    (settings.state_dir() / "replies.json").write_text(_json.dumps({
        "w1:p2": {"name": "LEAD", "workspace_id": "w1", "why": "asks you a question"},
        "w1:p3": {"name": "DATA", "workspace_id": "w1", "why": "needs a reply"}}), encoding="utf-8")
    # herdr shows a question dialog as "working" (background agents) or "done"
    snap = {"workspaces": [{"workspace_id": "w1", "label": "AlgoTrader"}],
            "agents": [{"pane_id": "w1:p2", "workspace_id": "w1", "agent_status": "working"},
                       {"pane_id": "w1:p3", "workspace_id": "w1", "agent_status": "done"}]}
    t0 = time.time()
    assert alerts.check_waiting(snap, now=t0, sender=lambda *a: None) == []
    got = alerts.check_waiting(snap, now=t0 + 601, sender=lambda *a: None)
    assert sorted(got) == ["DATA needs a reply", "LEAD asks you a question"]


def test_context_levels_by_tokens_and_fill():
    from navigator.insight import Context
    assert Context(150_000, 1_000_000).level == "ok"
    assert Context(250_000, 1_000_000).level == "warn"
    assert Context(720_000, 1_000_000).level == "full"
    assert Context(150_000, 200_000).level == "warn"   # 75% of a small window
    assert Context(180_000, 200_000).level == "full"   # 90%
    assert Context(141_000, 1_000_000).bar() == "▰▱▱▱" and Context(1_000, 1_000_000).bar() == "▰▱▱▱"
    assert Context(418_000, 1_000_000).bar() == "▰▰▱▱" and Context(914_000, 1_000_000).bar() == "▰▰▰▰"
    assert Context(312_000, 1_000_000).short == "312K" and Context(1_200_000, 2_000_000).short == "1.2M"


def test_sidebar_marks_full_context():
    from types import SimpleNamespace as NS
    from navigator.insight import Context
    from navigator.sync import ctx_mark
    assert ctx_mark(NS(context=None)) == ""
    assert ctx_mark(NS(context=Context(100_000, 1_000_000))) == "▰▱▱▱"
    assert ctx_mark(NS(context=Context(250_000, 1_000_000))) == "▰▱▱▱⠀"   # same bar, yellow
    assert ctx_mark(NS(context=Context(800_000, 1_000_000))) == "▰▰▰▱⠀⠀"


def test_idle_for_an_hour_is_inactive(tmp_path, monkeypatch):
    import os, time
    from navigator import model
    old, new = tmp_path / "old.jsonl", tmp_path / "new.jsonl"
    old.write_text("{}"); new.write_text("{}")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    monkeypatch.setattr(model, "cfg_inactive_after", lambda: 3600.0)
    assert time.time() - model._mtime(str(old)) > model.cfg_inactive_after()
    assert time.time() - model._mtime(str(new)) < model.cfg_inactive_after()
    assert model.RANK_ORDER["inactive"] > model.RANK_ORDER["idle"]
    assert model.summarize(model.Counter({"idle": 1, "inactive": 2})) == "○1 ◌2"


def test_sync_labels_inactive_without_question(tmp_path, monkeypatch):
    """An inactive agent gets herdr's "inactive" state label, and no question toast (it has no question)."""
    from navigator import herdr, model, settings, sync
    monkeypatch.setattr(settings, "state_dir", lambda: tmp_path)
    (tmp_path / "replies.json").write_text("{}")
    from types import SimpleNamespace as NS
    proj = NS(root=str(tmp_path), name="p", label="p", worktree="")
    a = model.Agent(cli="claude", status="inactive", project=proj, pane_id="w1:p1", workspace_id="w1", name="S")
    world = model.World([], [a], [], [], {}, "", {}, "", {})
    monkeypatch.setattr(model, "build", lambda *k, **kw: world)
    calls = []
    monkeypatch.setattr(herdr, "request", lambda m, p, **kw: calls.append((m, p)) or {})
    monkeypatch.setattr(herdr, "run", lambda *args, **kw: calls.append(args) or {})
    monkeypatch.setattr(sync, "agent_tree", lambda agents, folded=None: [])
    sync.sync(force=True)
    labels = [p for m, p in calls if m == "pane.report_metadata" and "state_labels" in p]
    assert labels and labels[0]["state_labels"] == {"idle": "inactive"}
    assert not any(c and c[0] == "notification" for c in calls)


def test_flush_splits_reports_at_herdrs_token_limit(monkeypatch):
    from navigator import herdr, sync
    calls = []
    monkeypatch.setattr(herdr, "report_metadata", lambda kind, target, src, toks, seq: calls.append(len(toks)))
    sync._PENDING.clear()
    for i in range(19):
        sync._report("workspace", "w1", f"t{i}", "x", {}, {}, "")
    sent = {}
    sync._flush(sent)
    assert calls == [16, 3] and len(sent) == 19


def test_context_after_compact_uses_post_tokens(tmp_path):
    import json
    from navigator import insight
    f = tmp_path / "t.jsonl"
    ans = {"type": "assistant", "message": {"model": "claude-opus-5-5", "usage": {
        "input_tokens": 10, "cache_read_input_tokens": 800_000, "cache_creation_input_tokens": 0}}}
    cut = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"preTokens": 800_010, "postTokens": 21_000}}
    f.write_text(json.dumps(ans) + "\n" + json.dumps(cut) + "\n", encoding="utf-8")
    c = insight.context("claude", str(f))
    assert c.used == 21_000 and c.window == 1_000_000 and c.level == "ok"
    later = dict(ans, message=dict(ans["message"], usage={"input_tokens": 5, "cache_read_input_tokens": 30_000}))
    f.write_text(f.read_text(encoding="utf-8") + json.dumps(later) + "\n", encoding="utf-8")
    assert insight.context("claude", str(f)).used == 30_005   # the next answer's usage wins again
