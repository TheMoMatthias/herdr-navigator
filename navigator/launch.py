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


def open_tab(world: World, project: Project, cwd: str, label: str, command: str) -> tuple[str, str, str]:
    """Run `command` in the project's workspace: in a fresh workspace's own idle pane, else in a
    new tab. Nothing is focused. Returns (workspace, pane, tab).
    A session in a linked worktree gets the worktree's own workspace, shown indented under its
    repo in the sidebar, instead of a tab in the repo's workspace."""
    if project.worktree and project.wt_path and os.path.isfile(os.path.join(project.wt_path, ".git")):
        ws_id, created = _ensure_worktree_workspace(world, project)
        if ws_id:
            return _run_in(ws_id, created, project.wt_path, cwd, label, command)
    ws_id, created = _ensure_workspace(world, project, cwd)
    return _run_in(ws_id, created, project.path or "", cwd, label, command)


def _ensure_worktree_workspace(world: World, project: Project) -> tuple[str, bool]:
    ws = next((w for w in world.workspaces if w.project and w.project.root == project.root
               and w.project.worktree == project.worktree), None)
    if ws:
        return ws.id, False
    primary = next((w for w in world.workspaces if w.project and w.project.root == project.root
                    and not w.project.worktree), None)
    primary_id = primary.id if primary else _ensure_workspace(world, projects_base(project),
                                                              project.path or project.root)[0]
    try:
        res = herdr.run("worktree", "open", "--workspace", primary_id, "--path", project.wt_path, "--no-focus")
    except herdr.HerdrError:
        return "", False
    ws_id = _ws_of(res)
    if not ws_id:  # find it by its folder
        want = os.path.normcase(project.wt_path.rstrip("\\/"))
        ws_id = next((p.get("workspace_id", "") for p in herdr.snapshot().get("panes", [])
                      if os.path.normcase(p.get("cwd", "").rstrip("\\/")) == want), "")
    return ws_id, bool(ws_id)


def _run_in(ws_id: str, created: bool, home: str, cwd: str, label: str, command: str) -> tuple[str, str, str]:
    pane = tab = ""
    if created:
        # a fresh workspace already has an idle root pane: use it instead of adding a tab
        snap = herdr.snapshot()
        pane = next((p["pane_id"] for p in snap.get("panes", []) if p.get("workspace_id") == ws_id), "")
        tab = next((p["tab_id"] for p in snap.get("panes", []) if p.get("pane_id") == pane), "")
        if os.path.normcase(cwd.rstrip("\\/")) != os.path.normcase(home.rstrip("\\/")):
            pane = ""  # the session lives in a sub-folder: give it its own tab at that cwd
    if not pane:
        res = herdr.create_tab(ws_id, cwd, label=label[:24], focus=False)
        pane = _pane_of(res)
        tab = (res.get("tab") or {}).get("tab_id", "")
    herdr.pane_run(pane, command)
    return ws_id, pane, tab


def _open_in_tab(world: World, project: Project, cwd: str, label: str, command: str, focus: bool = True) -> str:
    ws_id, pane, tab = open_tab(world, project, cwd, label, command)
    if focus:
        herdr.focus_workspace(ws_id)
        if tab:
            herdr.focus_tab(tab)
    return f"▶ {label or command}  in {project.label} ({pane})"


def resume(world: World, s: Session, focus: bool = True) -> str:
    from . import model
    try:  # the list may be minutes old: check what runs right now, so a session is never forked
        world = model.build()
    except Exception:
        pass
    live = world.live_sessions.get(s.id)
    if live and live.in_herdr:
        if focus:
            herdr.focus_agent(live.pane_id)
        return f"→ already running in {live.pane_id}"
    if live:
        return (f"✗ '{live.name or s.title}' is running in another terminal (pid {live.pid}). "
                "Resuming it twice would fork the conversation: exit it there, then resume here.")
    if not os.path.isdir(s.cwd):
        return f"✗ {s.cwd} no longer exists (worktree removed?)"
    from . import startup
    cmd = startup.launch_command(s.cli, s.id, s.title if s.named else "",
                                 startup.prefs_of(f"{s.cli}:{s.id}"))
    return _open_in_tab(world, s.project, s.cwd, s.title, cmd or s.resume_command(), focus)


def new_agent(world: World, project: Project, cli: str, focus: bool = True) -> str:
    cmd = settings.load().launch.get(f"{cli}_new", cli)
    cwd = project.path or project.root
    return _open_in_tab(world, project, cwd, cli, cmd, focus)


def new_session(world: World, project: Project, folder: str, cli: str, name: str = "",
                prefs: dict | None = None, branch: str = "", focus: bool = True) -> str:
    """Start a named session with launch options; with `branch`, in a new git worktree of the
    project (herdr creates it and groups its workspace under the repo)."""
    from . import startup
    prefs = prefs or {}
    cmd = startup.new_command(cli, name, prefs)
    if branch:
        repo = project.path or project.root
        primary = next((w for w in world.workspaces if w.project and w.project.root == project.root
                        and not w.project.worktree), None)
        args = ["worktree", "create", "--cwd", repo, "--branch", branch, "--label", (name or branch)[:28],
                "--focus" if focus else "--no-focus"]
        if primary:
            args += ["--workspace", primary.id]
        res = herdr.run(*args, timeout=60)
        ws_id, pane = _ws_of(res), _pane_of(res)
        if not pane:
            panes = herdr.snapshot().get("panes", [])
            tail = os.sep + branch.replace("/", os.sep)
            hit = next((p for p in panes if (ws_id and p.get("workspace_id") == ws_id)
                        or os.path.normcase(p.get("cwd", "").rstrip("\\/")).endswith(os.path.normcase(tail))), None)
            pane, ws_id = (hit or {}).get("pane_id", ""), (hit or {}).get("workspace_id", ws_id)
        if not pane:
            return f"✗ worktree '{branch}' created, but its pane was not found: start {cli} there yourself"
        herdr.pane_run(pane, cmd)
        startup.remember_for_pane(pane, prefs)
        if ws_id and focus:
            herdr.focus_workspace(ws_id)
        return f"⎇ {branch}: ▶ {cmd}"
    if not os.path.isdir(folder):
        return f"✗ {folder} does not exist"
    ws_id, pane, tab = open_tab(world, project, folder, name or cli, cmd)
    startup.remember_for_pane(pane, prefs)
    if focus:
        herdr.focus_workspace(ws_id)
        if tab:
            herdr.focus_tab(tab)
    return f"▶ {name or cmd}  in {project.label} ({pane})"
