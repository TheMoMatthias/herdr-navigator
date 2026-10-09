"""One resident helper per herdr server, so the sidebar follows herdr in milliseconds without
starting a Python process for every event.

* It subscribes to herdr's event stream (agent states per agent pane, panes, tabs, workspaces,
  worktrees) and syncs the sidebar in-process, debounced: a burst of events is one sync.
* It writes the tab-bar status line to `status.txt`, which the tab bar just prints (`type`),
  so the bar no longer starts Python every few seconds.
* `daemon.json` is its heartbeat. Event hooks, the status bar and the Navigator check it; when
  it is stale they start a new daemon and fall back to doing the work themselves.

Robustness: one instance (an OS file lock that dies with the process), every loop step guarded,
re-subscribe on any error or `events_lost` (then a full resync), exit when herdr has been gone
for a while or when the plugin's code changed (the next hook starts the new code)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import herdr, jsonfile, settings

ROOT = Path(__file__).resolve().parent.parent
BEAT_EVERY = 2.0      # seconds between heartbeats
STALE = 10.0          # a heartbeat older than this means the daemon is gone
DEBOUNCE = 0.3        # quiet time after the last event before syncing
MIN_GAP = 1.0         # at most one sync per second
STATUS_EVERY = 15.0   # status line refresh without events (outside sessions, phone alerts)
STATUS_GAP = 2.0      # at most one event-driven status refresh per 2 s (busy agents retitle constantly)
HEAL_EVERY = 300.0    # a sync even without events (events cover changes; this heals the rest)
WORK_EVERY = 10.0     # did a session's sub-agents or background jobs change? (a sync when so)
PULSE_EVERY = 0.2     # one frame of the pulsing "working" dot (sync.pulse_tick): ~2.4 s a breath
VIEW_EVERY = 20.0     # is herdr's Agents view still ours? (~5 ms; re-applied when dropped)
CODE_EVERY = 10.0     # has the plugin been updated? (exit, so the new code takes over)
GIVE_UP = 300.0       # herdr unreachable this long: exit (its startup hook starts a new one)
GLOBAL = ["pane.created", "pane.closed", "pane.exited", "pane.agent_detected", "pane.updated",
          "tab.created", "tab.closed", "tab.renamed", "workspace.created", "workspace.closed",
          "workspace.renamed", "workspace.focused", "worktree.created", "worktree.opened",
          "worktree.removed", "pane.focused"]
RESUBSCRIBE_ON = {"pane.created", "pane.closed", "pane.exited", "pane.agent_detected"}
# title changes (pane.updated) only refresh the status line: it notices a /rename and asks for a
# sync itself (status._maybe_resync_names), so a busy agent's titles do not sync the sidebar
STATUS_ONLY = {"workspace.focused", "tab.renamed", "pane.focused", "pane.updated"}


def _state(name: str) -> Path:
    return settings.state_dir() / name


def alive(state_dir: Path | None = None) -> bool:
    """Is a daemon running for this plugin (fresh heartbeat)?"""
    f = (state_dir or settings.state_dir()) / "daemon.json"
    try:
        return time.time() - json.loads(f.read_text(encoding="utf-8")).get("at", 0) < STALE
    except (OSError, ValueError):
        return False


def poke() -> bool:
    """Ask a running daemon to sync now. False when none runs."""
    if not alive():
        return False
    try:
        _state("daemon.poke").write_text(str(time.time()), encoding="utf-8")
        return True
    except OSError:
        return False


def start() -> bool:
    """Start a daemon in the background unless one runs. True when one was started."""
    if alive():
        return False
    py = ROOT / ".venv" / ("Scripts/pythonw.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        py = Path(sys.executable)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1",
           "HERDR_PLUGIN_STATE_DIR": str(settings.state_dir()),
           "HERDR_PLUGIN_CONFIG_DIR": str(settings.config_dir())}
    env.pop("HERDR_PLUGIN_EVENT", None)
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        subprocess.Popen([str(py), "-m", "navigator.daemon"], cwd=str(ROOT), env=env, creationflags=flags,
                         close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=os.name != "nt")
        return True
    except OSError:
        return False


def _lock():
    """Hold an exclusive lock on daemon.lock for the life of the process, or None if taken."""
    f = open(_state("daemon.lock"), "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def _code_stamp() -> float:
    return max((p.stat().st_mtime for p in (ROOT / "navigator").glob("*.py")), default=0.0)


class Daemon:
    def __init__(self) -> None:
        self.events = 0
        self.last_event = 0.0
        self.want_sync = True
        self.want_force = True        # first run (and after a reconnect): resend everything
        self.want_status = True
        self.resubscribe = threading.Event()
        self.herdr_ok_at = time.time()
        self.pending_since = 0.0   # first event not yet synced, for the latency in daemon.json
        self.latency = 0.0
        self.stop = False
        self.restart = False  # exiting for new code: start the successor once the lock is free
        self.log_file = _state("daemon.log")

    def log(self, msg: str) -> None:
        try:
            if self.log_file.exists() and self.log_file.stat().st_size > 200_000:
                self.log_file.replace(self.log_file.with_suffix(".log.1"))
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except OSError:
            pass

    # ---- the event stream -------------------------------------------------------------------
    def _subscriptions(self) -> list[dict]:
        agents = herdr.snapshot().get("agents", [])
        return ([{"type": t} for t in GLOBAL]
                + [{"type": "pane.agent_status_changed", "pane_id": a["pane_id"]} for a in agents])

    def _open(self):
        path = os.environ.get("HERDR_SOCKET_PATH") or herdr._default_socket()
        if os.name == "nt":
            return open(r"\\.\pipe" + "\\" + path, "r+b", buffering=0)
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(path)
        return s.makefile("rwb", buffering=0)

    def listen(self) -> None:
        backoff = 0.5
        while not self.stop:
            conn = None
            try:
                subs = self._subscriptions()
                conn = self._open()
                conn.write((json.dumps({"id": "nav:daemon", "method": "events.subscribe",
                                        "params": {"subscriptions": subs}}) + "\n").encode())
                self.resubscribe.clear()
                buf = b""
                started = False
                while not self.stop and not self.resubscribe.is_set():
                    chunk = conn.read(65536)
                    if not chunk:
                        raise ConnectionError("herdr closed the event stream")
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        msg = json.loads(line.decode("utf-8", "replace") or "{}")
                        if "error" in msg:
                            raise ConnectionError(json.dumps(msg["error"])[:200])
                        if not started:
                            started = True
                            backoff = 0.5
                            self.herdr_ok_at = time.time()
                            continue
                        self.on_event(msg.get("event") or (msg.get("data") or {}).get("type", ""), msg)
            except Exception as e:  # reconnect: herdr restarted, a pane vanished, events_lost
                if not self.stop:
                    self.log(f"event stream: {e}")
                    self.want_sync = self.want_force = self.want_status = True
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 5.0)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except OSError:
                        pass

    def on_event(self, kind: str, msg: dict | None = None) -> None:
        kind = kind.replace("_", ".", 1) if "." not in kind else kind  # pane_closed -> pane.closed
        if kind == "pane.focused":  # "where you were last" (F7), instead of a hook process per focus
            try:
                from . import history
                pane = history._find(msg or {}, "pane_id")
                if pane:
                    history.record(pane)
            except Exception as e:
                self.log(f"history: {e!r}")
        self.events += 1
        self.last_event = time.time()
        self.herdr_ok_at = self.last_event
        self.want_status = True
        if kind not in STATUS_ONLY:
            self.want_sync = True
            self.pending_since = self.pending_since or self.last_event
        if kind in RESUBSCRIBE_ON:
            from . import model
            model.forget_sessions()  # a new agent: its session must show up at once
            self.resubscribe.set()  # the pane list changed: subscribe to the new agent panes
            # (the stream thread reconnects; a blocked read ends at the next event)

    # ---- the work loop ------------------------------------------------------------------------
    def run(self) -> None:
        from . import live, model, status, sync
        model.SESSIONS_TTL = 10.0
        code = _code_stamp()
        last_sync = last_status = last_beat = last_heal = last_view = last_code = last_work = last_pulse = 0.0
        work_sig = None
        poke_seen = 0.0
        status_text = None
        threading.Thread(target=self.listen, daemon=True, name="nav-events").start()
        self.log(f"started (pid {os.getpid()})")
        while not self.stop:
            now = time.time()
            try:
                if now - last_beat >= BEAT_EVERY:
                    last_beat = now
                    jsonfile.write(_state("daemon.json"), {"pid": os.getpid(), "at": now, "events": self.events,
                                                           "since": self.herdr_ok_at,
                                                           "latency": round(self.latency, 2)})
                if now - last_code >= CODE_EVERY:
                    last_code = now
                    if _code_stamp() != code:
                        self.log("plugin code changed: exiting so the new code takes over")
                        self.restart = True
                        break
                if now - last_view >= VIEW_EVERY:
                    last_view = now
                    if sync.ensure_view():
                        self.log("herdr had dropped the Agents view: re-applied")
                try:
                    p = _state("daemon.poke").stat().st_mtime
                    if p > poke_seen:
                        poke_seen = p
                        self.want_sync = True
                except OSError:
                    pass
                if now - last_work >= WORK_EVERY:  # no herdr event when a background job ends
                    last_work = now
                    sig = live.work_counts(now)
                    if work_sig is not None and sig != work_sig:
                        self.want_sync = True
                    work_sig = sig
                if now - last_pulse >= PULSE_EVERY:
                    last_pulse = now
                    sync.pulse_tick()
                if now - last_heal >= HEAL_EVERY:
                    last_heal = now
                    self.want_sync = True
                quiet = now - self.last_event >= DEBOUNCE
                if self.want_sync and quiet and now - last_sync >= MIN_GAP:
                    force = self.want_force
                    t0 = time.perf_counter()
                    ran = sync.coalesced(force=force)
                    if ran:
                        self.want_sync = self.want_force = False
                        if self.pending_since:
                            self.latency, self.pending_since = time.time() - self.pending_since, 0.0
                        if force:
                            self.log(f"full sync {time.perf_counter() - t0:.2f}s")
                    # else a hook's sync held the lock: try again (forced, if it was) in a second
                    last_sync = time.time()
                    self.herdr_ok_at = last_sync
                    self.want_status = True
                if (self.want_status and quiet and now - last_status >= STATUS_GAP)                         or now - last_status >= STATUS_EVERY:
                    self.want_status = False
                    last_status = now
                    text = status.line()
                    if text != status_text:
                        status_text = text
                        jsonfile.write_text(_state("status.txt"), text + "\n")
                    if not text.startswith("herdr unreachable"):
                        self.herdr_ok_at = now
                if now - self.herdr_ok_at > GIVE_UP:
                    self.log("herdr gone for 5 minutes: exiting")
                    break
            except Exception as e:  # one bad step never ends the daemon
                self.log(f"step failed: {e!r}")
                time.sleep(1.0)
            time.sleep(0.1 if self.want_sync or self.want_status or sync.pulsing() else 0.25)
        self.stop = True


def main() -> None:
    held = _lock()
    if held is None:
        return  # another daemon runs
    d = Daemon()
    try:
        d.run()
    finally:
        for name in ("status.txt", "daemon.json"):  # the tab bar falls back to the old way
            try:
                _state(name).unlink()
            except OSError:
                pass
        d.log("stopped")
        held.close()
        # the tab bar's fallback would start one too, but a busy machine times it out (4 s) first
        if d.restart and start():
            d.log("started the new code")


if __name__ == "__main__":
    main()
