"""Which sessions reopen at logon: ticks, the rolling auto-tick, and per-session launch options.

A choice you make yourself (tick, untick, project on/off) is stored and always wins. Everything
else is decided fresh each time by the auto-tick: per project lane (the main checkout, and each
worktree on its own) the newest `per_lane` sessions you worked in during the last `window_days`.
Only explicit choices are stored, so the rule keeps rolling forward on its own.

State file: <state dir>/startup.json
  {"projects": {root: {"on": bool, "auto": bool}},
   "sessions": {"cli:id": {"tick": bool, "prefs": {...}}}}
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from . import projects, settings
from .sessions import Session

EFFORTS = ["", "low", "medium", "high", "xhigh", "max"]
PERMISSION_MODES = ["", "default", "acceptEdits", "auto", "plan", "dontAsk", "bypassPermissions"]


def _path():
    return settings.state_dir() / "startup.json"


def load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("projects", {})
    data.setdefault("sessions", {})
    return data


def save(data: dict) -> None:
    _path().write_text(json.dumps(data, indent=1), encoding="utf-8")


def skey(s: Session) -> str:
    return f"{s.cli}:{s.id}"


def lane(s: Session) -> str:
    return s.project.worktree or "main"


@dataclass
class Row:
    session: Session
    ticked: bool
    pinned: bool            # the tick is your choice (else the auto-tick decided)
    prefs: dict = field(default_factory=dict)


@dataclass
class ProjectRows:
    project: projects.Project
    on: bool                # restore this project at logon
    pinned: bool            # on/off is your choice
    auto: bool              # auto-tick runs in this project
    rows: list[Row] = field(default_factory=list)

    @property
    def ticked(self) -> list[Row]:
        return [r for r in self.rows if r.ticked] if self.on else []


def plan(sessions: list[Session], data: dict | None = None, now: float | None = None) -> list[ProjectRows]:
    """Every listed session grouped by project, with its tick decided. Newest project first."""
    data = data if data is not None else load()
    cfg = settings.load().restore
    now = now or time.time()
    per_lane = int(cfg.get("per_lane", 3))
    window = float(cfg.get("window_days", 3)) * 86400
    recency = float(cfg.get("recency_days", 14)) * 86400
    with_wt = bool(cfg.get("include_worktrees", True))
    groups: dict[str, ProjectRows] = {}
    for s in sorted(sessions, key=lambda s: -s.mtime):
        if s.subagent or (s.project.worktree and not with_wt):
            continue
        p = s.project
        g = groups.get(p.root)
        if not g:
            pd = data["projects"].get(p.root, {})
            pinned = "on" in pd
            on = pd["on"] if pinned else (now - s.mtime) <= recency
            base = projects.Project(p.root, p.name, "", p.path)
            g = groups[p.root] = ProjectRows(base, on, pinned, pd.get("auto", True))
        sd = data["sessions"].get(skey(s), {})
        g.rows.append(Row(s, False, "tick" in sd, sd.get("prefs", {})))
    for g in groups.values():
        budget: dict[str, int] = {}
        for r in g.rows:  # explicit ticks first: they use up their lane's budget
            if r.pinned:
                r.ticked = bool(data["sessions"][skey(r.session)]["tick"])
                if r.ticked:
                    budget[lane(r.session)] = budget.get(lane(r.session), 0) + 1
        for r in g.rows:  # newest first
            if r.pinned or not g.auto:
                continue
            ln = lane(r.session)
            if now - r.session.mtime <= window and budget.get(ln, 0) < per_lane:
                r.ticked = True
                budget[ln] = budget.get(ln, 0) + 1
    return sorted(groups.values(), key=lambda g: -max(r.session.mtime for r in g.rows))


def selected(sessions: list[Session], data: dict | None = None) -> list[Row]:
    """The sessions that reopen at logon, newest first, capped at max_sessions."""
    rows = [r for g in plan(sessions, data) for r in g.ticked]
    rows.sort(key=lambda r: -r.session.mtime)
    return rows[: int(settings.load().restore.get("max_sessions", 30))]


# ---- changing choices -------------------------------------------------------------------------

def set_tick(key: str, on: bool | None) -> None:
    """on=None hands the session back to the auto-tick."""
    data = load()
    sd = data["sessions"].setdefault(key, {})
    if on is None:
        sd.pop("tick", None)
    else:
        sd["tick"] = on
    if not sd:
        data["sessions"].pop(key, None)
    save(data)


def set_project(root: str, on: bool | None = None, auto: bool | None = None) -> None:
    data = load()
    pd = data["projects"].setdefault(root, {})
    if on is not None:
        pd["on"] = on
    if auto is not None:
        pd["auto"] = auto
    save(data)


def set_prefs(key: str, prefs: dict) -> None:
    data = load()
    sd = data["sessions"].setdefault(key, {})
    clean = {k: v for k, v in prefs.items() if v not in ("", None)}
    if clean:
        sd["prefs"] = clean
    else:
        sd.pop("prefs", None)
    if not sd:
        data["sessions"].pop(key, None)
    save(data)


def prefs_of(key: str) -> dict:
    return load()["sessions"].get(key, {}).get("prefs", {})


# ---- the command that relaunches a session ----------------------------------------------------

def _q(text: str) -> str:
    """Double-quoted argument that reads the same in PowerShell, cmd and POSIX shells."""
    safe = "".join(ch for ch in text if ch not in '"`$%!\\\r\n').strip()
    return f'"{safe or "session"}"'


def launch_command(cli: str, sid: str, name: str = "", prefs: dict | None = None) -> str:
    """Resume command for one session, with its name and launch options (Claude flags)."""
    cfg = settings.load()
    cmd = cfg.launch.get(f"{cli}_resume", "").format(id=sid)
    if not cmd:
        return ""
    prefs = prefs or {}
    if cli == "claude":
        r = cfg.restore
        if name and r.get("claude_name", True):
            cmd += f" -n {_q(name)}"
        rc = prefs.get("remote_control", r.get("claude_remote_control", False))
        if rc:
            cmd += f" --remote-control {_q(name)}" if name else " --remote-control"
        if prefs.get("model"):
            cmd += f" --model {_q(prefs['model'])}"
        if prefs.get("effort") in EFFORTS[1:]:
            cmd += f" --effort {prefs['effort']}"
        if prefs.get("permission_mode") in PERMISSION_MODES[1:]:
            cmd += f" --permission-mode {prefs['permission_mode']}"
    if prefs.get("args"):
        cmd += " " + str(prefs["args"]).strip()
    return cmd


# ---- one-time import from the session-restore tool ----------------------------------------------

def import_session_restore(registry_path: str) -> str:
    """Carry over ticks, project switches and Claude launch options from session-restore's
    sessions-registry.json. Only explicit choices (pinned ticks, project on/off) come over:
    its auto-ticks are recomputed here by the same rule."""
    reg = json.loads(open(registry_path, encoding="utf-8-sig").read())
    data = load()
    n_s = n_p = 0
    for d in reg.get("directories", []):
        path = d.get("path", "")
        if not path:
            continue
        root = projects.resolve(path).root
        if d.get("shelved") or d.get("enabled") is False:
            data["projects"].setdefault(root, {})["on"] = False
            n_p += 1
        for s in d.get("sessions", []):
            sid = s.get("sessionId")
            if not sid or s.get("gone"):
                continue
            key = f"claude:{sid}"
            sd = data["sessions"].setdefault(key, {})
            if s.get("pinned"):
                sd["tick"] = bool(s.get("enabled"))
                n_s += 1
            p = s.get("prefs") or {}
            prefs = {"model": p.get("model"), "effort": p.get("effort"),
                     "permission_mode": p.get("permissionMode")}
            if p.get("remoteControl") is not None:
                prefs["remote_control"] = bool(p["remoteControl"])
            prefs = {k: v for k, v in prefs.items() if v not in ("", None)}
            if prefs:
                sd["prefs"] = prefs
            if not sd:
                data["sessions"].pop(key, None)
    save(data)
    return f"imported {n_s} ticks and {n_p} switched-off projects"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "import":
        print(import_session_restore(sys.argv[2]))
