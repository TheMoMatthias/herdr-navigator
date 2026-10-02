"""herdr right-click (pane or workspace) › "Navigator: fold / unfold this project in Agents".
Shares the fold state with the Navigator's Agents tab, then re-syncs herdr's Agents panel."""
from __future__ import annotations

import json
import os

from . import model, startup, sync


def project_root(world, ctx: dict) -> str:
    pane = os.environ.get("HERDR_PANE_ID") or ctx.get("pane_id") or (ctx.get("pane") or {}).get("pane_id", "")
    ws = os.environ.get("HERDR_WORKSPACE_ID") or ctx.get("workspace_id") or (ctx.get("workspace") or {}).get(
        "workspace_id", "")
    a = next((a for a in world.agents if pane and pane in (a.pane_id, a.mirror_pane)), None)
    if a:
        return a.project.root
    p = world.project_of_workspace(ws) if ws else None
    return p.root if p else ""


def toggle(root: str) -> bool:
    folded = dict(startup.ui_state().get("agents_folded", {}))
    folded[root] = not folded.get(root, False)
    startup.set_ui("agents_folded", folded)
    return folded[root]


def main() -> None:
    try:
        ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        ctx = {}
    world = model.build()
    root = project_root(world, ctx)
    if root:
        toggle(root)
        sync.sync()


if __name__ == "__main__":
    main()
