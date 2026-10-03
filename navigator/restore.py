"""Reopen sessions: the logon restore, "open ticked now", and relaunching sessions in place.

At logon (`restore boot`, started by the autostart entry): wait, start herdr in a terminal if it
is not running, wait until the startup hook has resumed every pane herdr restored (each gets the
session that ran there, named: from our record, else herdr's own agent_session for the pane),
then open every ticked session that is not already running anywhere, one per tab in its
project's workspace. Last, the guarantee check waits until every ticked and resumed session has
a live agent and names any that has not, in restore.log, a herdr notification and the phone
alert. `run.cmd restore check` runs that check by hand. Sessions with no conversation (a Claude
file holding only a title) are left out with that reason: `claude --resume` cannot open them.

Relaunch in place: stop the agent process in its pane and run its resume command in the same
pane, so the layout stays. Used after signing in to an account, and from the menu.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import herdr, jsonfile, launch, model, settings, startup
from .sessions import Session

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n"
    p = settings.state_dir() / "restore.log"
    try:
        if p.exists() and p.stat().st_size > 512_000:
            p.write_text("".join(p.read_text(encoding="utf-8").splitlines(True)[-2000:]), encoding="utf-8")
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def notify(title: str, body: str = "") -> None:
    args = ["notification", "show", title] + (["--body", body] if body else [])
    try:
        herdr.run(*args, check=False)
    except Exception:
        pass


# ---- what is running ----------------------------------------------------------------------------

def running_ids(snap: dict | None = None) -> set[str]:
    """Session ids open right now, or being resumed in their own pane at this moment."""
    ids = live_ids(snap)
    # sessions being resumed in their own panes right now (herdr may not have detected them yet)
    ids |= {e["sid"] for e in load_panes().values() if time.time() - e.get("resumed_at", 0) < 300}
    return ids


def live_ids(snap: dict | None = None) -> set[str]:
    """Session ids with a live agent: herdr panes (their reported session) and other terminals."""
    ids: set[str] = set()
    try:
        snap = snap if snap is not None else herdr.snapshot()
        for p in snap.get("panes", []):
            ref = p.get("agent_session") or {}
            # herdr keeps a pane's agent_session after a restart even though nothing runs there:
            # only a pane with a live agent counts as running
            if ref.get("value") and p.get("agent"):
                ids.add(str(ref["value"]))
    except Exception:
        pass
    try:
        for a in model.build(with_sessions=False).agents:
            if a.session_id:
                ids.add(a.session_id)
    except Exception:
        pass
    return ids


# ---- agent panes across herdr restarts ------------------------------------------------------------
# herdr keeps pane ids across a server restart (measured), but resumes agents without their name
# or launch options. So the plugin records which session runs in which pane, setup turns herdr's
# own resume off, and at server start each recorded pane gets its session back, named.

def server_id() -> str:
    """The running herdr server: its socket file holds "<pid>:<nonce>", rewritten at every start."""
    try:
        return Path(os.environ.get("HERDR_SOCKET_PATH") or herdr._default_socket()).read_text(
            encoding="utf-8", errors="replace").strip()[:80]
    except OSError:
        return ""


def has_conversation(s: Session | None) -> bool:
    """False for a Claude session file that holds only metadata (title, mode) and no message:
    `claude --resume` answers "No conversation found" for those."""
    if s is None or s.cli != "claude" or not s.path:
        return True
    try:
        with open(s.path, "rb") as fh:
            head = fh.read(4_000_000)
    except OSError:
        return True
    return b'"type":"user"' in head or b'"type":"assistant"' in head


def _server_key() -> str:
    """Pane ids repeat across herdr servers (named sessions): keep one record per server."""
    sock = os.environ.get("HERDR_SOCKET_PATH") or herdr._default_socket()
    if os.path.normcase(os.path.abspath(sock)) == os.path.normcase(os.path.abspath(herdr._default_socket())):
        return "default"
    import hashlib
    return hashlib.sha1(os.path.normcase(sock).encode()).hexdigest()[:10]


def _panes_file() -> Path:
    return settings.state_dir() / f"pane-sessions-{_server_key()}.json"


def load_panes() -> dict[str, dict]:
    try:
        return jsonfile.read(_panes_file(), {})
    except (OSError, ValueError):
        return {}


def save_panes(m: dict[str, dict]) -> None:
    jsonfile.write(_panes_file(), m, indent=1)


def record_panes(world: model.World, snap: dict) -> None:
    """Called by the sync hook: remember the session of every agent pane."""
    m = load_panes()
    now = time.time()
    agent_panes = {a.pane_id: a for a in world.agents if a.in_herdr}
    existing = {p["pane_id"]: p for p in snap.get("panes", [])}
    changed = False
    for pane, a in agent_panes.items():
        if not a.session_id:
            continue
        e = {"cli": a.cli, "sid": a.session_id, "name": a.display, "cwd": existing.get(pane, {}).get("cwd", ""),
             "at": now}
        old = m.get(pane, {})
        if any(old.get(k) != e[k] for k in ("cli", "sid", "name", "cwd")) or now - old.get("at", 0) > 3600:
            if old.get("sid") != e["sid"]:
                startup.claim_pending(pane, f"{a.cli}:{a.session_id}")  # options chosen at "New session"
            m[pane] = {**old, **e}
            changed = True
    # After a herdr restart every pane comes back empty, so "no agent in a live pane" means "you
    # quit it" only once this server's resume has run: before that the record is what resumes it.
    settled = _resumed_this_server() and now - _resumed_at() > 60
    for pane, e in list(m.items()):
        if pane in agent_panes:
            if e.pop("quit", None):
                changed = True
            continue
        gone_agent = pane in existing and not existing[pane].get("agent")
        resuming = now - e.get("resumed_at", 0) < 300
        if gone_agent and settled and not resuming and e.get("quit") != e.get("sid"):
            e["quit"] = e.get("sid")  # kept: herdr's leftover agent_session must not bring it back
            changed = True
        elif pane not in existing and now - e.get("at", 0) > 30 * 86400:
            del m[pane]
            changed = True
    if changed:
        save_panes(m)


def resume_panes() -> str:
    """At server start: run each recorded pane's session again in that pane, named."""
    lock = settings.state_dir() / f"resume-panes-{_server_key()}.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        if time.time() - lock.stat().st_mtime < 300:
            return "already running"
        lock.unlink(missing_ok=True)
        return resume_panes()
    try:
        from .sessions import load_sessions
        m = load_panes()
        snap = herdr.snapshot()
        by_id = {x.id: x for x in load_sessions(include_hidden=True)}
        todo_: list[tuple[str, dict]] = []
        skipped: list[str] = []
        elsewhere = live_ids(snap)  # never a second process on a session that runs somewhere
        for p in snap.get("panes", []):
            pane = p["pane_id"]
            rec = m.get(pane, {})
            ref = p.get("agent_session") or {}
            # our record first (name, options); else what herdr itself remembers for the pane
            sid = rec.get("sid") or ref.get("value") or ""
            cli = rec.get("cli") or ref.get("agent") or ""
            if not sid or not cli or p.get("agent") or rec.get("quit") == sid:
                continue
            if sid in elsewhere:
                skipped.append(f"{rec.get('name') or sid[:8]}: already running elsewhere")
                continue
            if _foreground(pane):
                continue  # something else already runs there
            s = by_id.get(sid)
            name = rec.get("name") or (s.title if s and s.named else "")
            cwd = rec.get("cwd") or p.get("cwd") or ""
            if not os.path.isdir(cwd):
                skipped.append(f"{name or sid[:8]}: folder gone")
                continue
            if not has_conversation(s):
                skipped.append(f"{name or sid[:8]}: never started a conversation, nothing to resume")
                continue
            e = {"cli": cli, "sid": sid, "name": name, "cwd": cwd, "at": rec.get("at", time.time())}
            m[pane] = {**rec, **e}
            todo_.append((pane, m[pane]))
        for x in skipped:
            log(f"pane resume skipped {x}")
        if not todo_:
            log("pane resume: no panes to resume")
            return "no panes to resume"
        gate = claude_needs_refresh() and any(e["cli"] == "claude" for _, e in todo_)
        todo_.sort(key=lambda x: x[1]["cli"] != "claude")  # with an expired token, one Claude first
        gap = float(settings.load().restore.get("gap_seconds", 1.5))
        done, held, claude_started, claude_ok = 0, 0, False, True
        for pane, e in todo_:
            if e["cli"] == "claude":
                if claude_ok and gate and claude_started:
                    claude_ok, gate = _wait_claude_refreshed(), False
                if not claude_ok:
                    held += 1
                    continue
                claude_started = True
            cmd = startup.launch_command(e["cli"], e["sid"], e.get("name", ""),
                                         startup.prefs_of(f"{e['cli']}:{e['sid']}"))
            if not cmd:
                continue
            herdr.pane_run(pane, cmd)
            m[pane]["resumed_at"] = time.time()
            save_panes(m)
            log(f"resumed {e['cli']} {e['sid']} '{e.get('name', '')[:50]}' in its pane {pane}")
            done += 1
            time.sleep(gap)
        summary = f"resumed {done} in their panes" + (f", {held} Claude held (sign-in)" if held else "")
        log(summary)
        return summary
    finally:
        lock.unlink(missing_ok=True)
        jsonfile.write(settings.state_dir() / f"panes-resumed-{_server_key()}.json",
                       {"server": server_id(), "at": time.time()})


