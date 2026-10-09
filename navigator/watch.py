"""Watchers that run in the background after you set them in the Navigator.

* done   — tell me when this agent finishes (herdr toast, plus your phone if alerts are set up);
* chain  — when this agent finishes, hand its answer to another agent (the hand-off template);
* errors — tell me when this pane prints a failure (FAILED, a traceback, `Error:` …) in NEW output.

Each watcher is a small detached process listed in `watches.json`, so the Navigator can show and
cancel it. A done/chain watcher ends after it fired; an error watcher runs until you stop it or the
pane closes.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import herdr, insight, jsonfile, settings

KINDS = {"done": "🔔", "chain": "⛓", "errors": "🚨"}
ERROR_RE = re.compile(r"(\bFAILED\b|\bFAIL:|Traceback \(most recent call last\)|^\s*\w*Error:|\bERROR\b|"
                      r"\bpanic:|\bException\b|npm ERR!|\bfatal:)")
COOLDOWN = 120  # seconds between two error alerts of one pane


def _file() -> Path:
    return settings.state_dir() / "watches.json"


def load() -> dict:
    try:
        return jsonfile.read(_file(), {})
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    jsonfile.write(_file(), d, indent=1)


def _alive(pid: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def active() -> dict:
    """Watches whose process still runs (dead ones are dropped)."""
    d = load()
    live = {k: w for k, w in d.items() if _alive(w.get("pid", 0))}
    if live != d:
        _save(live)
    return live


def for_pane(pane: str, watches: dict | None = None) -> list[tuple[str, dict]]:
    return [(k, w) for k, w in (active() if watches is None else watches).items() if w.get("pane") == pane]


def start(kind: str, pane: str, name: str, target: str = "", target_name: str = "", note: str = "") -> str:
    """Start a watcher; returns a message for the user."""
    wid = uuid.uuid4().hex[:8]
    d = load()
    d[wid] = {"kind": kind, "pane": pane, "name": name, "target": target, "target_name": target_name,
              "note": note, "started": time.time(), "pid": 0}
    _save(d)
    root = Path(__file__).resolve().parent.parent
    py = root / ".venv" / ("Scripts/pythonw.exe" if os.name == "nt" else "bin/python")
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    proc = subprocess.Popen([str(py), "-m", "navigator.watch", "run", wid], cwd=str(root),
                            env={**os.environ, "PYTHONPATH": str(root), "PYTHONUTF8": "1"}, creationflags=flags,
                            close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=os.name != "nt")
    d = load()
    if wid in d:
        d[wid]["pid"] = proc.pid
        _save(d)
    return {"done": f"🔔 You'll be told when {name} finishes.",
            "chain": f"⛓ When {name} finishes, its answer goes to {target_name}.",
            "errors": f"🚨 Watching {name} for failures."}[kind]


def cancel(wid: str) -> None:
    d = load()
    w = d.pop(wid, None)
    _save(d)
    if w and w.get("pid"):
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(w["pid"]), "/F"], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.kill(w["pid"], 15)
        except OSError:
            pass


def describe(w: dict) -> str:
    return {"done": "🔔 tells you when it finishes",
            "chain": f"⛓ hands its answer to {w.get('target_name', '?')} when it finishes",
            "errors": "🚨 watched for failures"}.get(w.get("kind", ""), "")


# ---- the watcher processes ---------------------------------------------------------------------
def notify(title: str, body: str, sound: str = "done", phone: bool = True) -> None:
    herdr.run("notification", "show", title[:80], "--body", body[:200], "--sound", sound, check=False)
    if phone:
        try:
            from . import alerts
            alerts.send(title, body)
        except Exception:
            pass


def _agent(pane: str) -> dict | None:
    try:
        return herdr.run("agent", "get", pane).get("agent", {})
    except herdr.HerdrError:
        return None


def _status(pane: str) -> str:
    a = _agent(pane)
    return "gone" if a is None else insight.agent_status(a)


def _wait_codex(pane: str, *until: str, every: float = 2.0) -> str:
    """herdr reports Codex as "unknown" after a turn, so `agent wait --until idle` would never
    return: poll the status derived from the rollout instead."""
    while True:
        st = _status(pane)
        if st == "gone" or st in until:
            return st
        time.sleep(every)


def _wait(pane: str, *until: str) -> str:
    """Block until the agent reaches one of the states; returns it ('gone' when the pane closed)."""
    a = _agent(pane)
    if a is None:
        return "gone"
    if a.get("agent") == "codex":
        return _wait_codex(pane, *until)
    args = [a for u in until for a in ("--until", u)]
    try:
        res = herdr.run("agent", "wait", pane, *args, timeout=7 * 24 * 3600)
    except (herdr.HerdrError, subprocess.TimeoutExpired):
        return "gone"
    return (res.get("agent") or {}).get("agent_status", "") or _status(pane)


def _settled(pane: str) -> str:
    """Wait for the turn that is running (or the next one) to end."""
    if _status(pane) not in ("working",):
        if _wait(pane, "working") == "gone":
            return "gone"
    return _wait(pane, "idle", "done", "blocked")


def _answer(pane: str) -> tuple[str, str, str]:
    """(answer, cli, project label) of the agent in this pane, from its transcript."""
    from . import insight, model
    a = next((a for a in model.build().agents if a.pane_id == pane), None)
    if not a:
        return "", "", ""
    return insight.last_answer(a.cli, a.transcript), a.cli, a.project.label


def run_done(w: dict) -> None:
    st = _settled(w["pane"])
    if st == "gone":
        return
    if st == "blocked":
        notify(f"{w['name']} waits for you", "It asks for an approval or an answer.", "request")
        return
    answer = _answer(w["pane"])[0]
    notify(f"{w['name']} finished", (answer.strip().splitlines() or [""])[-1][:180])


def run_chain(w: dict) -> None:
    while True:
        st = _settled(w["pane"])
        if st == "gone":
            return
        if st == "blocked":  # it asks something first: tell you, then keep waiting for the real end
            notify(f"{w['name']} waits for you", f"Then its answer goes to {w['target_name']}.", "request")
            if _wait(w["pane"], "working") == "gone":
                return
            continue
        break
    answer, cli, project = _answer(w["pane"])
    if not answer:
        notify(f"{w['name']} finished", f"No answer found to hand to {w['target_name']}.", "request")
        return
    text = settings.load().handoff.replace("{name}", w["name"]).replace("{cli}", cli) \
        .replace("{project}", project).replace("{answer}", answer)
    if w.get("note"):
        text = w["note"].strip() + "\n\n" + text
    try:
        herdr.run("agent", "prompt", w["target"], text, timeout=60)
        notify(f"{w['name']} → {w['target_name']}", "Its answer was handed over.", "done", phone=False)
    except herdr.HerdrError as e:
        notify(f"Hand-off to {w['target_name']} failed", str(e)[:180], "request")


def new_lines(before: list[str], now: list[str]) -> list[str]:
    """The lines of `now` that came after what `before` ended with (terminal output scrolls)."""
    if not before:
        return []
    tail = before[-3:]
    for i in range(len(now) - len(tail), -1, -1):
        if now[i:i + len(tail)] == tail:
            return now[i + len(tail):]
    return now  # scrolled past everything we saw: all of it is new


def failures(lines: list[str]) -> list[str]:
    return [ln.strip() for ln in lines if ERROR_RE.search(ln)]


def run_errors(w: dict, interval: float = 3.0) -> None:
    def read() -> list[str] | None:
        try:
            raw = herdr.run("pane", "read", w["pane"], "--source", "recent-unwrapped", "--lines", "120").get("raw", "")
        except herdr.HerdrError:
            return None
        return [ln for ln in raw.splitlines() if ln.strip()]
    seen = read()
    if seen is None:
        return
    last = 0.0
    while True:
        time.sleep(interval)
        now = read()
        if now is None:
            return  # the pane closed
        bad = failures(new_lines(seen, now))
        seen = now
        if bad and time.time() - last > COOLDOWN:
            last = time.time()
            notify(f"{w['name']}: failure", bad[-1][:180], "request")


def run(wid: str) -> None:
    w = load().get(wid)
    if not w:
        return
    try:
        {"done": run_done, "chain": run_chain, "errors": run_errors}[w["kind"]](w)
    finally:
        d = load()
        d.pop(wid, None)
        _save(d)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "run":
        run(sys.argv[2])
