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
from .model import STATE_ICON, summarize


def line() -> str:
    hint = "F1 ☰ Navigator"
    try:
        snap = herdr.snapshot()
    except Exception:
        return f"{hint}  │  herdr unreachable"
    ws_id = snap.get("focused_workspace_id", "")
    cwd = next((p.get("cwd", "") for p in snap.get("panes", []) if p.get("workspace_id") == ws_id), "")
    here = projects.resolve(cwd).label if cwd else "?"
    agents = snap.get("agents", [])
    mine = Counter(a.get("agent_status") for a in agents if a.get("workspace_id") == ws_id)
    elsewhere = [a for a in agents if a.get("workspace_id") != ws_id and a.get("agent_status") in ("blocked", "done")]
    labels = {w["workspace_id"]: w.get("label", "") for w in snap.get("workspaces", [])}
    parts = [hint, f"▣ {here} {summarize(mine)}".rstrip()]
    blocked = [a for a in elsewhere if a.get("agent_status") == "blocked"]
    if blocked:
        names = sorted({labels.get(a.get("workspace_id"), "?") for a in blocked})
        parts.append(f"{STATE_ICON['blocked']} {', '.join(names)[:28]} waits · {len(elsewhere)} need you: Ctrl+Alt+I")
    elif elsewhere:
        parts.append(f"{STATE_ICON['done']}{len(elsewhere)} need you: Ctrl+Alt+I")
    in_herdr = {(a.get("agent_session") or {}).get("value") for a in agents}
    outside = [r for r in live._claude_registry(time.time()) if r.session_id not in in_herdr]
    _maybe_reconcile(outside)
    if outside:
        parts.append(f"↗{len(outside)} in other windows: F3")
    parts.append("F2 resume · F6 layout · F7 recent · Ctrl+Alt+R back")
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
