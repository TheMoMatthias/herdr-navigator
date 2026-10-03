"""Pane layout actions for the pane menu (panemenu.py) and herdr plugin actions: split right/down,
zoom, move the pane left/right/up/down, even out the splits, move it to a new tab, or open the
Layout map for it. Usage: run.cmd paneact <split-right|split-down|zoom|move-left|move-right|
move-up|move-down|even|newtab|arrange>."""
from __future__ import annotations

import json
import os
import sys

from . import herdr, panes, settings


def target() -> tuple[str, str]:
    """(pane, tab) the menu was opened on, else the focused pane."""
    try:
        ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        ctx = {}
    snap = herdr.snapshot()
    pane = (os.environ.get("HERDR_PANE_ID") or os.environ.get("HERDR_ACTIVE_PANE_ID") or ctx.get("pane_id") or (ctx.get("pane") or {}).get("pane_id")
            or snap.get("focused_pane_id", ""))
    tab = os.environ.get("HERDR_TAB_ID") or next(
        (p.get("tab_id", "") for p in snap.get("panes", []) if p.get("pane_id") == pane), "")
    return pane, tab


def run(op: str) -> str:
    from .compact import _note_context
    try:
        _note_context(json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}"))
    except ValueError:
        pass
    pane, tab = target()
    if not pane:
        return "✗ no pane here"
    if op.startswith("split-"):
        return panes.split(pane, op[6:])
    if op == "zoom":
        return panes.zoom(pane)
    if op.startswith("move-"):
        return panes.swap(pane, op[5:])
    if op == "even":
        return panes.equalize(tab)
    if op == "newtab":
        return panes.to_new_tab(pane)
    if op == "arrange":  # the Navigator's Layout map, this pane selected
        entry = "navigator" if os.name == "nt" else "navigator-unix"
        herdr.run("plugin", "pane", "open", "--plugin", settings.PLUGIN_ID, "--entrypoint", entry,
                  "--placement", "overlay", "--focus", "--env", "NAV_TAB=panes", "--env", f"NAV_PANE={pane}",
                  check=False)
        return ""
    return f"✗ unknown pane action {op}"


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    op = argv[0] if argv else ""
    try:
        msg = run(op)
    except herdr.HerdrError as e:
        msg = "✗ " + str(e)[:160]
        if op.startswith("move-") and ("no pane" in msg.lower() or "neighbor" in msg.lower()):
            msg = f"✗ nothing to swap with {op[5:].replace('-', ' ')} of this pane"
    if msg.startswith("✗"):
        herdr.run("notification", "show", "Pane not changed", "--body", msg[2:], check=False)


if __name__ == "__main__":
    main()
