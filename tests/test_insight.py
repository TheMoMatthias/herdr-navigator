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
