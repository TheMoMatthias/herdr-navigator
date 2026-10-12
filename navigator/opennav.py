"""F1 (and the other Navigator keys): open the Navigator where you are, and close it again.

The Navigator opens as a zoomed pane over the tab you are in, not as a popup: herdr's popup is
session-modal (nothing else takes a click while it is open) and there is only one per session.
A zoomed pane leaves the sidebar and the other workspaces usable. Pressed where it is open (on
the same tab key), the key closes it; pressed anywhere else, it moves there: the open one closes
and a new one opens over the pane you are in, back on the tab and row you were on.
"""
from __future__ import annotations

import os
import sys
import time

from . import herdr, jsonfile, settings


def _state():
    return settings.state_dir() / "nav-popup.json"


def _close(pane: str) -> None:
    try:
        herdr.run("plugin", "pane", "close", pane)
    except (herdr.HerdrError, OSError, ValueError):
        pass  # already gone


def _open(args: list[str]) -> str:
    try:
        r = herdr.run("plugin", "pane", "open", "--plugin", settings.PLUGIN_ID, *args)
    except (herdr.HerdrError, OSError, ValueError):
        return ""
    return ((r.get("plugin_pane") or {}).get("pane") or {}).get("pane_id", "")


def toggle(tab: str) -> str:
    """Open, move or close the Navigator for `tab`. Returns "opened", "moved" or "closed"."""
    snap = herdr.snapshot()
    here = snap.get("focused_pane_id", "") or ""
    panes = {p.get("pane_id") for p in snap.get("panes", [])}
    last = jsonfile.read(_state(), {}) or {}
    mine = last.get("pane", "")
    open_ = bool(mine) and mine in panes
    if open_:
        _close(mine)
        if here == mine and last.get("tab") == tab:
            jsonfile.write(_state(), {})
            return "closed"
        if here == mine:  # another tab key while looking at it: reopen over the pane it covered
            here = last.get("from", "")
    if here not in panes:
        here = ""
    entry = "navigator" if os.name == "nt" else "navigator-unix"
    env = ["--env", f"NAV_TAB={tab}", "--env", f"NAV_FROM_PANE={here}"]
    if here:
        pane = _open(["--entrypoint", entry, "--placement", "zoomed", "--target-pane", here, "--focus", *env])
    else:  # an empty tab has no pane to cover: a tab of its own
        pane = _open(["--entrypoint", entry, "--placement", "tab", "--focus", *env,
                      *(["--workspace", snap["focused_workspace_id"]] if snap.get("focused_workspace_id") else [])])
    jsonfile.write(_state(), {"pane": pane, "tab": tab, "from": here, "at": time.time()})
    return "moved" if open_ else "opened"


def as_pane() -> None:
    """Called at the Navigator's start when it runs in its own pane: the helpers that act on "the
    pane you are in" (HERDR_PANE_ID / HERDR_ACTIVE_PANE_ID) must mean the pane it was opened from,
    and the Layout map leaves the Navigator's own pane out (NAV_SELF_PANE)."""
    frm = os.environ.pop("NAV_FROM_PANE", None)
    if frm is None:
        return
    os.environ["NAV_SELF_PANE"] = os.environ.pop("HERDR_PANE_ID", "")
    os.environ.pop("HERDR_TAB_ID", None)
    if frm:
        os.environ["HERDR_ACTIVE_PANE_ID"] = frm
    else:
        os.environ.pop("HERDR_ACTIVE_PANE_ID", None)


if __name__ == "__main__":
    toggle(sys.argv[1] if len(sys.argv) > 1 else "projects")