def _resumed_mark() -> dict:
    try:
        return jsonfile.read(settings.state_dir() / f"panes-resumed-{_server_key()}.json", {}) or {}
    except (OSError, ValueError):
        return {}


def _resumed_this_server() -> bool:
    mark = _resumed_mark()
    return bool(mark.get("server")) and mark.get("server") == server_id()


def _resumed_at() -> float:
    return float(_resumed_mark().get("at", 0))


def on_server_start() -> None:
    """The plugin's startup hook: panes first, then the usual sync."""
    from . import sync
    try:
        time.sleep(2)  # let the restored shells come up
        summary = resume_panes()
        if summary.startswith("resumed"):
            notify("Sessions resumed", summary + " (named, with your launch options)")
    except Exception as e:
        log(f"resume panes failed: {e}")
    sync.main()


def _wait_panes_resumed(timeout: float = 120) -> bool:
    """Until the startup hook has resumed this server's panes (whoever started herdr)."""
    end = time.time() + timeout
    while time.time() < end:
        if _resumed_this_server():
            return True
        time.sleep(2)
    return False


def todo(sessions: list[Session] | None = None, everything: bool = False) -> tuple[list[startup.Row], list[str]]:
    """(rows to open, reasons for the ones left out)."""
    from .sessions import load_sessions
    sessions = sessions if sessions is not None else load_sessions()
    rows = startup.selected(sessions) if not everything else [
        r for g in startup.plan(sessions) for r in g.rows]
    live = running_ids()
    out, skipped = [], []
    for r in rows:
        s = r.session
        if s.id in live:
            continue
        if not os.path.isdir(s.cwd):
            skipped.append(f"{s.title[:40]}: folder gone")
            continue
        if not settings.load().launch.get(f"{s.cli}_resume"):
            skipped.append(f"{s.title[:40]}: no resume command for {s.cli}")
            continue
        if not has_conversation(s):
            skipped.append(f"{s.title[:40]}: never started a conversation, nothing to resume")
            continue
        out.append(r)
    return out, skipped


