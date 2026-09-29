"""Keep one mirror pane per agent session that runs outside herdr, for projects on the sidebar.

reconcile() opens missing mirrors (as a tab named after the session, in the workspace of its
worktree, else its repo), forgets mirrors whose pane was closed, and closes mirrors of
projects taken off the sidebar. It is cheap to call; status.py triggers it in the background
whenever the set of outside sessions changes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import herdr, model, settings

LOCK_STALE = 60


def _file() -> Path:
    return settings.state_dir() / "mirrors.json"


def load() -> dict[str, str]:
    return model.mirror_panes()


def save(m: dict[str, str]) -> None:
    _file().write_text(json.dumps(m), encoding="utf-8")


def forget(pane: str) -> None:
    m = load()
    if m.pop(pane, None) is not None:
        save(m)


def enabled() -> bool:
    return settings.load().mirror_outside


def reconcile() -> list[str]:
    lock = settings.state_dir() / "mirrors.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        if time.time() - lock.stat().st_mtime < LOCK_STALE:
            return []
        lock.unlink(missing_ok=True)
        return reconcile()
    try:
        return _reconcile()
    finally:
        lock.unlink(missing_ok=True)


def _reconcile() -> list[str]:
    msgs = []
    world = model.build()
    live_panes = {p["pane_id"] for p in herdr.snapshot().get("panes", [])}
    m = {pane: sid for pane, sid in load().items() if pane in live_panes}
    have = set(m.values())
    for a in world.agents:
        if a.pane_id or a.mirror_pane or a.session_id in have or not a.session_id:
            continue
        v = world.view(a.project.root)
        if not (enabled() and v and v.in_sidebar and v.live):
            continue
        ws = next((w for w in v.workspaces if w.project and w.project.worktree == a.project.worktree), None) \
            or next((w for w in v.workspaces if not w.linked_worktree), None)
        if not ws:
            continue
        try:
            res = herdr.run("plugin", "pane", "open", "--plugin", settings.PLUGIN_ID, "--entrypoint",
                            "mirror" if os.name == "nt" else "mirror-unix", "--placement", "tab",
                            "--workspace", ws.id, "--env", f"NAV_SESSION={a.cli}:{a.session_id}", "--no-focus")
        except herdr.HerdrError as e:
            msgs.append(f"✗ mirror {a.display}: {str(e)[:80]}")
            continue
        pane = ((res.get("plugin_pane") or {}).get("pane") or {})
        if pane.get("pane_id"):
            m[pane["pane_id"]] = a.session_id
            herdr.run("tab", "rename", pane["tab_id"], f"↗ {a.display}"[:28], check=False)
            msgs.append(f"＋ mirror {a.display}")
    # mirrors of projects that left the sidebar
    for pane, sid in list(m.items()):
        a = world.live_sessions.get(sid)
        v = world.view(a.project.root) if a else None
        if a and v and not v.in_sidebar:
            herdr.run("pane", "close", pane, check=False)
            del m[pane]
    save(m)
    return msgs


def spawn_background() -> None:
    """Run reconcile detached, so callers (the 3-second status line) never wait for it."""
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(root)}
    kw = {"creationflags": 0x00000008 | 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen([sys.executable, "-m", "navigator.mirrors"], env=env, cwd=str(root),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


if __name__ == "__main__":
    for line in reconcile():
        print(line)
