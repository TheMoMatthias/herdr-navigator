"""One status line for herdr's tab bar (`[ui] tab_bar_right` command entry).

Leads with the key that opens the Navigator, then where you are, what needs you, and the key
that gets you there. Stdlib only and no transcript reads: it runs every few seconds.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter

from . import herdr, live, projects
from .model import STATE_ICON


STATE_WORD = {"blocked": "waiting", "done": "done", "working": "working", "idle": "idle"}


def _plain(counts: Counter) -> str:
    """'1 working, 2 idle' — words, not glyphs, so the bar reads without a legend."""
    return ", ".join(f"{counts[s]} {w}" for s, w in STATE_WORD.items() if counts.get(s)) or "no agents"


def line() -> str:
    """Most urgent first, then where you are, then the keys; every item says what it is and
    which key acts on it."""
    menu = "F1 Menu · F2 Sessions · F6 Layout · F7 Recent · Ctrl+Alt+R Back"
    try:
        snap = herdr.snapshot()
    except Exception:
        return f"herdr unreachable  │  {menu}"
    try:  # phone alert for an agent that has waited on you too long (off unless configured)
        from . import alerts
        alerts.check_waiting(snap)
    except Exception:
        pass
    ws_id = snap.get("focused_workspace_id", "")
    cwd = next((p.get("cwd", "") for p in snap.get("panes", []) if p.get("workspace_id") == ws_id), "")
    here = projects.resolve(cwd).label if cwd else "?"
    agents = snap.get("agents", [])
    mine = Counter(a.get("agent_status") for a in agents if a.get("workspace_id") == ws_id)
    elsewhere = [a for a in agents if a.get("workspace_id") != ws_id and a.get("agent_status") in ("blocked", "done")]
    labels = {w["workspace_id"]: w.get("label", "") for w in snap.get("workspaces", [])}
    parts = []
    blocked = [a for a in elsewhere if a.get("agent_status") == "blocked"]
    if blocked:
        names = sorted({labels.get(a.get("workspace_id"), "?") for a in blocked})
        parts.append(f"{STATE_ICON['blocked']} {', '.join(names)[:28]} waiting for you (Ctrl+Alt+I)")
    elif elsewhere:
        n = len(elsewhere)
        parts.append(f"{STATE_ICON['done']} {n} agent{'s' * (n != 1)} finished elsewhere (Ctrl+Alt+I)")
    parts.append(f"{here}: {_plain(mine)}")
    in_herdr = {(a.get("agent_session") or {}).get("value") for a in agents}
    outside = [r for r in live._claude_registry(time.time()) if r.session_id not in in_herdr]
    _maybe_reconcile(outside)
    if outside:
        n = len(outside)
        parts.append(f"{n} session{'s' * (n != 1)} outside herdr (F3)")
    parts.append(menu)
    return "  │  ".join(parts)


def _maybe_reconcile(outside) -> None:
    """Sessions started or ended in other windows: refresh their mirror panes, in the background."""
    from . import mirrors, settings
    try:
        sel = (settings.state_dir() / "sidebar.json").stat().st_mtime
    except OSError:
        sel = 0
    sig = json.dumps([sorted(r.session_id for r in outside), sel, sorted(mirrors.load())])
    f = settings.state_dir() / "status-sig.json"
    try:
        if f.read_text(encoding="utf-8") == sig:
            return
    except OSError:
        pass
    f.write_text(sig, encoding="utf-8")
    if mirrors.enabled():
        mirrors.spawn_background()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(line())


if __name__ == "__main__":
    main()
