"""/compact with your own instructions, so you never type them again.

The text lives in navigator.toml (`[compact] instructions`, Settings > Prompts). Claude Code
takes it as `/compact <instructions>`; other CLIs get a plain `/compact`. Sent from the
Navigator (Compact button, the row menu, every ticked agent) or from herdr itself: a key
(prefix+shift+c) or right-click on a pane > "Navigator: compact this agent"."""
from __future__ import annotations

import json
import os

from . import herdr, settings

TAKES_INSTRUCTIONS = {"claude"}


def instructions() -> str:
    """One line: a newline would submit the command early in the CLI's input box."""
    return " ".join(str(settings.load().compact.get("instructions", "")).split())


def command(cli: str) -> str:
    text = instructions()
    return f"/compact {text}" if text and cli in TAKES_INSTRUCTIONS else "/compact"


def send(pane: str, cli: str) -> str:
    cmd = command(cli)
    herdr.run("agent", "prompt", pane, cmd)
    return "⇣ compacting" + (" with your instructions" if cmd != "/compact" else "")


def main() -> None:
    """herdr action: compact the agent in the pane it was invoked on (or the focused one)."""
    try:
        ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        ctx = {}
    snap = herdr.snapshot()
    pane = (os.environ.get("HERDR_PANE_ID") or ctx.get("pane_id") or (ctx.get("pane") or {}).get("pane_id")
            or snap.get("focused_pane_id", ""))
    agent = next((a for a in snap.get("agents", []) if a.get("pane_id") == pane), None)
    if not agent:
        herdr.run("notification", "show", "Nothing to compact here", "--body",
                  "This pane runs no agent. Focus an agent's pane, then press prefix+shift+c.", check=False)
        return
    name = agent.get("terminal_title_stripped") or pane
    try:
        msg = send(pane, agent.get("agent", ""))
    except herdr.HerdrError as e:
        msg = "not sent: " + ("it is waiting on a question or approval" if "agent_blocked" in str(e) else str(e)[:120])
    herdr.run("notification", "show", f"{name}: {msg}", check=False)


if __name__ == "__main__":
    main()
