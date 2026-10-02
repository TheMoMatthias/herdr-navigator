"""Recent places: every pane you focus, most recent first, across tabs, workspaces and projects.

Fed by the plugin's `pane.focused` event hook (`run history record`). Kept small and
stdlib-only: the hook fires on every focus change.
"""
from __future__ import annotations

import json
import os
import sys
import time

from . import settings

MAX = 60


def _file():
    return settings.state_dir() / "history.json"


def load() -> list[dict]:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _find(obj, key: str):
    if isinstance(obj, dict):
        if isinstance(obj.get(key), str):
            return obj[key]
        for v in obj.values():
            r = _find(v, key)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, key)
            if r:
                return r
    return None


def record(pane_id: str | None = None) -> None:
    if not pane_id:
        try:
            ev = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "{}"))
        except ValueError:
            ev = {}
        pane_id = _find(ev, "pane_id") or os.environ.get("HERDR_PANE_ID")
    if not pane_id:
        return
    h = [e for e in load() if e.get("pane_id") != pane_id]
    h.insert(0, {"pane_id": pane_id, "at": time.time()})
    try:
        _file().write_text(json.dumps(h[:MAX]), encoding="utf-8")
    except OSError:
        pass


def recent(live_panes: set[str]) -> list[dict]:
    """History entries whose pane still exists, most recent first."""
    return [e for e in load() if e.get("pane_id") in live_panes]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "record":
        from .hook import _alive
        if _alive():
            raise SystemExit(0)  # the daemon records focus changes from its event stream
        record(sys.argv[2] if len(sys.argv) > 2 else None)