# ---- Claude sign-in gate ------------------------------------------------------------------------

def _claude_token_expiry() -> float | None:
    p = Path.home() / ".claude" / ".credentials.json"
    try:
        exp = json.loads(p.read_text(encoding="utf-8"))["claudeAiOauth"]["expiresAt"]
        return float(exp) / 1000.0
    except (OSError, ValueError, KeyError, TypeError):
        return None


def claude_needs_refresh() -> bool:
    exp = _claude_token_expiry()
    return exp is not None and exp - time.time() < 600


def _wait_claude_refreshed(timeout: float = 90) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not claude_needs_refresh():
            return True
        time.sleep(2)
    return False


# ---- opening ------------------------------------------------------------------------------------

def open_rows(rows: list[startup.Row]) -> dict:
    """Open each session in its own tab of its project's workspace. Returns a summary."""
    gap = float(settings.load().restore.get("gap_seconds", 1.5))
    world = model.build(with_sessions=False)
    opened: dict[str, str] = {}   # session id -> pane id
    failed: list[str] = []
    held: list[str] = []
    # An expired Claude token refreshed by many processes at once logs most of them out:
    # open one Claude session first, and only continue once its refresh landed.
    gate = claude_needs_refresh() and any(r.session.cli == "claude" for r in rows)
    if gate:
        first = next(r for r in rows if r.session.cli == "claude")
        rows = [first] + [r for r in rows if r is not first]
    claude_ok, claude_started = True, False
    for i, r in enumerate(rows):
        s = r.session
        if s.cli == "claude":
            if claude_ok and gate and claude_started:
                claude_ok, gate = _wait_claude_refreshed(), False
                if not claude_ok:
                    log("claude sign-in did not refresh: holding the remaining Claude sessions")
            if not claude_ok:
                held.append(s.title)
                continue
            claude_started = True
        cmd = startup.launch_command(s.cli, s.id, s.title if s.named else "", r.prefs)
        try:
            _, pane, _ = launch.open_tab(world, s.project, s.cwd, s.title, cmd)
            opened[s.id] = pane
            log(f"opened {s.cli} {s.id} '{s.title[:60]}' in {pane}")
            world = model.build(with_sessions=False)  # the workspace may be new
        except Exception as e:
            failed.append(f"{s.title[:40]}: {str(e)[:120]}")
            log(f"FAILED {s.cli} {s.id}: {e}")
        if i < len(rows) - 1:
            time.sleep(gap)
    return {"opened": opened, "failed": failed, "held": held}


