"""Rearrange the panes of a tab into a standard shape, keeping every process running.

herdr can only split a pane next to another one, and ignores moves within the same tab. So the
tab is rebuilt by moves: every pane except the first is parked in a temporary tab, then moved
back one by one. For a split node, the first pane of its second half goes next to the first
pane of its first half, and each half is then built the same way. Finally the split ratios are
set, so a 3-column layout really is thirds.
"""
from __future__ import annotations

import math

from . import herdr

SHAPES = {
    "columns": "▥ Columns",
    "rows": "▤ Rows",
    "grid": "▦ Grid",
    "main": "◧ Main + stack",
}

THUMBS = {  # 3-line previews for the picker
    "columns": ("┌┬┬┐", "│││", "└┴┴┘"),
    "rows": ("┌──┐", "├──┤", "└──┘"),
    "grid": ("┌┬┐", "├┼┤", "└┴┘"),
    "main": ("┌─┬┐", "│ ├┤", "└─┴┘"),
}


def _leaf(i: int) -> dict:
    return {"type": "pane", "i": i}


def _chain(idx: list[int], direction: str) -> dict:
    """n panes side by side (or stacked) with equal sizes."""
    if len(idx) == 1:
        return _leaf(idx[0])
    rest = _chain(idx[1:], direction)
    return {"type": "split", "direction": direction, "ratio": 1 / len(idx), "first": _leaf(idx[0]), "second": rest}


def shape(kind: str, n: int) -> dict:
    idx = list(range(n))
    if n == 1:
        return _leaf(0)
    if kind == "columns":
        return _chain(idx, "right")
    if kind == "rows":
        return _chain(idx, "down")
    if kind == "main":
        return {"type": "split", "direction": "right", "ratio": 0.6, "first": _leaf(0),
                "second": _chain(idx[1:], "down")}
    # grid: columns of stacked panes, as square as possible
    cols = math.ceil(math.sqrt(n))
    groups = [idx[c::cols] for c in range(cols)]
    groups = [sorted(g) for g in groups if g]
    col_nodes = [_chain(g, "down") for g in groups]

    def join(nodes: list[dict]) -> dict:
        if len(nodes) == 1:
            return nodes[0]
        return {"type": "split", "direction": "right", "ratio": 1 / len(nodes), "first": nodes[0],
                "second": join(nodes[1:])}
    return join(col_nodes)


def _first(node: dict) -> int:
    return node["i"] if node["type"] == "pane" else _first(node["first"])


def _moves(node: dict, out: list[tuple[int, int, str]]) -> None:
    """(pane index to move, anchor pane index, split direction), in build order."""
    if node["type"] == "pane":
        return
    out.append((_first(node["second"]), _first(node["first"]), node["direction"]))
    _moves(node["first"], out)
    _moves(node["second"], out)


def _paths(node: dict, path: list[bool], out: list[tuple[list[bool], float]]) -> None:
    if node["type"] == "split":
        out.append((path, node["ratio"]))
        _paths(node["first"], path + [False], out)
        _paths(node["second"], path + [True], out)


def ordered_panes(tab_id: str) -> list[str]:
    """Panes of a tab in reading order (top-left first), from the live layout."""
    snap = herdr.snapshot()
    lay = next((l for l in snap.get("layouts", []) if l.get("tab_id") == tab_id), {})
    ps = lay.get("panes", [])
    ps.sort(key=lambda p: (p.get("rect", {}).get("x", 0), p.get("rect", {}).get("y", 0)))
    return [p["pane_id"] for p in ps]


def arrange(tab_id: str, kind: str, panes: list[str] | None = None) -> str:
    panes = panes or ordered_panes(tab_id)
    n = len(panes)
    if n < 2:
        return "✗ only one pane here: split first"
    tree = shape(kind, n)
    # 1. park everything except the anchor in one temporary tab
    temp_tab = ""
    current = dict(enumerate(panes))
    for i in range(1, n):
        if not temp_tab:
            res = herdr.run("pane", "move", current[i], "--new-tab", "--no-focus")
            mr = res.get("move_result") or {}
            current[i] = (mr.get("pane") or {}).get("pane_id", current[i])
            temp_tab = (mr.get("pane") or {}).get("tab_id", "")
        else:
            res = herdr.run("pane", "move", current[i], "--tab", temp_tab, "--split", "right", "--no-focus")
            current[i] = ((res.get("move_result") or {}).get("pane") or {}).get("pane_id", current[i])
    # 2. bring them back in shape
    plan: list[tuple[int, int, str]] = []
    _moves(tree, plan)
    for moving, anchor, direction in plan:
        res = herdr.run("pane", "move", current[moving], "--tab", tab_id, "--target-pane", current[anchor],
                        "--split", direction, "--no-focus")
        current[moving] = ((res.get("move_result") or {}).get("pane") or {}).get("pane_id", current[moving])
    # 3. exact proportions
    ratios: list[tuple[list[bool], float]] = []
    _paths(tree, [], ratios)
    for path, ratio in ratios:
        herdr.request("layout.set_split_ratio", {"tab_id": tab_id, "path": path, "ratio": ratio})
    herdr.request("pane.focus", {"pane_id": current[0]})
    return f"{SHAPES[kind]}: {n} panes"
