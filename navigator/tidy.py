"""Close sessions that have sat idle for a long time (off until [tidy] enabled = true).

A session is closed when all of these hold:
  * herdr says idle, with no sub-agents, background jobs or workflows running, not focused;
  * its last transcript write is older than `idle_hours`; and
  * so is the herdr server itself: the clock starts no earlier than the server start, so the
    morning restart, where every session is "idle for 10 hours", closes nothing at once.
Closing never loses the conversation: the session stays in the Navigator to resume. It is also
unticked for the logon restore, which would otherwise open it again.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from . import herdr, model, restore, settings, startup
from .sessions import load_sessions

MAX_PER_SWEEP = 10  # a long-parked backlog goes over several sweeps, never as one burst


def config() -> tuple[bool, float]:
    cfg = settings.load().tidy
    return bool(cfg.get("enabled")), max(1.0, float(cfg.get("idle_hours", 10) or 10)) * 3600


def server_started_at() -> float:
    """When this herdr server started: its socket file is rewritten at every start (0 = unknown)."""
    try:
        return Path(os.environ.get("HERDR_SOCKET_PATH") or herdr._default_socket()).stat().st_mtime
    except OSError:
        return 0.0


def candidates(world: model.World, snap: dict, now: float) -> list[tuple[model.Agent, float]]:
    """(agent, seconds idle) for every session due to close, longest idle first."""
    enabled, limit = config()
    started = server_started_at()
    if not enabled or not started:
        return []
    mtime = {s.id: s.mtime for s in load_sessions(include_hidden=True)}
    out = []
    for a in world.agents:
        if not (a.in_herdr and a.session_id and a.status == "idle") or a.focused:
            continue
        if a.subagents or a.jobs or a.workflows or a.session_id not in mtime:
            continue
        idle = now - max(mtime[a.session_id], started)
        if idle >= limit:
            out.append((a, idle))
    return sorted(out, key=lambda x: -x[1])[:MAX_PER_SWEEP]


def sweep(world: model.World, snap: dict | None = None) -> list[str]:
    """Close what is due; returns the names closed."""
    snap = snap or herdr.snapshot()
    now = time.time()
    per_tab: dict[str, int] = {}
    for p in snap.get("panes", []):
        per_tab[p.get("tab_id", "")] = per_tab.get(p.get("tab_id", ""), 0) + 1
    closed = []
    for a, idle in candidates(world, snap, now):
        startup.set_tick(f"{a.cli}:{a.session_id}", False)
        try:  # the last pane of its tab takes the tab with it; a tab with other panes keeps them
            if per_tab.get(a.tab_id, 0) <= 1 and a.tab_id:
                herdr.run("tab", "close", a.tab_id)
            else:
                herdr.run("pane", "close", a.pane_id)
        except herdr.HerdrError as e:
            restore.log(f"tidy: could not close {a.display}: {e}")
            continue
        restore.log(f"tidy: closed {a.cli} {a.session_id} '{a.display[:50]}' idle {idle / 3600:.1f} h (resume it from the Navigator)")
        closed.append(a.display)
    if closed:
        restore.notify("Idle sessions closed", f"{len(closed)}: " + "; ".join(closed)[:180] + ". Resume from the Navigator.")
    return closed
