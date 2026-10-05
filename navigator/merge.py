"""One Space per folder: a second plain Space opened on a folder that already has one joins it.

herdr's sidebar "new" always makes a new Space, also in a folder that is open already (the repo's
own checkout, say). herdr nests only linked worktrees under their repo, so that second Space sat
apart as another "AlgoTrader". Each sync moves the duplicate's panes into the folder's Space as
tabs (tab labels and splits kept, processes keep running) and closes the emptied duplicate.
Linked-worktree Spaces are never touched. Off: `[workspaces] merge_duplicates = false`.
"""
from __future__ import annotations

import os

from . import herdr


def _key(cwd: str) -> str:
    return os.path.normcase(os.path.normpath(cwd.removeprefix("\\\\?\\"))) if cwd else ""


def duplicates(world) -> list[tuple]:
    """(duplicate, keeper) Space pairs: plain Spaces on the same folder. The keeper is the one
    herdr knows as a git checkout, else the oldest (lowest number)."""
    groups: dict[str, list] = {}
    for w in world.workspaces:
        if w.cwd and not w.linked_worktree:
            groups.setdefault(_key(w.cwd), []).append(w)
    out = []
    for spaces in groups.values():
        if len(spaces) < 2:
            continue
        keeper = min(spaces, key=lambda w: (not getattr(w, "git", False), w.number))
        out += [(w, keeper) for w in spaces if w is not keeper]
    return out


def _move(pane_id: str, destination: dict, focus: bool) -> dict:
    return herdr.request("pane.move", {"pane_id": pane_id, "destination": destination, "focus": focus},
                         timeout=6) or {}


def merge(world) -> list[str]:
    """Move every duplicate Space into its keeper. Returns one line per merged Space."""
    snap = world.snapshot or herdr.snapshot()
    panes_by_tab: dict[str, list[str]] = {}
    for p in snap.get("panes", []):
        panes_by_tab.setdefault(p.get("tab_id", ""), []).append(p["pane_id"])
    done = []
    for dup, keeper in duplicates(world):
        moved = 0
        for t in dup.tabs:
            panes = panes_by_tab.get(t["tab_id"], [])
            if not panes:
                continue
            try:
                _move(panes[0], {"type": "new_tab", "workspace_id": keeper.id, "label": t.get("label") or None},
                      dup.focused)
                tab = (herdr.request("pane.get", {"pane_id": panes[0]}, timeout=6) or {})
                tab_id = (tab.get("pane") or tab).get("tab_id", "")
                for p in panes[1:]:  # the tab's other panes join it side by side
                    _move(p, {"type": "tab", "tab_id": tab_id, "split": "right"}, False)
                moved += len(panes)
            except (herdr.HerdrError, OSError, ValueError):
                break
        left = [p for p in herdr.snapshot().get("panes", []) if p.get("workspace_id") == dup.id]
        if moved and not left:
            try:
                herdr.request("workspace.close", {"workspace_id": dup.id}, timeout=6)
            except (herdr.HerdrError, OSError, ValueError):
                pass
        if moved:
            done.append(f"moved {moved} pane(s) of Space {dup.label} ({dup.id}) into {keeper.label} ({keeper.id})")
    return done
