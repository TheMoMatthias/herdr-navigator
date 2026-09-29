"""The "needs you" queue: agents that are blocked (waiting for an answer or approval) or done
(finished, not yet looked at), oldest first, blocked before done.

`run attention next` (bound to Ctrl+Alt+I / F8) jumps to the head of the queue; focusing an agent
marks it seen, so pressing again walks the queue.
"""
from __future__ import annotations

import sys

from . import herdr

ORDER = {"blocked": 0, "done": 1}


def queue(snap: dict | None = None) -> list[dict]:
    snap = snap or herdr.snapshot()
    agents = [a for a in snap.get("agents", []) if a.get("agent_status") in ORDER]
    return sorted(agents, key=lambda a: (ORDER[a["agent_status"]], a.get("state_change_seq", 0)))


def label(a: dict, snap: dict) -> str:
    ws = {w["workspace_id"]: w.get("label", "") for w in snap.get("workspaces", [])}
    title = a.get("terminal_title_stripped") or a.get("agent", "agent")
    return f"{ws.get(a.get('workspace_id'), '')} · {title}"[:60]


def next_() -> str:
    snap = herdr.snapshot()
    q = [a for a in queue(snap) if not a.get("focused")] or queue(snap)
    if not q:
        herdr.run("notification", "show", "Nothing needs you", "--body",
                  "No agent is blocked or waiting to be looked at.", check=False)
        return "nothing needs you"
    a = q[0]
    herdr.run("agent", "focus", a["pane_id"])
    rest = len(q) - 1
    herdr.run("notification", "show",
              f"{'⚠ waiting for you' if a['agent_status'] == 'blocked' else '✔ finished'}: {label(a, snap)}",
              "--body", f"{rest} more in the queue: press Ctrl+Alt+I again." if rest else "Queue is empty after this.",
              check=False)
    return a["pane_id"]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "next":
        print(next_())
