"""Make herdr's Spaces sidebar show exactly the projects ticked in the Navigator.

Ticked project  -> a workspace at the repo root; each worktree with a running agent opens as a
                   linked-worktree workspace, which herdr indents under its repo (one group).
Unticked project -> its workspaces close, but only those with no agent and no busy command.
                   Anything still running is kept and reported, never killed.
"""
from __future__ import annotations

import os

from . import herdr, settings
from .model import ProjectView, World, load_selection, save_selection


def _ws_busy(ws_id: str, world: World) -> bool:
    if any(a.workspace_id == ws_id for a in world.agents):
        return True
    try:
        panes = herdr.run("pane", "list", "--workspace", ws_id).get("panes", [])
    except herdr.HerdrError:
        return True
    for p in panes:
        try:
            info = herdr.run("pane", "process-info", "--pane", p["pane_id"]).get("process_info", {})
        except herdr.HerdrError:
            return True
        shell = info.get("shell_pid")
        if any(fp.get("pid") != shell for fp in info.get("foreground_processes") or []):
            return True
    return False


def open_project(world: World, v: ProjectView) -> list[str]:
    msgs = []
    primary = next((w for w in v.workspaces if not w.linked_worktree and not w.project.worktree), None)
    if primary is None:
        path = v.project.path or v.project.root
        if not os.path.isdir(path):
            return [f"✗ {path} no longer exists"]
        res = herdr.create_workspace(path, label=v.project.name, focus=False)
        primary_id = (res.get("workspace") or {}).get("workspace_id", "")
        msgs.append(f"＋ {v.project.name}")
    else:
        primary_id = primary.id
    if settings.load().open_active_worktrees:
        for wt in v.active_worktrees():
            if wt.workspace or not wt.exists:
                continue
            if not os.path.exists(os.path.join(wt.path, ".git")):
                # a leftover folder of a removed worktree: its agent still shows in the Navigator
                msgs.append(f"skipped {wt.label}: not a git worktree any more")
                continue
            try:
                herdr.run("worktree", "open", "--workspace", primary_id, "--path", wt.path, "--no-focus")
                msgs.append(f"＋ {v.project.name} ⎇ {wt.label}")
            except herdr.HerdrError as e:
                msgs.append(f"✗ {wt.label}: {str(e)[:60]}")
    return msgs


def close_project(world: World, v: ProjectView) -> list[str]:
    msgs = []
    # linked worktrees first, then the primary
    for w in sorted(v.workspaces, key=lambda w: not w.linked_worktree):
        if w.focused and len(world.workspaces) == 1:
            msgs.append(f"kept {w.label}: it is the only workspace")
            continue
        if _ws_busy(w.id, world):
            msgs.append(f"kept {w.label}: something is running there")
            continue
        herdr.run("workspace", "close", w.id, check=False)
        msgs.append(f"－ {w.label}")
    return msgs


def set_selected(world: World, root: str, on: bool) -> list[str]:
    sel = load_selection()
    if sel is None:
        sel = {v.project.root for v in world.projects if v.in_sidebar}
    (sel.add if on else sel.discard)(root)
    save_selection(sel)
    v = world.view(root)
    if not v:
        return []
    return open_project(world, v) if on else close_project(world, v)


def refresh_worktrees(world: World) -> list[str]:
    """Open newly active worktrees of ticked projects (safe to run any time)."""
    msgs = []
    for v in world.projects:
        if v.in_sidebar and v.live:
            msgs += [m for m in open_project(world, v) if "⎇" in m or m.startswith("✗")]
    return msgs


def after_change() -> None:
    """Sidebar tokens for workspaces that just appeared."""
    from . import sync
    try:
        sync.coalesced()
    except Exception:
        pass
