"""Controlling herdr itself (Settings › System): what runs, the update, stop and restart.

herdr ends everything a pane or a key command started, and `herdr update` refuses to run inside
herdr at all. So update, stop and restart run as processes Windows' own process service creates
(the way daemon.py starts itself): they belong to no pane, and outlive the server they stop.
Elsewhere a new session does the same job.

`herdr update` installs the new version and leaves the running server, and every agent in it,
alone; only a stop or a restart ends the panes (layout and agent conversations come back).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import herdr, settings

ROOT = Path(__file__).resolve().parent.parent
RELEASES = "https://api.github.com/repos/herdrdev/herdr/releases/latest"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DONE = "__DONE"  # the update log's last line: "__DONE ok" or "__DONE failed"


@dataclass
class Status:
    ok: bool
    why: str = ""
    client: str = ""        # installed version (what a new window runs)
    server: str = ""        # version of the running server ("" when none runs)
    running: bool = False
    stale: bool = False     # the running server is older than the installed binary


def binary() -> str:
    """The newest installed herdr: HERDR_BIN_PATH is the running (possibly old) server's."""
    name = "herdr.exe" if os.name == "nt" else "herdr"
    cur = Path.home() / ".herdr" / "packages" / "standalone" / "current" / name
    return str(cur) if cur.exists() else herdr.herdr_bin()


def _run(*args: str, timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run([binary(), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=_NO_WINDOW)


def parse_status(text: str) -> Status:
    """`herdr status` prints "client:" / "server:" / "update:" blocks of "  key: value" lines."""
    sec, kv = "", {}
    for ln in text.splitlines():
        if ln and not ln[0].isspace() and ln.rstrip().endswith(":"):
            sec = ln.strip()[:-1]
        elif ":" in ln and sec:
            k, v = ln.split(":", 1)
            kv[f"{sec}.{k.strip()}"] = v.strip()
    if "client.version" not in kv:
        return Status(False, "herdr did not say what it is running")
    running = kv.get("server.status") == "running"
    return Status(True, "", kv["client.version"], kv.get("server.version", "") if running else "", running,
                  kv.get("update.server_binary_stale") == "yes")


def status() -> Status:
    try:
        r = _run("status")
    except (OSError, subprocess.SubprocessError) as e:
        return Status(False, f"could not run herdr: {e}")
    return parse_status(r.stdout)


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def newer(latest: str, installed: str) -> bool:
    return bool(latest and installed) and _ver(latest) > _ver(installed)


def latest(timeout: float = 8) -> str:
    """The newest stable release on GitHub ("" when it can't be reached)."""
    try:
        req = urllib.request.Request(RELEASES, headers={"User-Agent": "herdr-navigator"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return str(json.load(r).get("tag_name", "")).lstrip("v")
    except (OSError, ValueError):
        return ""


def agents() -> tuple[int, int]:
    """(agents in herdr, of them working right now): what a stop or restart would end."""
    try:
        found = herdr.snapshot().get("agents", [])
    except Exception:
        return 0, 0
    working = sum(1 for a in found if isinstance(a, dict) and str(a.get("agent_status", a.get("status", ""))) == "working")
    return len(found), working


def update_log() -> Path:
    return settings.state_dir() / "herdr-update.log"


def _detached(cmdline: str) -> bool:
    """Run a command line as a process that belongs to no herdr pane."""
    if os.name == "nt":
        line = cmdline.replace("'", "''")
        ps = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
              f"@{{CommandLine='{line}'; CurrentDirectory=$env:USERPROFILE}}; exit $r.ReturnValue")
        try:
            r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=15, creationflags=_NO_WINDOW)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        subprocess.Popen(["sh", "-c", cmdline], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def _quote(p: str) -> str:
    return f'"{p}"'


def start_update() -> bool:
    """Install the newest herdr, logging to update_log(); its last line is DONE ok / DONE failed."""
    log = update_log()
    try:
        log.unlink(missing_ok=True)
    except OSError:
        pass
    h, lg = _quote(binary()), _quote(str(log))
    if os.name == "nt":
        return _detached(f'cmd.exe /d /c "({h} update && echo {DONE} ok || echo {DONE} failed) > {lg} 2>&1"')
    return _detached(f"({h} update && echo {DONE} ok || echo {DONE} failed) > {lg} 2>&1")


def update_progress() -> tuple[bool, bool, str]:
    """(finished, succeeded, the last thing herdr said) while or after start_update()."""
    try:
        lines = [ln.strip() for ln in update_log().read_text(encoding="utf-8", errors="replace").splitlines()
                 if ln.strip()]
    except OSError:
        return False, False, "starting…"
    if not lines:
        return False, False, "starting…"
    if lines[-1].startswith(DONE):
        said = [ln for ln in lines if not ln.startswith(DONE) and not ln.startswith("==>")]
        return True, lines[-1].endswith("ok"), (said[-1] if said else lines[-1])
    return False, False, lines[-1]


def stop() -> bool:
    """Stop the herdr server: every pane ends. The layout and agent conversations are saved."""
    if os.name == "nt":
        return _detached(f'cmd.exe /d /c "{_quote(binary())} server stop"')
    return _detached(f"{_quote(binary())} server stop")


def can_restart() -> bool:
    """Restart opens a new terminal window with herdr: Windows Terminal (or a console) on Windows."""
    return os.name == "nt"


def restart(terminal: str = "") -> bool:
    """Stop the server, wait until it is gone, then open herdr again in a new terminal window."""
    if not can_restart():
        return False
    script = ROOT / "navigator" / "herdr_restart.ps1"
    cmd = (f'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File '
           f'{_quote(str(script))} -Herdr {_quote(binary())}' + (f" -Terminal {_quote(terminal)}" if terminal else ""))
    return _detached(cmd)


if __name__ == "__main__":
    s = status()
    print(s if not s.ok else f"client {s.client}, server {s.server or 'not running'}, stale={s.stale}, "
                             f"latest={latest() or '?'}")
    sys.exit(0)
