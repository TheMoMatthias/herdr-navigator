"""The pane menu: one key (F5 or prefix+.) opens a small popup over the current pane with what a
right-click menu would hold. Click an entry, or move with the arrows and press Enter; Esc closes.
herdr 0.9.3 builds its own right-click menus from a fixed list, so plugins cannot add entries
there; this popup is the reachable stand-in. Usage: run.cmd panemenu."""
from __future__ import annotations

import os

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from . import herdr

ENTRIES = [
    ("compact", "⇣ Compact this agent (your instructions)"),
    (None, None),
    ("split-right", "◫ Split right"),
    ("split-down", "⊟ Split down"),
    ("zoom", "⛶ Zoom / unzoom"),
    (None, None),
    ("move-left", "← Move pane left"),
    ("move-right", "→ Move pane right"),
    ("move-up", "↑ Move pane up"),
    ("move-down", "↓ Move pane down"),
    ("even", "= Even out all splits"),
    ("newtab", "↦ Move to a new tab"),
    ("arrange", "▦ Arrange panes… (layout map)"),
    (None, None),
    ("new-here", "＋ New agent session here…"),
    ("fold", "▸ Fold / unfold this project"),
    (None, None),
    ("relaunch", "↻ Relaunch all sessions (after an account switch)…"),
]


def pane_info() -> tuple[str, str, str]:
    """(pane id, its workspace, its agent's name or '') for the tiled pane under the popup."""
    pane = os.environ.get("HERDR_ACTIVE_PANE_ID") or os.environ.get("HERDR_PANE_ID") or ""
    try:
        snap = herdr.snapshot()
    except herdr.HerdrError:
        return pane, os.environ.get("HERDR_WORKSPACE_ID", ""), ""
    pane = pane or snap.get("focused_pane_id", "")
    ws = next((p.get("workspace_id", "") for p in snap.get("panes", []) if p.get("pane_id") == pane),
              os.environ.get("HERDR_WORKSPACE_ID", ""))
    agent = next((a for a in snap.get("agents", []) if a.get("pane_id") == pane), None)
    return pane, ws, (agent.get("terminal_title_stripped") or agent.get("agent") or "agent") if agent else ""


class PaneMenu(App):
    CSS = """
    Screen { background: $surface; }
    #head { padding: 0 1; color: $text-muted; }
    OptionList { border: none; height: 1fr; }
    """
    BINDINGS = [Binding("escape", "quit", "close"), Binding("q", "quit", show=False)]

    def __init__(self, pane: str, agent: str):
        super().__init__()
        self.pane, self.agent = pane, agent
        self.choice = ""

    def compose(self) -> ComposeResult:
        yield Static(f"{self.agent or 'pane'}  ·  {self.pane}" if self.pane else "no pane", id="head")
        opts = []
        for op, label in ENTRIES:
            if op is None:
                opts.append(None)
            elif op == "compact" and not self.agent:
                opts.append(Option("⇣ Compact (no agent in this pane)", id=op, disabled=True))
            else:
                opts.append(Option(label, id=op))
        yield OptionList(*opts)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.choice = event.option.id or ""
        self.exit()


def main() -> None:
    pane, ws, agent = pane_info()
    app = PaneMenu(pane, agent)
    app.run()
    if not app.choice or not pane:
        return
    # the popup is gone now: act on the pane that was under it
    os.environ["HERDR_PANE_ID"] = pane
    os.environ["HERDR_WORKSPACE_ID"] = ws
    os.environ.pop("HERDR_TAB_ID", None)
    os.environ.pop("HERDR_PLUGIN_CONTEXT_JSON", None)
    if app.choice == "compact":
        from . import compact
        compact.main()
    elif app.choice == "new-here":
        from . import newhere
        newhere.main()
    elif app.choice == "fold":
        from . import fold
        fold.main()
    elif app.choice == "relaunch":  # the Navigator opens on the relaunch sheet: you confirm there
        from . import accounts
        accounts.offer_relaunch("")
    else:
        from . import paneact
        paneact.main([app.choice])


if __name__ == "__main__":
    main()
