"""Sign in / sign out per CLI, then relaunch that CLI's sessions under the new account.

A running agent reads its credentials when it starts, so after switching account (or after a
sign-in expired) the open sessions keep the old one until they are restarted. Sign in opens the
CLI's login command in a herdr tab and starts a watcher: once the CLI rewrites its credentials
file, the Navigator opens on its Startup tab with the relaunch offer.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import herdr, model, settings

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def clis() -> dict[str, dict]:
    """CLIs with a login command whose program is installed."""
    out = {}
    for cli, c in settings.load().login.items():
        cmd = c.get("login", "")
        try:
            exe = shlex.split(cmd)[0] if cmd else ""
        except ValueError:
            continue
        if exe and shutil.which(exe):
            out[cli] = c
    return out


def watch_file(cli: str) -> Path | None:
    w = settings.load().login.get(cli, {}).get("watch", "")
    return Path(os.path.expanduser(w)) if w else None


def stamp(cli: str) -> float:
    p = watch_file(cli)
    try:
        return p.stat().st_mtime if p else 0.0
    except OSError:
        return 0.0


def status(cli: str) -> str:
    """One line: who is signed in, if the CLI can say."""
    cmd = settings.load().login.get(cli, {}).get("status", "")
    if not cmd:
        return "signed in" if stamp(cli) else "not signed in"
    try:
        args = cmd if os.name == "nt" else shlex.split(cmd)  # Windows: let cmd find .cmd shims
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=15, creationflags=_NO_WINDOW, shell=os.name == "nt")
    except (OSError, subprocess.SubprocessError):
        return "?"
    out = (r.stdout or r.stderr).strip()
    try:
        d = json.loads(out)
        if not d.get("loggedIn", True):
            return "not signed in"
        who = d.get("email") or d.get("account") or ""
        plan = d.get("subscriptionType") or d.get("authMethod") or ""
        return " · ".join(x for x in (who, plan) if x) or "signed in"
    except ValueError:
        return out.splitlines()[0][:60] if out else "?"


def _here_workspace() -> tuple[str, str]:
    snap = herdr.snapshot()
    ws = snap.get("focused_workspace_id", "")
    pane = next((p for p in snap.get("panes", []) if p.get("pane_id") == snap.get("focused_pane_id")), {})
    return ws, pane.get("cwd") or str(Path.home())


def run_in_tab(label: str, command: str) -> str:
    ws, cwd = _here_workspace()
    res = herdr.create_tab(ws, cwd, label=label, focus=True)
    pane = (res.get("root_pane") or res.get("pane") or {}).get("pane_id", "")
    herdr.pane_run(pane, command)
    return pane


def sign_in(cli: str) -> str:
    c = settings.load().login.get(cli, {})
    if not c.get("login"):
        return f"✗ no login command for {cli}"
    before = stamp(cli)
    run_in_tab(f"🔑 {cli}", c["login"])
    _spawn_watch(cli, before)
    return f"🔑 {cli}: finish the sign-in in the new tab. The Navigator comes back when it is done."


def sign_out(cli: str) -> str:
    c = settings.load().login.get(cli, {})
    if not c.get("logout"):
        return f"✗ no logout command for {cli}"
    run_in_tab(f"🔑 {cli}", c["logout"])
    return f"{cli}: signed out. Sign in again with the account you want."


def _spawn_watch(cli: str, before: float) -> None:
    root = Path(__file__).resolve().parent.parent
    py = root / ".venv" / ("Scripts/pythonw.exe" if os.name == "nt" else "bin/python")
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONUTF8": "1"}
    subprocess.Popen([str(py), "-m", "navigator.accounts", "watch", cli, repr(before)], cwd=str(root), env=env,
                     creationflags=_DETACHED | _NO_WINDOW, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def watch(cli: str, before: float, timeout: float = 600) -> bool:
    """Wait for the credentials file to change, then offer the relaunch."""
    end = time.time() + timeout
    while time.time() < end:
        if stamp(cli) > before:
            time.sleep(2)  # let the CLI finish writing
            offer_relaunch(cli)
            return True
        time.sleep(2)
    return False


def offer_relaunch(cli: str) -> None:
    """Open the Navigator in a new tab, on Startup, with the relaunch sheet for `cli`."""
    from . import restore
    world = model.build(with_sessions=False)
    t = restore.relaunch_targets(cli, world)
    n = len(t["restart"]) + len(t["busy"])
    if not n:
        herdr.run("notification", "show", f"Signed in to {cli}", "--body",
                  "No open sessions of it to relaunch.", check=False)
        return
    entry = "navigator" if os.name == "nt" else "navigator-unix"
    herdr.run("plugin", "pane", "open", "--plugin", settings.PLUGIN_ID, "--entrypoint", entry,
              "--placement", "tab", "--focus", "--env", "NAV_TAB=startup", "--env", f"NAV_RELAUNCH={cli}",
              check=False)


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "watch":
        watch(sys.argv[2], float(sys.argv[3]))
    elif len(sys.argv) > 2 and sys.argv[1] in ("in", "out", "status"):
        fn = {"in": sign_in, "out": sign_out, "status": status}[sys.argv[1]]
        print(fn(sys.argv[2]))
