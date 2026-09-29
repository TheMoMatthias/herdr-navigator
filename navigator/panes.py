"""Pane operations for the Panes tab: everything herdr can do to a pane, callable by button.

Existing panes are only ever split, swapped, resized, moved, renamed, zoomed or closed; their
processes keep running. Layout presets create a *new* tab, so they never touch live panes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from . import herdr

RESIZE_STEP = 0.05


@dataclass
class PaneBox:
    pane_id: str
    x: int
    y: int
    w: int
    h: int
    focused: bool
    label: str
    status: str = ""


@dataclass
class TabLayout:
    tab_id: str
    workspace_id: str
    area: tuple[int, int]      # width, height
    zoomed: bool
    panes: list[PaneBox]


def current(snap: dict | None = None, names: dict[str, str] | None = None) -> TabLayout | None:
    """Layout of the focused tab, with a readable label per pane."""
    snap = snap or herdr.snapshot()
    tab_id = snap.get("focused_tab_id", "")
    lay = next((l for l in snap.get("layouts", []) if l.get("tab_id") == tab_id), None)
    if not lay:
        return None
    info = {p["pane_id"]: p for p in snap.get("panes", [])}
    boxes = []
    for p in lay.get("panes", []):
        r = p.get("rect", {})
        i = info.get(p["pane_id"], {})
        label = (names or {}).get(p["pane_id"]) or i.get("label") or i.get("agent") \
            or os.path.basename((i.get("cwd") or "").rstrip("\\/")) or p["pane_id"]
        boxes.append(PaneBox(p["pane_id"], r.get("x", 0), r.get("y", 0), r.get("width", 1), r.get("height", 1),
                             bool(p.get("focused")), label, i.get("agent_status", "") if i.get("agent") else ""))
    a = lay.get("area", {})
    return TabLayout(tab_id, lay.get("workspace_id", ""), (a.get("width", 1), a.get("height", 1)),
                     bool(lay.get("zoomed")), boxes)


def neighbor(layout: TabLayout, pane_id: str, direction: str) -> str | None:
    """Nearest pane in a direction, by geometry (for arrow-key selection in the map)."""
    me = next((b for b in layout.panes if b.pane_id == pane_id), None)
    if not me:
        return None
    cx, cy = me.x + me.w / 2, me.y + me.h / 2
    best, best_d = None, 1e9
    for b in layout.panes:
        if b.pane_id == pane_id:
            continue
        bx, by = b.x + b.w / 2, b.y + b.h / 2
        ok = {"left": bx < cx and b.x + b.w <= me.x + 1, "right": bx > cx and b.x >= me.x + me.w - 1,
              "up": by < cy and b.y + b.h <= me.y + 1, "down": by > cy and b.y >= me.y + me.h - 1}[direction]
        if ok:
            d = abs(bx - cx) + abs(by - cy)
            if d < best_d:
                best, best_d = b.pane_id, d
    return best


# --- operations (all return a short message for the status line) --------------------------

def focus(pane_id: str) -> str:
    herdr.request("pane.focus", {"pane_id": pane_id})
    return f"→ {pane_id}"


def split(pane_id: str, direction: str) -> str:
    res = herdr.run("pane", "split", pane_id, "--direction", direction, "--focus")
    return f"＋ {(res.get('pane') or {}).get('pane_id', '')} {'right of' if direction == 'right' else 'below'} {pane_id}"


def zoom(pane_id: str) -> str:
    herdr.run("pane", "zoom", pane_id, "--toggle")
    return f"⛶ zoom {pane_id}"


def swap(pane_id: str, direction: str) -> str:
    herdr.run("pane", "swap", "--pane", pane_id, "--direction", direction)
    return f"⇆ {pane_id} {direction}"


def resize(pane_id: str, direction: str, amount: float = RESIZE_STEP) -> str:
    herdr.run("pane", "resize", "--pane", pane_id, "--direction", direction, "--amount", str(amount))
    return f"⇲ {pane_id} {direction}"


def equalize(tab_id: str) -> str:
    """Every split in the tab to 50/50."""
    root = herdr.request("layout.export", {"tab_id": tab_id}).get("layout", {}).get("root", {})
    paths: list[list[bool]] = []

    def walk(node: dict, path: list[bool]) -> None:
        if node.get("type") == "split":
            paths.append(path)
            walk(node.get("first", {}), path + [False])
            walk(node.get("second", {}), path + [True])

    walk(root, [])
    for p in paths:
        herdr.request("layout.set_split_ratio", {"tab_id": tab_id, "path": p, "ratio": 0.5})
    return f"= {len(paths)} splits evened out"


def to_new_tab(pane_id: str, label: str = "") -> str:
    args = ["pane", "move", pane_id, "--new-tab", "--focus"]
    if label:
        args += ["--tab-label", label[:24]]
    herdr.run(*args)
    return f"↦ {pane_id} → new tab"


def to_new_workspace(pane_id: str, label: str = "") -> str:
    args = ["pane", "move", pane_id, "--new-workspace", "--focus"]
    if label:
        args += ["--label", label[:24]]
    herdr.run(*args)
    return f"↦ {pane_id} → new workspace"


def to_tab(pane_id: str, tab_id: str, target_pane: str, split_dir: str = "right") -> str:
    herdr.run("pane", "move", pane_id, "--tab", tab_id, "--target-pane", target_pane, "--split", split_dir,
              "--focus")
    return f"↦ {pane_id} → {tab_id}"


def rename(pane_id: str, label: str) -> str:
    if label:
        herdr.run("pane", "rename", pane_id, label)
    else:
        herdr.run("pane", "rename", pane_id, "--clear")
    return f"✎ {pane_id}"


def close(pane_id: str) -> str:
    herdr.run("pane", "close", pane_id)
    return f"✕ {pane_id} closed"


# --- presets: a new tab with a ready-made layout --------------------------------------------

def _pane(cwd: str, command: list[str] | None = None) -> dict:
    n = {"type": "pane", "cwd": cwd}
    if command:
        n["command"] = command
    return n


def _split(direction: str, a: dict, b: dict, ratio: float = 0.5) -> dict:
    return {"type": "split", "direction": direction, "ratio": ratio, "first": a, "second": b}


PRESETS = {
    "2 columns": lambda c: _split("right", _pane(c), _pane(c)),
    "3 columns": lambda c: _split("right", _pane(c), _split("right", _pane(c), _pane(c)), 0.333),
    "2 rows": lambda c: _split("down", _pane(c), _pane(c)),
    "2×2 grid": lambda c: _split("right", _split("down", _pane(c), _pane(c)), _split("down", _pane(c), _pane(c))),
    "main + 2 stacked": lambda c: _split("right", _pane(c), _split("down", _pane(c), _pane(c)), 0.6),
}


def preset(name: str, workspace_id: str, cwd: str) -> str:
    herdr.request("layout.apply", {"workspace_id": workspace_id, "tab_label": name, "focus": True,
                                   "root": PRESETS[name](cwd)})
    return f"＋ new tab: {name}"
