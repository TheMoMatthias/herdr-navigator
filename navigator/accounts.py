"""Sign in / sign out / switch per CLI, then relaunch that CLI's sessions under the new account.

A running agent reads its credentials when it starts, so after switching account (or after a
sign-in expired) the open sessions keep the old one until they are restarted. Every change of
login first keeps the live one in its account's profile (profiles.keep). Switching to a saved
profile whose login no longer works opens the sign-in instead. Sign in opens the CLI's login
command in a herdr tab and starts a watcher: once the CLI rewrites its credentials, the new
login is saved as that account's profile and the sessions relaunch on it (relaunch_when_ready:
idle ones at once, busy ones as soon as their turn ends).
"""
from __future__ import annotations

import json
import os
import functools
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import herdr, model, profiles, settings

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


@functools.lru_cache(maxsize=None)
def _which(exe: str) -> bool:
    """Is this program installed? A PATH scan costs ~0.1 s each on Windows and is asked while the
    Navigator draws, so answers are kept for a day (and dropped when PATH changes)."""
    from . import jsonfile
    f = settings.state_dir() / "which-cache.json"
    import zlib  # hash() of a str differs per process
    key = f"{zlib.crc32(os.environ.get('PATH', '').encode()):x}|{exe}"
    try:
        cache = jsonfile.read(f, {}) or {}
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(key)
    if hit and time.time() - hit[1] < 86400:
        return bool(hit[0])
    found = bool(shutil.which(exe))
    cache = {k: v for k, v in cache.items() if time.time() - v[1] < 86400}
    cache[key] = [found, time.time()]
    try:
        jsonfile.write(f, cache)
    except OSError:
        pass
    return found


def clis() -> dict[str, dict]:
    """CLIs with a login command whose program is installed."""
    out = {}
    for cli, c in settings.load().login.items():
        cmd = c.get("login", "")
        try:
            exe = shlex.split(cmd)[0] if cmd else ""
        except ValueError:
            continue
        if exe and _which(exe):
            out[cli] = c
    return out


def watch_file(cli: str) -> Path | None:
    w = settings.load().login.get(cli, {}).get("watch", "")
    return settings.user_path(w) if w else None


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


def sign_in(cli: str, expect: str = "") -> str:
    """Open the CLI's login in a tab; `expect` names the account the sign-in is for (a profile
    whose saved login stopped working)."""
    c = settings.load().login.get(cli, {})
    if not c.get("login"):
        return f"✗ no login command for {cli}"
    profiles.keep(cli, status(cli))  # the login being replaced stays switchable
    before = stamp(cli)
    run_in_tab(f"🔑 {cli}", c["login"])
    _spawn_watch(cli, before)
    who = f" as {expect}" if expect else ""
    return f"🔑 {cli}: sign in{who} in the new tab. The sessions relaunch on their own once it is done."


def sign_out(cli: str) -> str:
    c = settings.load().login.get(cli, {})
    if not c.get("logout"):
        return f"✗ no logout command for {cli}"
    profiles.keep(cli, status(cli))
    run_in_tab(f"🔑 {cli}", c["logout"])
    return f"{cli}: signed out. Sign in again with the account you want."


def switch(cli: str, name: str) -> str:
    """Switch to saved profile `name`. When its saved login has expired or does not come up as
    its account, open the sign-in for that account instead; else relaunch the sessions on it."""
    msg = profiles.switch(cli, name, status(cli))
    if not msg.startswith("⇄"):
        return msg
    want = profiles.email(profiles.label(cli, name))
    now = status(cli)
    if (not profiles.usable(cli, name) or now.startswith("not ") or now in ("?", "")
            or (want and profiles.email(now) and profiles.email(now) != want)):
        return sign_in(cli, want or name).replace("🔑", "🔑 saved login expired:", 1)
    spawn_relaunch(cli)
    return f"{msg}: relaunching its sessions (busy ones once their turn ends)"


