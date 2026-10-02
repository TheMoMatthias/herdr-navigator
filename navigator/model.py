"""Join the live herdr session, the running-agent registry and the session index into a
per-project picture: projects -> worktrees -> agents -> sub-agents."""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field

from . import asks, herdr, insight, live, projects, settings
from .sessions import Session, is_listed, load_sessions

# "reply" is the Navigator's own: idle in herdr, but its last message asks you something.
STATE_ORDER = {"blocked": 0, "reply": 1, "done": 2, "working": 3, "idle": 4, "unknown": 5}
STATE_ICON = {"blocked": "⚠", "reply": "⏳", "done": "✔", "working": "◐", "idle": "○", "unknown": "?"}
NEEDS_YOU = ("blocked", "reply", "done")
# Claude's registry: busy (thinking), shell (running a command), idle; Codex (inferred): active
EXTERNAL_STATUS = {"busy": "working", "shell": "working", "active": "working", "idle": "idle",
                   "waiting": "blocked", "permission": "blocked", "blocked": "blocked"}


@dataclass
class Agent:
    """A running top-level agent, inside herdr (pane_id set) or in another terminal."""
    cli: str
    status: str                  # herdr vocabulary: blocked/done/working/idle/unknown
    project: projects.Project
    name: str = ""               # session name / title
    title: str = ""              # terminal title (herdr) or session title
    session_id: str = ""
    pane_id: str = ""
    workspace_id: str = ""
    tab_id: str = ""
    focused: bool = False
    pid: int = 0
    activity: str = ""
    subagents: list[live.SubAgent] = field(default_factory=list)
    mirror_pane: str = ""        # herdr pane mirroring this outside session, if any
    question: "asks.Question | None" = None  # a question it is waiting for you to answer
    transcript: str = ""         # the session's transcript file, if known
    context: "insight.Context | None" = None  # how full its context window is
    waiting_since: float = 0.0   # when it last spoke, for agents that wait on you

    @property
    def in_herdr(self) -> bool:
        return bool(self.pane_id)

    @property
    def display(self) -> str:
        """The session's name as its CLI shows it, for any provider."""
        return self.name or self.title or self.cli

    @property
    def key(self) -> str:
        return self.pane_id or f"{self.cli}:{self.session_id or self.pid}"


@dataclass
class LiveWorkspace:
    id: str
    label: str
    number: int
    focused: bool
    status: str
    cwd: str
    project: projects.Project | None
    linked_worktree: bool = False
    tabs: list[dict] = field(default_factory=list)


@dataclass
class Worktree:
    label: str
    path: str
    agents: list[Agent] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    workspace: LiveWorkspace | None = None

    @property
    def exists(self) -> bool:
        return bool(self.path) and os.path.isdir(self.path)


@dataclass
class ProjectView:
    project: projects.Project
    workspaces: list[LiveWorkspace] = field(default_factory=list)
    agents: list[Agent] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    worktrees: dict[str, Worktree] = field(default_factory=dict)
    pinned: bool = False
    in_sidebar: bool = False

    @property
    def last_activity(self) -> float:
        t = [s.mtime for s in self.sessions]
        return max(t) if t else 0.0

    @property
    def live(self) -> bool:
        return bool(self.workspaces)

    def counts(self) -> Counter:
        return Counter(a.status for a in self.agents)

    def attention(self) -> int:
        c = self.counts()
        return (c["blocked"] + c["reply"]) * 1000 + c["done"] * 100 + c["working"] * 10 + c["idle"]

    def active_worktrees(self) -> list[Worktree]:
        return [w for w in self.worktrees.values() if w.agents]


@dataclass
class World:
    projects: list[ProjectView]
    agents: list[Agent]
    workspaces: list[LiveWorkspace]
    sessions: list[Session]
    live_sessions: dict[str, Agent]
    focused_workspace: str
    tab_labels: dict[str, str]
    error: str = ""

    def project_of_workspace(self, ws_id: str) -> projects.Project | None:
        for w in self.workspaces:
            if w.id == ws_id:
                return w.project
        return None

    def workspace_for(self, project: projects.Project) -> LiveWorkspace | None:
        cands = [w for w in self.workspaces if w.project and w.project.root == project.root]
        if not cands:
            return None
        exact = [w for w in cands if w.project.worktree == project.worktree]
        return (exact or cands)[0]

    def view(self, root: str) -> ProjectView | None:
        return next((v for v in self.projects if v.project.root == root), None)