def verify(opened: dict[str, str], timeout: float = 90) -> tuple[int, list[str]]:
    """Wait until herdr sees an agent in each opened pane. Returns (verified, missing panes)."""
    want = {p for p in opened.values() if p}
    seen: set[str] = set()
    end = time.time() + timeout
    while want - seen and time.time() < end:
        try:
            for p in herdr.snapshot().get("panes", []):
                if p.get("pane_id") in want and p.get("agent"):
                    seen.add(p["pane_id"])
        except Exception:
            pass
        if want - seen:
            time.sleep(3)
    return len(seen), sorted(want - seen)


def open_now(rows: list[startup.Row], wait: bool = True) -> str:
    res = open_rows(rows)
    n = len(res["opened"])
    ok, missing = verify(res["opened"]) if wait and n else (n, [])
    summary = f"opened {n} verified {ok} failed {len(res['failed'])} held {len(res['held'])}"
    log(summary + (f" (no agent seen in {', '.join(missing)})" if missing else ""))
    return summary


# ---- relaunch in place --------------------------------------------------------------------------

def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=_NO_WINDOW)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _children(pid: int) -> list[tuple[int, str]]:
    """Direct child processes (pid, name) of `pid`."""
    if not pid:
        return []
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class PE(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)
        out = []
        e = PE()
        e.dwSize = ctypes.sizeof(PE)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.th32ParentProcessID == pid and e.th32ProcessID != pid:
                out.append((int(e.th32ProcessID), e.szExeFile))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
        k32.CloseHandle(snap)
        return [c for c in out if c[1].lower() != "conhost.exe"]
    try:
        r = subprocess.run(["pgrep", "-l", "-P", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return [(int(a), b) for a, _, b in (ln.partition(" ") for ln in r.stdout.splitlines()) if a.isdigit()]


def _foreground(pane_id: str) -> list[dict]:
    """What runs in the pane besides its shell: herdr's view plus the shell's own children
    (on Windows herdr only reports recognised agents as foreground)."""
    try:
        info = herdr.run("pane", "process-info", "--pane", pane_id).get("process_info", {})
    except herdr.HerdrError:
        return []
    shell = info.get("shell_pid")
    procs = {p["pid"]: p for p in info.get("foreground_processes", []) if p.get("pid") and p.get("pid") != shell}
    for pid, name in _children(int(shell or 0)):
        procs.setdefault(pid, {"pid": pid, "name": name})
    return list(procs.values())


def relaunch_in_place(pane_id: str, cli: str, sid: str, name: str = "") -> str:
    """Stop the agent in this pane and resume the same session in the same pane."""
    cmd = startup.launch_command(cli, sid, name, startup.prefs_of(f"{cli}:{sid}"))
    if not cmd:
        return f"✗ no resume command for {cli}"
    procs = _foreground(pane_id)
    for p in procs:
        _kill(int(p["pid"]))
    end = time.time() + 10
    while _foreground(pane_id) and time.time() < end:
        time.sleep(0.4)
    if _foreground(pane_id):
        return f"✗ '{name or sid[:8]}' did not stop: not relaunched (two copies would fork it)"
    time.sleep(0.6)  # let the shell print its prompt
    herdr.pane_run(pane_id, cmd)
    log(f"relaunched {cli} {sid} in {pane_id}")
    return f"↻ {name or sid[:8]}"


def relaunch_targets(cli: str = "", world: model.World | None = None) -> dict:
    """Sessions a relaunch would touch: herdr panes split by busy/idle, plus other terminals."""
    world = world or model.build(with_sessions=False)
    out = {"restart": [], "busy": [], "elsewhere": []}
    for a in world.agents:
        if cli and a.cli != cli or not a.session_id:
            continue
        if not a.in_herdr:
            out["elsewhere"].append(a)
        elif a.status == "working":
            out["busy"].append(a)
        else:
            out["restart"].append(a)
    return out


def relaunch_all(cli: str = "", include_busy: bool = False) -> str:
    t = relaunch_targets(cli)
    done, bad = 0, []
    for a in t["restart"] + (t["busy"] if include_busy else []):
        msg = relaunch_in_place(a.pane_id, a.cli, a.session_id, a.display)
        if msg.startswith("✗"):
            bad.append(msg)
        else:
            done += 1
        time.sleep(float(settings.load().restore.get("gap_seconds", 1.5)))
    summary = f"relaunched {done}" + (f", {len(t['busy'])} busy left alone" if t["busy"] and not include_busy else "")
    if bad:
        summary += f", {len(bad)} failed"
    log(summary)
    return summary


# ---- logon --------------------------------------------------------------------------------------

def server_up() -> bool:
    try:
        out = subprocess.run([herdr.herdr_bin(), "status", "server"], capture_output=True, text=True,
                             timeout=8, creationflags=_NO_WINDOW).stdout
        return "status: running" in out
    except (OSError, subprocess.SubprocessError):
        return False


def terminal_command() -> list[str]:
    custom = str(settings.load().restore.get("terminal", "")).strip()
    hb = herdr.herdr_bin()
    if custom:
        import shlex
        return shlex.split(custom, posix=os.name != "nt")
    if os.name == "nt":
        wt = shutil.which("wt.exe") or os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe")
        if os.path.exists(wt):
            return [wt, "-w", "new", hb]
        return ["cmd.exe", "/c", "start", "herdr", hb]
    if sys.platform == "darwin":
        return ["osascript", "-e", f'tell application "Terminal" to do script "{hb}"']
    for t in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
        if shutil.which(t):
            return [t, "--", hb] if t == "gnome-terminal" else [t, "-e", hb]
    return [hb]


def boot(delay: float | None = None) -> str:
    cfg = settings.load().restore
    delay = float(cfg.get("logon_delay_seconds", 20)) if delay is None else delay
    log(f"logon restore: waiting {delay:.0f}s")
    time.sleep(delay)
    if not server_up():
        try:
            subprocess.Popen(terminal_command(), close_fds=True)
        except OSError as e:
            log(f"could not start herdr: {e}")
            return "✗ herdr did not start"
        end = time.time() + 90
        while not server_up() and time.time() < end:
            time.sleep(2)
        if not server_up():
            log("herdr server never came up")
            return "✗ herdr did not start"
    # the startup hook resumes herdr's own panes first; ticked sessions it brought back are then
    # running and are not opened twice
    if not _wait_panes_resumed():
        log("pane resume did not report back in 120s: running it from here")
        log("pane resume: " + resume_panes())
    time.sleep(5)
    rows, skipped = todo()
    for s in skipped:
        log(f"skipped {s}")
    summary = open_now(rows) if rows else "opened 0 (everything ticked was already running)"
    held = " Claude needs a sign-in: Navigator › ⚙ Settings › Accounts." if " held 0" not in summary and rows else ""
    want, missing = guarantee()
    if missing:
        summary += f" · {len(missing)} of {len(want)} NOT running: " + ", ".join(missing[:8])
    else:
        summary += f" · all {len(want)} sessions running"
    if skipped:
        summary += f" · {len(skipped)} left out (see restore.log)"
    log("logon restore: " + summary)
    notify("Startup restore", summary + held)
    try:
        from . import alerts
        if settings.load().alerts.get("on_restore", True):
            alerts.send("Sessions restored", summary + held, "warning" if missing else "white_check_mark")
    except Exception as e:
        log(f"alert failed: {e}")
    return summary


def guarantee(timeout: float = 120) -> tuple[dict[str, str], list[str]]:
    """The proof after a restore: every ticked session and every pane resumed in this boot must
    have a live agent. Waits up to `timeout`; returns (expected id -> name, names still missing)."""
    from .sessions import load_sessions
    want = {r.session.id: r.session.title for r in startup.selected(load_sessions())
            if has_conversation(r.session) and os.path.isdir(r.session.cwd)
            and settings.load().launch.get(f"{r.session.cli}_resume")}
    now = time.time()
    want |= {e["sid"]: e.get("name") or e["sid"][:8] for e in load_panes().values()
             if now - e.get("resumed_at", 0) < 900}
    end = time.time() + timeout
    while True:
        missing = sorted(want[sid] for sid in set(want) - live_ids())
        if not missing or time.time() >= end:
            break
        time.sleep(5)
    for name in missing:
        log(f"NOT RUNNING after restore: {name}")
    return want, missing


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "boot":
        print(boot())
    elif cmd == "now":
        rows, skipped = todo()
        summary = open_now(rows) if rows else "nothing to open"
        notify("Sessions opened", summary)
        print(summary, *skipped, sep="\n")
    elif cmd == "startup":
        try:  # the resident helper that keeps herdr's sidebar and tab bar current
            from . import daemon
            daemon.start()
        except Exception as e:
            print(f"navigator: daemon not started: {e}")
        on_server_start()
    elif cmd == "panes":
        print(resume_panes())
    elif cmd == "check":
        want, missing = guarantee(timeout=0)
        print(f"{len(want) - len(missing)} of {len(want)} running", *[f"missing: {m}" for m in missing], sep="\n")
    elif cmd == "dry-run":
        rows, skipped = todo()
        for r in rows:
            s = r.session
            print(f"{s.cli:7} {s.project.label[:28]:28} {s.title[:50]:50} {startup.launch_command(s.cli, s.id, s.title, r.prefs)}")
        print(*skipped, sep="\n")
    elif cmd == "relaunch":
        rest = sys.argv[2:]
        cli = next((a for a in rest if a and not a.startswith("--")), "")
        summary = relaunch_all(cli, include_busy="--busy" in rest)
        notify("Relaunch", summary)
        print(summary)
    else:
        print("usage: restore boot | now | dry-run | relaunch [cli]")


if __name__ == "__main__":
    main()
