"""/compact with your own instructions, so you never type them again.

The text lives in navigator.toml (`[compact] instructions`, Settings > Prompts). Claude Code
takes it as `/compact <instructions>`; other CLIs get their plain command (`/compact`, or
`/compress` for Gemini and its fork Qwen). Sent from the
Navigator (Compact button, the row menu, every ticked agent) or from herdr itself: a key
(prefix+shift+c) or the pane menu (F5, panemenu.py)."""
from __future__ import annotations

import json
import os

from . import herdr, settings

TAKES_INSTRUCTIONS = {"claude"}
PLAIN = {"gemini": "/compress", "qwen": "/compress"}  # everything else: /compact


def instructions() -> str:
    """One line: a newline would submit the command early in the CLI's input box."""
    return " ".join(str(settings.load().compact.get("instructions", "")).split())


def command(cli: str) -> str:
    text = instructions()
    return f"/compact {text}" if text and cli in TAKES_INSTRUCTIONS else PLAIN.get(cli, "/compact")


def send(pane: str, cli: str) -> str:
    cmd = command(cli)
    herdr.run("agent", "prompt", pane, cmd)
    return "⇣ compacting" + (" with your instructions" if " " in cmd else "")


def _note_context(ctx: dict) -> None:
    """What herdr handed the last action (for diagnosing a right-click that picks the wrong pane)."""
    try:
        keep = {k: os.environ.get(k, "") for k in ("HERDR_PANE_ID", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID",
                                                    "HERDR_PLUGIN_ACTION_ID")}
        (settings.state_dir() / "last-action.json").write_text(json.dumps({"env": keep, "context": ctx}),
                                                               encoding="utf-8")
    except Exception:  # a diagnostic note never stops the action
        pass


def main() -> None:
    """herdr action: compact the agent in the pane it was invoked on (or the focused one)."""
    try:
        ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        ctx = {}
    snap = herdr.snapshot()
    agents = snap.get("agents", [])
    pane = os.environ.get("HERDR_PANE_ID") or ctx.get("pane_id") or (ctx.get("pane") or {}).get("pane_id")
    ws = os.environ.get("HERDR_WORKSPACE_ID") or ctx.get("workspace_id") or (ctx.get("workspace") or {}).get(
        "workspace_id")
    agent = next((a for a in agents if pane and a.get("pane_id") == pane
                  and (not ws or a.get("workspace_id") == ws)), None)  # never a pane of another Space
    if not agent and ws:  # right-click on a Space: its agent, when there is exactly one
        here = [a for a in agents if a.get("workspace_id") == ws]
        if len(here) > 1:
            herdr.run("notification", "show", f"{len(here)} agents in this Space", "--body",
                      "Right-click the agent's own pane (or focus it and press prefix+shift+c).", check=False)
            return
        agent = here[0] if here else None
    if not agent and not pane and not ws:
        agent = next((a for a in agents if a.get("pane_id") == snap.get("focused_pane_id")), None)
    if agent:
        pane = agent["pane_id"]
    _note_context(ctx)
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