def session_name(r: "live.Running | None", s: "Session | None", terminal_title: str) -> str:
    """User-given name > provider title (Codex thread name, Claude session title) > terminal title."""
    if r and r.user_named and r.name:
        return r.name
    if s and s.named:
        return s.title
    if r and r.name and r.cli != "claude":
        return r.name
    return terminal_title or (s.title if s else "") or (r.name if r else "")


PLUGIN_ROOT = projects.key(str(__import__("pathlib").Path(__file__).resolve().parent.parent))


def is_plugin_pane(cwd: str) -> bool:
    """Plugin panes (mirrors, popups) run from the plugin folder; they are never user agents."""
    return bool(cwd) and projects.key(cwd) == PLUGIN_ROOT


def mirror_panes() -> dict[str, str]:
    """pane_id -> session_id of the mirror panes the Navigator opened."""
    try:
        return json.loads((settings.state_dir() / "mirrors.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def summarize(counts: Counter) -> str:
    parts = [f"{STATE_ICON[s]}{counts[s]}" for s in ("blocked", "reply", "done", "working", "idle") if counts.get(s)]
    return " ".join(parts)


# --- sidebar selection -------------------------------------------------------------------

def _selection_file():
    return settings.state_dir() / "sidebar.json"


def load_selection() -> set[str] | None:
    """Project roots ticked for the sidebar; None until the user ticks anything."""
    try:
        return set(json.loads(_selection_file().read_text(encoding="utf-8"))["roots"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_selection(roots: set[str]) -> None:
    _selection_file().write_text(json.dumps({"roots": sorted(roots)}), encoding="utf-8")


# --- live state ----------------------------------------------------------------------------

def live_state() -> tuple[list[LiveWorkspace], list[dict], dict[str, str], str]:
    snap = herdr.snapshot()
    panes = snap.get("panes", [])
    first_cwd: dict[str, str] = {}
    for p in panes:
        first_cwd.setdefault(p.get("workspace_id", ""), p.get("cwd", ""))
    tabs_by_ws: dict[str, list[dict]] = {}
    tab_labels = {}
    for t in snap.get("tabs", []):
        tabs_by_ws.setdefault(t["workspace_id"], []).append(t)
        tab_labels[t["tab_id"]] = t.get("label", "")
    workspaces = []
    for w in snap.get("workspaces", []):
        wt = w.get("worktree") or {}
        cwd = wt.get("checkout_path") or first_cwd.get(w["workspace_id"], "")
        cwd = cwd.removeprefix("\\\\?\\")
        workspaces.append(LiveWorkspace(
            id=w["workspace_id"], label=w.get("label", ""), number=w.get("number", 0),
            focused=bool(w.get("focused")), status=w.get("agent_status", ""), cwd=cwd,
            project=projects.resolve(cwd) if cwd else None,
            linked_worktree=bool(wt.get("is_linked_worktree")),
            tabs=tabs_by_ws.get(w["workspace_id"], []),
        ))
    return workspaces, snap.get("agents", []), tab_labels, snap.get("focused_workspace_id", "")


def build(with_sessions: bool = True) -> World:
    err = ""
    try:
        workspaces, herdr_agents, tab_labels, focused_ws = live_state()
    except (herdr.HerdrError, OSError, ValueError) as e:  # server unreachable: still show history
        workspaces, herdr_agents, tab_labels, focused_ws, err = [], [], {}, "", str(e)
    all_sessions = load_sessions(include_hidden=True) if with_sessions else []
    sessions = [s for s in all_sessions if is_listed(s)]
    running = live.running(all_sessions) if with_sessions else []
    run_by_id = {r.session_id: r for r in running if r.session_id}

    ws_project = {w.id: w.project for w in workspaces}
    agents: list[Agent] = []
    seen_ids = set()
    by_session = {s.id: s for s in all_sessions}
    mirrors = mirror_panes()
    for a in herdr_agents:
        if a["pane_id"] in mirrors or is_plugin_pane(a.get("cwd", "")):
            continue  # a mirror pane stands in for an outside session: listed once, below
        sid = (a.get("agent_session") or {}).get("value", "")
        cwd = a.get("cwd", "")
        r = run_by_id.get(sid)
        seen_ids.add(sid)
        term_title = a.get("terminal_title_stripped") or a.get("terminal_title") or ""
        agents.append(Agent(
            cli=a.get("agent", "?"), status=a.get("agent_status", "unknown"),
            project=projects.resolve(r.cwd if r else cwd) if (r or cwd) else (ws_project.get(a.get("workspace_id")) or projects.Project("?", "?")),
            name=session_name(r, by_session.get(sid), term_title), title=term_title,
            session_id=sid, pane_id=a["pane_id"], workspace_id=a.get("workspace_id", ""),
            tab_id=a.get("tab_id", ""), focused=bool(a.get("focused")), pid=r.pid if r else 0,
            activity=r.activity if r else "", subagents=r.subagents if r else [],
        ))
    mirror_of = {sid: pane for pane, sid in mirrors.items()}
    for r in running:
        if r.session_id in seen_ids:
            continue
        pane = mirror_of.get(r.session_id, "")
        pane_info = next((a for a in herdr_agents if a["pane_id"] == pane), {})
        agents.append(Agent(
            cli=r.cli, status=EXTERNAL_STATUS.get(r.status, "unknown"), project=r.project,
            name=session_name(r, by_session.get(r.session_id), ""), title=r.name,
            session_id=r.session_id, pid=r.pid, activity=r.activity, subagents=r.subagents,
            mirror_pane=pane, workspace_id=pane_info.get("workspace_id", ""), tab_id=pane_info.get("tab_id", ""),
        ))

    views: dict[str, ProjectView] = {}

    def view(p: projects.Project) -> ProjectView:
        base = projects.Project(p.root, p.name, "", p.path)
        v = views.setdefault(p.root, ProjectView(base))
        if p.worktree:
            v.worktrees.setdefault(p.worktree, Worktree(p.worktree, p.wt_path))
        return v

    for root, name in settings.load().projects.items():
        view(projects.resolve(root)).pinned = True
    for w in workspaces:
        if w.project:
            v = view(w.project)
            v.workspaces.append(w)
            if w.project.worktree:
                v.worktrees[w.project.worktree].workspace = w
    for a in agents:
        v = view(a.project)
        v.agents.append(a)
        if a.project.worktree:
            v.worktrees[a.project.worktree].agents.append(a)
    for s in sessions:
        v = view(s.project)
        v.sessions.append(s)
        if s.project.worktree:
            v.worktrees[s.project.worktree].sessions.append(s)

    selection = load_selection()
    for v in views.values():
        v.in_sidebar = v.live if selection is None else (v.project.root in selection)

    for a in agents:
        if not a.session_id:
            continue
        r, s = run_by_id.get(a.session_id), by_session.get(a.session_id)
        a.transcript = (r.transcript if r else "") or (s.path if s else "")
        a.context = insight.context(a.cli, a.transcript)
        if a.status == "working":
            continue
        a.question = asks.pending(a.cli, a.transcript)  # a pending question means it waits for you
        if a.question:
            a.status = "blocked"
        elif a.status == "idle" and insight.asks_you(insight.last_answer(a.cli, a.transcript, 1200)):
            a.status = "reply"
        if a.status in NEEDS_YOU:
            a.waiting_since = insight.last_answer_at(a.cli, a.transcript)

    live_sessions = {a.session_id: a for a in agents if a.session_id}
    ordered = sorted(
        views.values(),
        key=lambda v: (not v.in_sidebar, -v.attention(), not v.pinned, -v.last_activity, v.project.name.lower()),
    )
    return World(ordered, agents, workspaces, sessions, live_sessions, focused_ws, tab_labels, err)


def age(ts: float) -> str:
    d = max(0, time.time() - ts)
    if d < 90:
        return "now"
    if d < 3600:
        return f"{int(d // 60)}m"
    if d < 86400:
        return f"{int(d // 3600)}h"
    return f"{int(d // 86400)}d"
