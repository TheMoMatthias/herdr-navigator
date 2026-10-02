"""While you were away: what changed since the Navigator was last open.

asks      an agent waits on a question or approval
finished  an agent stopped working since then (its turn is done)
ended     a session that is no longer running wrote to its transcript since then
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from . import insight, settings

AWAY_SECONDS = 600
MAX_ITEMS = 14


@dataclass
class Item:
    kind: str           # asks | finished | ended
    name: str
    project: str
    line: str
    pane_id: str = ""   # where to jump (herdr agents and mirrors)


def _seen_file():
    return settings.state_dir() / "seen.json"


def last_seen() -> float:
    try:
        return float(json.loads(_seen_file().read_text(encoding="utf-8"))["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0


def mark_seen(now: float | None = None) -> None:
    try:
        _seen_file().write_text(json.dumps({"at": now or time.time()}), encoding="utf-8")
    except OSError:
        pass


def _first_line(text: str, n: int = 90) -> str:
    for ln in text.splitlines():
        ln = ln.strip(" #*-•>")
        if ln:
            return ln[:n]
    return ""


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path) if path else 0.0
    except OSError:
        return 0.0


def items(world, since: float) -> list[Item]:
    out: list[Item] = []
    running = set()
    for a in world.agents:
        running.add(a.session_id)
        jump = a.pane_id or a.mirror_pane
        if a.question or a.status == "blocked":
            line = a.question.text if a.question else (a.activity or a.title or "waiting for you")
            out.append(Item("asks", a.display, a.project.label, line[:90], jump))
        elif a.status in ("done", "idle") and _mtime(a.transcript) > since and \
                insight.last_answer_at(a.cli, a.transcript) > since:  # it answered, not just resumed
            out.append(Item("finished", a.display, a.project.label,
                            _first_line(insight.last_answer(a.cli, a.transcript, 4000)), jump))
    for s in world.sessions:
        if s.id in running or s.mtime <= since or s.subagent or insight.last_answer_at(s.cli, s.path) <= since:
            continue
        out.append(Item("ended", s.title, s.project.label, _first_line(insight.last_answer(s.cli, s.path, 4000))))
    order = {"asks": 0, "finished": 1, "ended": 2}
    out.sort(key=lambda i: order[i.kind])
    return out[:MAX_ITEMS]


def due(since: float, now: float | None = None) -> bool:
    return since > 0 and (now or time.time()) - since >= AWAY_SECONDS
