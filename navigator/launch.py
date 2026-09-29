"""Actions that change what is on screen: go to a project, resume a session, start an agent."""
from __future__ import annotations

import os

from . import herdr, settings
from .model import World
from .projects import Project
from .sessions import Session


def projects_base(p: Project) -> Project:
    return Project(p.root, p.name, "", p.path)


def _pane_of(result: dict) -> str:
    return (result.get("root_pane") or result.get("pane") or {}).get("pane_id", "")


def _ws_of(result: dict) -> str:
    return (result.get("workspace") or {}).get("workspace_id", "")


def goto_project(world: World, project: Project, wt_path: str = "") -> str:
    """Focus the project's (or worktree's) workspace, opening it if none is open."""
    if project.worktree and wt_path:
        ws = next((w for w in world.workspaces if w.project and w.project.root == project.root
                   and w.project.worktree == project.worktree), None)
        if ws:
            herdr.focus_workspace(ws.id)
            return f"→ {project.label} ({ws.id})"
        primary = next((w for w in world.workspaces if w.project and w.project.root == project.root
                        and not w.project.worktree), None)
        args = ["worktree", "open", "--path", wt_path, "--focus"]
        if primary:
            args += ["--workspace", primary.id]
        try:
            herdr.run(*args)
        except herdr.HerdrError:
            herdr.create_workspace(wt_path, label=project.label, focus=True)
        return f"＋ opened {project.label}"
    ws = world.workspace_for(projects_base(project))
    if ws:
        herdr.focus_workspace(ws.id)
        return f"→ {project.label} ({ws.id})"
    cwd = project.path or project.root
    if not os.path.isdir(cwd):
        return f"✗ {cwd} no longer exists"
    res = herdr.create_workspace(cwd, label=project.label, focus=True)
    return f"＋ opened {project.label} ({_ws_of(res)})"


def _ensure_workspace(world: World, project: Project, cwd: str) -> tuple[str, bool]:
    ws = world.workspace_for(project)
    if ws:
        return ws.id, False
    # one workspace per project; worktree sessions become tabs inside it
    res = herdr.create_workspace(project.path or cwd, label=project.name, focus=False)
    return _ws_of(res), True


def _open_in_tab(world: World, project: Project, cwd: str, label: str, command: str) -> str:
    ws_id, created = _ensure_workspace(world, project, cwd)
    if created:
        # a fresh workspace already has an idle root pane: use it instead of adding a tab
        snap = herdr.snapshot()
        pane = next((p["pane_id"] for p in snap.get("panes", []) if p.get("workspace_id") == ws_id), "")
        tab = next((p["tab_id"] for p in snap.get("panes", []) if p.get("pane_id") == pane), "")
        if cwd.lower() != (project.path or "").lower():
            pane = ""  # the session lives in a sub-checkout: give it its own tab at that cwd
    else:
        pane = tab = ""
    if not pane:
        res = herdr.create_tab(ws_id, cwd, label=label[:24], focus=False)
        pane = _pane_of(res)
        tab = (res.get("tab") or {}).get("tab_id", "")
    herdr.pane_run(pane, command)
    herdr.focus_workspace(ws_id)
    if tab:
        herdr.focus_tab(tab)
    return f"▶ {command}  in {project.label} ({pane})"


def resume(world: World, s: Session) -> str:
    live = world.live_sessions.get(s.id)
    if live and live.in_herdr:
        herdr.focus_agent(live.pane_id)
        return f"→ already running in {live.pane_id}"
    if live:
        return (f"✗ '{live.name or s.title}' is running in another terminal (pid {live.pid}). "
                "Resuming it twice would fork the conversation: exit it there, then resume here.")
    if not os.path.isdir(s.cwd):
        return f"✗ {s.cwd} no longer exists (worktree removed?)"
    return _open_in_tab(world, s.project, s.cwd, s.title, s.resume_command())


def new_agent(world: World, project: Project, cli: str) -> str:
    cmd = settings.load().launch.get(f"{cli}_new", cli)
    cwd = project.path or project.root
    return _open_in_tab(world, project, cwd, cli, cmd)