def _spawn(*args: str) -> None:
    root = Path(__file__).resolve().parent.parent
    py = root / ".venv" / ("Scripts/pythonw.exe" if os.name == "nt" else "bin/python")
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONUTF8": "1"}
    subprocess.Popen([str(py), "-m", "navigator.accounts", *args], cwd=str(root), env=env,
                     creationflags=_DETACHED | _NO_WINDOW, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def spawn_relaunch(cli: str) -> None:
    _spawn("relaunch", cli)


RELAUNCH_WAIT = 4 * 3600  # how long busy sessions are waited for before the relauncher gives up
RELAUNCH_POLL = 20.0


def relaunch_when_ready(cli: str, wait: float = RELAUNCH_WAIT, poll: float = RELAUNCH_POLL) -> str:
    """Relaunch every session of `cli` that still runs on the previous login: idle ones now, busy
    ones as soon as their turn ends (never mid-turn). One runner per CLI at a time."""
    from . import restore
    lock = settings.state_dir() / f"relaunch-{cli}.lock"
    try:
        if time.time() - lock.stat().st_mtime < 2 * poll:
            return "another relaunch is running"  # it touches its lock every poll
    except OSError:
        pass
    lock.write_text(str(os.getpid()), encoding="utf-8")
    done: list[str] = []
    try:
        end = time.time() + wait
        first = True
        while True:
            t = restore.relaunch_targets(cli)
            if first and (t["restart"] or t["busy"] or t["elsewhere"]):
                busy = f"; {len(t['busy'])} busy, relaunched when their turn ends" if t["busy"] else ""
                away = f"; {len(t['elsewhere'])} in other windows: restart those yourself" if t["elsewhere"] else ""
                herdr.run("notification", "show", f"{cli}: new login", "--body",
                          f"Relaunching {len(t['restart'])} session(s){busy}{away}.", check=False)
            first = False
            if t["restart"]:
                restore.relaunch_all(cli)
                done += [a.display for a in t["restart"]]
            if not t["busy"] or time.time() > end:
                break
            lock.touch()
            time.sleep(poll)
    finally:
        lock.unlink(missing_ok=True)
    if done:
        herdr.run("notification", "show", f"{cli}: sessions relaunched", "--body",
                  ", ".join(done)[:200], check=False)
    return f"relaunched {len(done)}"


def keep_fresh(seen: dict[str, float]) -> list[str]:
    """The daemon's check (every few seconds, a file stat per CLI): when a CLI rewrote its login,
    save it into its account's profile, so a later switch back never restores a refresh token
    that has since been replaced. A different account than last time (signed in outside the
    Navigator) counts as a new login. Skipped while sessions started on an earlier login still
    run: their own token refresh may be what was written. Returns the CLIs whose profile it kept."""
    from . import restore
    kept = []
    for cli in clis():
        if not profiles.supported(cli):
            continue
        st = stamp(cli)
        first, changed = cli not in seen, seen.get(cli) != st
        seen[cli] = st
        if first or not changed or not st:
            continue
        who = status(cli)
        mail = profiles.email(who)
        if not mail:
            continue
        if mail != profiles.last_email(cli):
            if profiles.last_email(cli):
                profiles.mark_login(cli, who)  # another account, signed in some other way
            else:
                profiles.set_email(cli, mail)
        elif profiles.login_at(cli) and any(not restore.on_current_login(a.pane_id, cli)
                 for a in model.build(with_sessions=False).agents if a.cli == cli and a.in_herdr):
            continue
        if profiles.keep(cli, who):
            kept.append(cli)
    return kept


def _spawn_watch(cli: str, before: float) -> None:
    _spawn("watch", cli, repr(before))


def watch(cli: str, before: float, timeout: float = 600) -> bool:
    """Wait for the credentials file to change, then keep the new login and relaunch on it."""
    end = time.time() + timeout
    while time.time() < end:
        if stamp(cli) > before:
            time.sleep(2)  # let the CLI finish writing
            who = status(cli)
            if who.startswith("not "):
                before = stamp(cli)  # a sign-out or a failed attempt: keep waiting
                continue
            profiles.keep(cli, who)  # the new account is a switchable profile from now on
            profiles.mark_login(cli, who)
            relaunch_when_ready(cli)
            return True
        time.sleep(2)
    return False


def offer_relaunch(cli: str) -> None:
    """Open the Navigator in a new tab, on Startup, with the relaunch sheet for `cli` ("" = every CLI)."""
    from . import restore
    world = model.build(with_sessions=False)
    t = restore.relaunch_targets(cli, world)
    n = len(t["restart"]) + len(t["busy"])
    if not n:
        herdr.run("notification", "show", f"Signed in to {cli}" if cli else "Relaunch", "--body",
                  "Every open session already runs on the current sign-in." if t.get("current")
                  else "No open sessions to relaunch.", check=False)
        return
    entry = "navigator" if os.name == "nt" else "navigator-unix"
    herdr.run("plugin", "pane", "open", "--plugin", settings.PLUGIN_ID, "--entrypoint", entry,
              "--placement", "tab", "--focus", "--env", "NAV_TAB=startup", "--env", f"NAV_RELAUNCH={cli}",
              check=False)


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "watch":
        watch(sys.argv[2], float(sys.argv[3]))
    elif len(sys.argv) > 2 and sys.argv[1] == "relaunch":
        relaunch_when_ready(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "offer":  # run accounts offer [cli]: the relaunch sheet
        offer_relaunch(sys.argv[2] if len(sys.argv) > 2 else "")
    elif len(sys.argv) > 2 and sys.argv[1] in ("in", "out", "status"):
        fn = {"in": sign_in, "out": sign_out, "status": status}[sys.argv[1]]
        print(fn(sys.argv[2]))
