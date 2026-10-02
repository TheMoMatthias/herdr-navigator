"""Fold a project in herdr's sidebar: its Agents tree and its Spaces' session rows shrink to the
agents that need you. Run from a key (prefix+f / F10: the focused pane's project; prefix+shift+f:
every project at once) or herdr's right-click menu on a pane or a workspace. Shares the fold
state with the Navigator's Agents tab, then re-syncs herdr's sidebar."""
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


def toggle_all(world) -> bool:
    """Fold every project, or unfold them all when everything is folded already."""
    folded = dict(startup.ui_state().get("agents_folded", {}))
    roots = {a.project.root for a in world.agents} | {w.project.root for w in world.workspaces if w.project}
    shut = not all(folded.get(r, False) for r in roots)
    for r in roots:
        folded[r] = shut
    startup.set_ui("agents_folded", folded)
    return shut


def main() -> None:
    import sys
    try:
        ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        ctx = {}
    world = model.build()
    if sys.argv[1:2] == ["all"]:
        shut = toggle_all(world)
        sync.coalesced()
        herdr_note("Folded every project" if shut else "Unfolded every project")
        return
    root = project_root(world, ctx)
    if root:
        shut = toggle(root)
        sync.coalesced()
        name = next((a.project.name for a in world.agents if a.project.root == root), os.path.basename(root))
        herdr_note(f"{'Folded' if shut else 'Unfolded'} {name}")
    else:
        herdr_note("Nothing to fold here: this pane is not in a project")


def herdr_note(text: str) -> None:
    from . import herdr
    herdr.run("notification", "show", text, "--body", "prefix+f / F10 folds this project, prefix+shift+f all",
              check=False)


if __name__ == "__main__":
    main()
