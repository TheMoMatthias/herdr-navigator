"""Close sessions that have sat idle for a long time (off until [tidy] enabled = true).

A session is a candidate when herdr shows it idle (or inactive: idle for an hour), with no
sub-agents, background jobs or workflows, and it is not in focus. It is recommended for closing when
  * its last transcript write is older than `idle_hours`, and
  * it is not one of the `keep_newest` most recently used sessions of its project.
The automatic sweep also never starts the idle clock before the herdr server did, so the morning
restart, where every session is "idle for 10 hours", closes nothing at once.
Closing never loses the conversation: it stays in the Navigator to resume (a session that is
messaged again later simply comes back with ▶ Resume). The session is unticked for the logon
restore, which would otherwise open it again.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import herdr, model, restore, settings, startup
from .sessions import load_sessions

MAX_PER_SWEEP = 10  # a long-parked backlog goes over several sweeps, never as one burst
PARKED = ("idle", "inactive")


@dataclass
class Item:
    agent: model.Agent
    idle: float     # seconds since the session last wrote anything
    close: bool     # recommended to close
    reason: str     # why, in a few words


def config() -> tuple[bool, float, int]:
    """(on, idle seconds, newest sessions per project that always stay)."""
    cfg = settings.load().tidy
    return (bool(cfg.get("enabled")), max(1.0, float(cfg.get("idle_hours", 10) or 10)) * 3600,
            max(0, int(cfg.get("keep_newest", 3) or 0)))


def server_started_at() -> float:
    """When this herdr server started: its socket file is rewritten at every start (0 = unknown)."""
    try:
        return Path(os.environ.get("HERDR_SOCKET_PATH") or herdr._default_socket()).stat().st_mtime
    except OSError:
        return 0.0


def ago(seconds: float) -> str:
    h = seconds / 3600
    return f"{h:.0f} h" if h < 48 else f"{h / 24:.0f} days"


def review(world: model.World, now: float, floor: float = 0.0) -> list[Item]:
    """Every parked session with a recommendation. `floor` is the earliest the idle clock may start."""
    _, limit, keep = config()
    mtime = {s.id: s.mtime for s in load_sessions(include_hidden=True)}
    rank: dict[str, int] = {}  # 0 = the project's most recently used session
    by_project: dict[str, list[model.Agent]] = {}
    for a in world.agents:
        if a.in_herdr and a.session_id in mtime:
            by_project.setdefault(a.project.root, []).append(a)
    for group in by_project.values():
        for i, a in enumerate(sorted(group, key=lambda a: -mtime[a.session_id])):
            rank[a.session_id] = i
    out = []
    for a in world.agents:
        if not (a.in_herdr and a.session_id in mtime and a.status in PARKED) or a.focused:
            continue
        if a.subagents or a.jobs or a.workflows:
            continue
        idle = now - max(mtime[a.session_id], floor)
        if rank[a.session_id] < keep:
            out.append(Item(a, idle, False, f"one of the newest {keep} in {a.project.name}"))
        elif idle < limit:
            out.append(Item(a, idle, False, f"idle only {ago(idle)}"))
        else:
            out.append(Item(a, idle, True, f"idle {ago(idle)}"))
    return sorted(out, key=lambda i: (i.agent.project.name, -i.idle))


def close(agents: list[model.Agent], snap: dict | None = None) -> list[str]:
    """Close these sessions' panes (the tab too when it was the only pane); returns the names closed."""
    snap = snap or herdr.snapshot()
    per_tab: dict[str, int] = {}
    for p in snap.get("panes", []):
        per_tab[p.get("tab_id", "")] = per_tab.get(p.get("tab_id", ""), 0) + 1
    closed = []
    for a in agents:
        startup.set_tick(f"{a.cli}:{a.session_id}", False)
        try:  # the last pane of its tab takes the tab with it; a tab with other panes keeps them
            if per_tab.get(a.tab_id, 0) <= 1 and a.tab_id:
                herdr.run("tab", "close", a.tab_id)
            else:
                herdr.run("pane", "close", a.pane_id)
        except herdr.HerdrError as e:
            restore.log(f"tidy: could not close {a.display}: {e}")
            continue
        restore.log(f"tidy: closed {a.cli} {a.session_id} '{a.display[:50]}' (resume it from the Navigator)")
        closed.append(a.display)
    return closed


def sweep(world: model.World, snap: dict | None = None) -> list[str]:
    """The automatic pass: close what is due."""
    enabled = config()[0]
    started = server_started_at()
    if not enabled or not started:
        return []
    due = [i.agent for i in review(world, time.time(), started) if i.close][:MAX_PER_SWEEP]
    closed = close(due, snap) if due else []
    if closed:
        restore.notify("Idle sessions closed", f"{len(closed)}: " + "; ".join(closed)[:180] + ". Resume from the Navigator.")
    return closed
