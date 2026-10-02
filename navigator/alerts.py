"""Phone alerts through ntfy (https://ntfy.sh): an agent waiting on you too long, the logon restore.

Off until `[alerts] ntfy_topic` is set. Messages go only to that topic on that server, and
carry only the session's name, its project and how long it waited.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import settings

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def enabled() -> bool:
    return bool(str(settings.load().alerts.get("ntfy_topic", "")).strip())


def send(title: str, body: str, tags: str = "robot", priority: str = "default") -> bool:
    cfg = settings.load().alerts
    topic = str(cfg.get("ntfy_topic", "")).strip()
    if not topic:
        return False
    url = str(cfg.get("ntfy_server", "https://ntfy.sh")).rstrip("/") + "/" + topic
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST",
                                 headers={"Title": title.encode("utf-8").decode("latin-1", "replace"),
                                          "Tags": tags, "Priority": priority})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return 200 <= r.status < 300
    except OSError:
        return False


def send_background(title: str, body: str, tags: str = "robot") -> None:
    """Fire and forget, so a 3-second status line never waits on the network."""
    root = Path(__file__).resolve().parent.parent
    py = root / ".venv" / ("Scripts/pythonw.exe" if os.name == "nt" else "bin/python")
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONUTF8": "1"}
    subprocess.Popen([str(py), "-m", "navigator.alerts", "send", title, body, tags], cwd=str(root), env=env,
                     creationflags=_DETACHED | _NO_WINDOW, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _state_path() -> Path:
    return settings.state_dir() / "alerts-state.json"


def check_waiting(snap: dict, now: float | None = None, sender=send_background) -> list[str]:
    """Called with each status-line snapshot: alert once per wait that passes the limit."""
    if not enabled():
        return []
    limit = float(settings.load().alerts.get("blocked_minutes", 10)) * 60
    if limit <= 0:
        return []
    now = now or time.time()
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    labels = {w["workspace_id"]: w.get("label", "") for w in snap.get("workspaces", [])}
    waiting = {a["pane_id"]: a for a in snap.get("agents", []) if a.get("agent_status") == "blocked"}
    sent = []
    new_state = {}
    for pane, a in waiting.items():
        st = dict(state.get(pane) or {"since": now, "alerted": False})
        if not st["alerted"] and now - st["since"] >= limit:
            name = a.get("terminal_title_stripped") or a.get("agent") or pane
            mins = int((now - st["since"]) // 60)
            title = f"{name[:40]} waits for you"
            body = f"{labels.get(a.get('workspace_id'), '')}: waiting {mins} min"
            sender(title, body, "warning")
            st["alerted"] = True
            sent.append(title)
        new_state[pane] = st
    if new_state != state:
        _state_path().write_text(json.dumps(new_state), encoding="utf-8")
    return sent


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "send":
        send(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "robot")
    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        print("sent" if send("herdr navigator", "Test alert: phone alerts work.", "white_check_mark")
              else "not sent (set [alerts] ntfy_topic, check the server)")
