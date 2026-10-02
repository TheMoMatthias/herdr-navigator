"""Saved project layouts: remember a tab's panes (split tree, cwd, names, which agent ran where)
and rebuild it later, in the project's workspace, with the agents started again.

Restoring never touches existing panes: herdr builds a new tab (`layout.apply`). An agent
comes back as its own session (`claude --resume`, `codex resume`) when that session is not
running anywhere, otherwise as a fresh agent of the same CLI in the same place.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import herdr, settings
from .model import World
from .projects import Project


def _dir() -> Path:
    d = settings.state_dir() / "layouts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(project: Project) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", project.root.lower()).strip("-")[-80:]
    return _dir() / f"{slug}.json"


def saved(project: Project) -> dict[str, dict]:
    try:
        return json.loads(_file(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(world: World, project: Project, tab_id: str, name: str) -> str:
    root = herdr.request("layout.export", {"tab_id": tab_id}).get("layout", {}).get("root")
    if not root:
        return "✗ nothing to save"
    by_pane = {a.pane_id: a for a in world.agents if a.pane_id}
    labels = {}
    for t in herdr.snapshot().get("panes", []):
        labels[t["pane_id"]] = t.get("label") or ""

    def strip(n: dict) -> dict:
        if n.get("type") == "split":
            return {"type": "split", "direction": n["direction"], "ratio": n.get("ratio", 0.5),
                    "first": strip(n["first"]), "second": strip(n["second"])}
        a = by_pane.get(n.get("pane_id", ""))
        node = {"type": "pane", "cwd": n.get("cwd", "")}
        if labels.get(n.get("pane_id", "")):
            node["label"] = labels[n["pane_id"]]
        if a:
            node["agent"] = {"cli": a.cli, "session": a.session_id, "name": a.display}
        return node

    data = saved(project)
    data[name] = {"saved_at": time.time(), "root": strip(root)}
    _file(project).write_text(json.dumps(data, indent=1), encoding="utf-8")
    return f"💾 saved layout '{name}' for {project.name}"


def delete(project: Project, name: str) -> str:
    data = saved(project)
    if data.pop(name, None) is None:
        return f"✗ no layout '{name}'"
    _file(project).write_text(json.dumps(data, indent=1), encoding="utf-8")
    return f"🗑 deleted layout '{name}'"


def _leaves(n: dict) -> list[dict]:
    return [n] if n.get("type") == "pane" else _leaves(n["first"]) + _leaves(n["second"])


def _for_apply(n: dict) -> dict:
    if n.get("type") == "split":
        return {"type": "split", "direction": n["direction"], "ratio": n.get("ratio", 0.5),
                "first": _for_apply(n["first"]), "second": _for_apply(n["second"])}
    out = {"type": "pane", "cwd": n.get("cwd") or None}
    if n.get("label"):
        out["label"] = n["label"]
    return out


def restore(world: World, project: Project, name: str) -> str:
    spec = saved(project).get(name)
    if not spec:
        return f"✗ no layout '{name}'"
    ws = world.workspace_for(project)
    created = False
    if ws is None:
        res = herdr.create_workspace(project.path or project.root, label=project.name, focus=True)
        ws_id = (res.get("workspace") or {}).get("workspace_id", "")
        first_tab = (res.get("tab") or {}).get("tab_id", "")
        created = True
    else:
        ws_id, first_tab = ws.id, ""
    res = herdr.request("layout.apply", {"workspace_id": ws_id, "tab_label": name[:24], "focus": True,
                                         "root": _for_apply(spec["root"])})
    new_root = (res.get("layout") or {}).get("root", {})
    started = 0
    launch = settings.load().launch
    for old, new in zip(_leaves(spec["root"]), _leaves(new_root)):
        agent = old.get("agent")
        if not agent or not new.get("pane_id"):
            continue
        cli, sid = agent.get("cli", ""), agent.get("session", "")
        busy = sid and sid in world.live_sessions
        tpl = launch.get(f"{cli}_resume") if sid and not busy else None
        if tpl:  # named, with its launch options, like every other relaunch
            from . import startup
            cmd = startup.launch_command(cli, sid, agent.get("name", ""), startup.prefs_of(f"{cli}:{sid}"))
        else:
            cmd = launch.get(f"{cli}_new", cli)
        if cmd:
            herdr.run("pane", "run", new["pane_id"], cmd, check=False)
            started += 1
    if created and first_tab:
        herdr.run("tab", "close", first_tab, check=False)  # the empty tab the new workspace came with
    herdr.run("workspace", "focus", ws_id, check=False)
    return f"▦ restored '{name}' in {project.name} ({started} agent{'s' if started != 1 else ''} started)"
