"""herdr Navigator: one clickable, keyboard-driven screen for projects, worktrees, agents,
sub-agents and session resume.

Every row, tab and button is clickable; every action also has a key, and the footer always
shows the keys that work right now (click them too). Click a row once to select it, click it
again (or press Enter) to open it, so a stray click never launches anything. Clicking the
☐/☑ box of a project puts it on (or takes it off) herdr's sidebar.
"""
from __future__ import annotations

import os
import shlex
import shutil
import sys
import time

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, DataTable, Footer, Input, Static, TabbedContent, TabPane

from . import keys as keymap
from . import arrange, attention, history, launch, layouts, model, panes, settings, sidebar, uiwidth
from .model import STATE_ICON, Agent, World, age, summarize
from .settings_ui import SettingsPane
from .startup_ui import ContextMenu, Digest, FinishWorktree, NewSession, Prompt, StartupPane, UsagePane
from . import ui
from .ui import Btn, Field

STATE_STYLE = {"blocked": "bold #fe8019", "reply": "bold #fe8019", "done": "bold #56d364", "working": "dim", "idle": "dim",
               "inactive": "dim italic", "unknown": "dim"}
NEEDS_YOU = model.NEEDS_YOU
CLI_STYLE = {
    "claude": "#d97757", "codex": "#10a37f", "pi": "#7aa2f7", "opencode": "#e5c07b", "kilo": "#f8f675",
    "gemini": "#4796e3", "qwen": "#8b7cf6", "copilot": "#a371f7", "droid": "#ff7b39", "amp": "#f34e3f",
    "cline": "#56b6c2", "cursor": "#c8c8c8", "cursor-agent": "#c8c8c8", "hermes": "#d4a72c",
}


# Order of the "+ New" picker and the CLI filter; anything else follows alphabetically.
CLI_ORDER = ["claude", "codex", "pi", "opencode", "kilo", "gemini", "qwen", "copilot",
             "droid", "amp", "cline", "cursor", "hermes"]
CLI_NEW_KEY = {"claude": "c", "codex": "x", "pi": "e", "opencode": "u", "kilo": "y"}
CLI_SHORT = {"opencode": "opencd", "copilot": "copilt", "cursor-agent": "cursor"}
TAG_WIDTH = 6


def cli_tag(cli: str) -> Text:
    """The source CLI as a coloured fixed-width name, so titles after it line up."""
    name = CLI_SHORT.get(cli, cli or "?")[:TAG_WIDTH]
    return Text(name.ljust(TAG_WIDTH), style="dim")  # one tone: the name says which CLI


def cli_rank(cli: str) -> tuple[int, str]:
    return (CLI_ORDER.index(cli) if cli in CLI_ORDER else len(CLI_ORDER), cli)


def installed_clis() -> list[str]:
    """CLIs whose "new" command is on PATH, in picker order."""
    launch_cfg = settings.load().launch
    out = []
    for key, cmd in launch_cfg.items():
        if key.endswith("_new") and cmd:
            try:
                exe = shlex.split(cmd)[0]
            except ValueError:
                continue
            if shutil.which(exe):
                out.append(key[:-4])
    return sorted(out, key=cli_rank)
TABS = ["projects", "agents", "sessions", "panes", "usage", "settings"]
# older keybindings and entry points; "recent" is Agents sorted by your last visit
TAB_ALIAS = {"startup": "sessions", "resume": "sessions", "recent": "agents"}
# Entry points that open a section of ⚙ Settings (F4 is the key cheat sheet).
SETTINGS_SECTION = {"keys": "keys", "alerts": "alerts", "accounts": "accounts", "logon": "logon",
                    "prompts": "prompts", "general": "general", "updates": "updates"}
WT_SEP = "|wt|"

# "?" shows what the current tab is for and how to work it.
HELP = {
    "projects": ("Projects: where your work lives",
                 "Every project and its worktrees, newest first. ☑ in front = shown in herdr's sidebar.\n\n"
                 "Click a row to select it, click it again or press Enter to go there.\n"
                 "▾/▸ (or ← →) folds a project's worktrees. Type in 🔍 to filter.\n"
                 "+ New agent ▾ starts Claude, Codex… here; its last entry asks for a name, a new worktree, model.\n"
                 "☰ Sessions lists this project's sessions to resume or tick for logon.\n"
                 "⎇ Finish worktree (on a worktree row) checks it is clean, then closes and removes it.\n"
                 "Right-click a row (or press .) for all of this in a menu."),
    "agents": ("Agents: everything running now",
               "Every agent, inside herdr or in another window (↗), nested under its project. ← → or ▾ folds a project\n"
               "(a folded one still shows who needs you). Those that need you come first, longest wait first:\n"
               "⚠ waits for an approval or has a question open · ✔ finished.\n"
               "○ Parked = idle, nothing asked. Waits = how long it has been waiting. ◌ Inactive = parked with nothing new for an hour. ↳N = sub-agents running, ⟳N = background tasks running (shell jobs, monitors, dynamic workflows). Ctx = context in use against where the session auto-compacts (its CLI setting, else the model window) (▰▰▱▱): green, yellow from 200K or 70%, red from 700K or 85% (time to compact).\n\n"
               "⚑ Next (g) selects the next one that needs you and puts you in the message box.\n"
               "Answer: when it shows numbered options (a question or a permission prompt), click one.\n"
               "Enter or a second click jumps to the agent's pane. Tick ☐ several to send to all of them.\n"
               "⇅ Sort: Recent lists the places you visited last, shells included (what F7 opens).\n"
               "Right-click (or .) an agent: relaunch it, reopen it at logon, hand off its answer."),
    "sessions": ("Sessions: resume anything, choose what reopens at logon",
                 "Every past session of every CLI, by project. Search finds any of them.\n\n"
                 "Enter on a session resumes it in its project (a running one: jumps there).\n"
                 "☑ = reopens at logon. Click the box or press Space. Dim ☑ 'auto' = picked automatically\n"
                 "(the newest few per lane); your own clicks are bright and stay.\n"
                 "Project rows: ▸/▾ or ← → folds them, the box switches the whole project on or off at logon.\n"
                 "☑ Ticks ▾: tick what runs now, untick everything, back to automatic.\n"
                 "↻ Relaunch ▾ restarts open sessions in their panes (after signing in to another account).\n"
                 "Right-click (or .) a session: launch options (model, effort, Remote Control), resume command."),
    "panes": ("Layout: arrange the panes of the current tab",
              "The map is to scale. Click a pane to select it, double-click to go there.\n"
              "Drag a pane onto another: the middle swaps them, an edge puts it beside.\n"
              "The shapes on top rearrange the whole tab; ＋ New tab ▾ opens a ready-made layout.\n"
              "Save a layout under a name to restore it for this project later (Projects › ▦ Layout)."),
    "usage": ("Usage: tokens per project and session",
              "Today and the last 7 days, from the Claude and Codex transcripts (cache reads included)."),
    "settings": ("Settings: what you set up once",
                 "Pick a section on the left. Changes save on their own when you leave a field.\n"
                 "Logon restore: what reopens when you log on. Accounts: sign in, switch accounts.\n"
                 "Phone alerts: Telegram, ntfy, webhook, WhatsApp. Prompts: one-click prompts.\n"
                 "General: sidebar and lists. Keys: every key the Navigator and herdr know."),
}


CTX_STYLE = {"ok": "#56d364", "warn": "#fabd2f", "full": "bold #fb4934"}


def ctx_text(a) -> Text:
    """Context gauge: a 4-cell bar of the window and the tokens in use; green while roomy, yellow from
    200K, red from 700K (`[context] warn_at / full_at`): time to /compact."""
    c = getattr(a, "context", None)
    if not c:
        return Text("")
    return Text(f"{c.bar()} {c.short:>5}", style=CTX_STYLE[c.level])


AGENT_FILTERS = (("All", ""), ("⏳ Needs you", "needs"), ("◐ Working", "working"), ("○ Parked", "idle"),
                 ("↗ Elsewhere", "outside"))


def prompt_options(raw: str) -> list[tuple[str, str]]:
    """Numbered choices at the bottom of a pane ("❯ 1. Yes", "2. Yes, don't ask again", "3. No"):
    a permission prompt or a question. Pressing the number picks one."""
    import re
    pat = re.compile(r"^\s*(?:[❯>›→]\s*)?(\d)[.)]\s+(\S.{0,80})$")
    lines = raw.splitlines()[-30:]
    found: list[tuple[str, str]] = []
    for ln in lines:
        m = pat.match(ln)
        if m:
            n, label = m.group(1), m.group(2).strip()
            if n == "1":
                found = []  # a new list starts
            if not found or int(n) == int(found[-1][0]) + 1:
                found.append((n, label))
    return found if len(found) >= 2 else []


def conversation_lines(raw: str) -> list[str]:
    """The agent's own output from a pane read: everything above the CLI's input box (the last
    horizontal rule) and without blank or rule-only lines."""
    lines = raw.splitlines()
    rule = lambda ln: ln.strip().startswith("──") and ln.count("─") >= 20
    for i in range(len(lines) - 1, -1, -1):  # the lowest rule: the input box (and its status lines below)
        if rule(lines[i]):
            lines = lines[:i]
            # a box: a second rule just above, with only the prompt line between them
            box = [j for j in range(len(lines) - 1, max(-1, len(lines) - 5), -1) if rule(lines[j])]
            if box and all(ln.strip().startswith(("❯", ">", "›")) or not ln.strip() for ln in lines[box[0] + 1:]):
                lines = lines[:box[0]]
            break
    return [l for l in lines if l.strip() and not set(l.strip()) <= set("─━═-")]


def NL_JOIN(lines: list[str]) -> str:
    return chr(10).join(lines)


def clip(text: str, n: int) -> str:
    """Cut to n characters and say so."""
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def state_text(status: str) -> Text:
    return Text(f"{STATE_ICON.get(status, '?')} {status}", style=STATE_STYLE.get(status, ""))


class TopBar(Static):
    """Where you are and what needs you, in words. A click goes to the agents behind it."""

    class Clicked(Message):
        pass

    def on_click(self) -> None:
        self.post_message(self.Clicked())


STATE_WORDS = (("blocked", "waiting on you", "bold #fe8019"), ("reply", "need a reply", "bold #fe8019"),
               ("done", "done", "bold"), ("working", "working", "dim"), ("idle", "parked", "dim"),
               ("inactive", "inactive", "dim italic"))


def counts_text(counts) -> Text:
    """'⚠ 1 waiting on you · ◐ 2 working · ○ 9 idle': the glyph plus the word it stands for."""
    out = Text()
    for state, word, style in STATE_WORDS:
        if counts.get(state):
            if out:
                out.append(" · ", style="dim")
            out.append(f"{STATE_ICON[state]} {counts[state]} {word}", style=style)
    return out


class ClickTwiceTable(DataTable):
    """Row table: a click on a new row selects it, a second click opens it.
    A click in the first column posts BoxClicked instead (the sidebar checkbox)."""

    BINDINGS = [Binding("j", "cursor_down", "Down", show=False), Binding("k", "cursor_up", "Up", show=False),
                Binding("space", "box", "Tick"), Binding("period", "menu", "Menu", key_display="."),
                Binding("left", "fold(False)", "Fold", show=False), Binding("right", "fold(True)", "Unfold", show=False),
                Binding("shift+f10", "menu", "Menu", show=False)]

    class Menu(Message):
        def __init__(self, table: "ClickTwiceTable", row_key: str, at) -> None:
            super().__init__()
            self.table, self.row_key, self.at = table, row_key, at

    def action_menu(self) -> None:
        if self.row_count:
            r = self.region
            at = (r.x + 6, r.y + 2 + self.cursor_row - int(self.scroll_y))
            self.post_message(self.Menu(self, self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value, at))

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action in ("box", "menu"):
            return self.id in ("proj-table", "agent-table")
        if action == "fold":
            return self.id in ("proj-table", "agent-table")
        return True

    def action_box(self) -> None:
        if self.row_count:
            self.post_message(self.BoxClicked(self, self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value))

    class Fold(Message):
        def __init__(self, table: "ClickTwiceTable", row_key: str, open_: bool | None = None) -> None:
            super().__init__()
            self.table, self.row_key, self.open_ = table, row_key, open_

    def action_fold(self, open_: bool) -> None:
        if self.row_count:
            self.post_message(self.Fold(self, self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value, open_))

    class BoxClicked(Message):
        def __init__(self, table: "ClickTwiceTable", row_key: str) -> None:
            super().__init__()
            self.table = table
            self.row_key = row_key

    def on_mount(self) -> None:
        self._hl_at = 0.0
        self.cursor_type = "row"
        self.zebra_stripes = True

    def watch_cursor_coordinate(self, old, new) -> None:  # type: ignore[override]
        super().watch_cursor_coordinate(old, new)
        if old.row != new.row:
            self._hl_at = time.monotonic()

    def fresh_highlight(self) -> bool:
        return time.monotonic() - getattr(self, "_hl_at", 0.0) < 0.25

    async def _on_click(self, event: events.Click) -> None:
        meta = event.style.meta if event.style else {}
        if event.button == 3 and meta.get("row", -1) >= 0 and self.id in ("proj-table", "agent-table"):
            self.move_cursor(row=meta["row"])
            rk = self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value
            self.post_message(self.Menu(self, rk, (event.screen_x, event.screen_y)))
            event.stop()
            return
        if meta.get("column") == 0 and meta.get("row", -1) >= 0 and self.id == "proj-table":
            self.move_cursor(row=meta["row"])
            rk = self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value
            self.post_message(self.Fold(self, rk))
            event.stop()
            return
        box_col = 1 if self.id == "proj-table" else 0
        if meta.get("column") == box_col and meta.get("row", -1) >= 0 and self.id in ("proj-table", "agent-table"):
            row = meta["row"]
            self.move_cursor(row=row)
            rk = self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value
            self.post_message(self.BoxClicked(self, rk))
            event.stop()
            return
        await super()._on_click(event)


class ProjTable(ClickTwiceTable):
    BINDINGS = [Binding("space", "box", "Sidebar on/off")]


class AgentTable(ClickTwiceTable):
    BINDINGS = [Binding("space", "box", "Select for Send")]


class LayoutMap(Widget):
    """A to-scale map of the focused tab's panes. Click a pane to select it, double-click to jump
    to it. The selected pane is what the buttons act on."""

    can_focus = True

    class Picked(Message):
        def __init__(self, pane_id: str, double: bool) -> None:
            super().__init__()
            self.pane_id = pane_id
            self.double = double

    class Dropped(Message):
        def __init__(self, src: str, target: str, where: str) -> None:
            super().__init__()
            self.src, self.target, self.where = src, target, where

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.layout_data: panes.TabLayout | None = None
        self.selected: str | None = None
        self._drag: str | None = None
        self._drop: tuple[str, str] | None = None
        self._suppress_click = False

    def _hit(self, x: int, y: int):
        if not self.layout_data:
            return None
        boxes, _, _ = self._scaled()
        for b, x0, y0, x1, y1 in boxes:
            if x0 <= x < x1 and y0 <= y < y1:
                return b, (x - x0) / max(1, x1 - x0), (y - y0) / max(1, y1 - y0)
        return None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        hit = self._hit(event.x, event.y)
        self._drag = hit[0].pane_id if hit else None
        self._drop = None
        if self._drag:
            self.capture_mouse()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._drag:
            return
        hit = self._hit(event.x, event.y)
        drop = (hit[0].pane_id, panes.zone(hit[1], hit[2])) if hit and hit[0].pane_id != self._drag else None
        if drop != self._drop:
            self._drop = drop
            self.refresh()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._drag:
            self.release_mouse()
        if self._drag and self._drop:
            self._suppress_click = True
            self.post_message(self.Dropped(self._drag, self._drop[0], self._drop[1]))
        self._drag, self._drop = None, None
        self.refresh()

    def show(self, layout, selected) -> None:
        self.layout_data, self.selected = layout, selected
        self.refresh()

    def _scaled(self):
        lay = self.layout_data
        W, H = max(self.size.width, 10), max(self.size.height, 5)
        aw, ah = lay.area
        out = []
        for b in lay.panes:
            x0 = round(b.x * W / aw)
            y0 = round(b.y * H / ah)
            x1 = max(x0 + 3, round((b.x + b.w) * W / aw))
            y1 = max(y0 + 3, round((b.y + b.h) * H / ah))
            out.append((b, x0, y0, min(x1, W), min(y1, H)))
        return out, W, H

    def render(self) -> Text:
        if not self.layout_data or not self.layout_data.panes:
            return Text("no layout (is herdr running?)", style="dim")
        boxes, W, H = self._scaled()
        grid = [[" "] * W for _ in range(H)]
        style = [[""] * W for _ in range(H)]
        for b, x0, y0, x1, y1 in boxes:
            sel = b.pane_id == self.selected
            fill = ""
            if self._drop and b.pane_id == self._drop[0]:
                fill = "bold reverse yellow"
            elif self._drag and b.pane_id == self._drag:
                fill = "bold reverse magenta"
            sel = sel or bool(fill)
            for y in range(y0, y1):
                for x in range(x0, x1):
                    edge_x, edge_y = x in (x0, x1 - 1), y in (y0, y1 - 1)
                    ch = " "
                    if edge_x and edge_y:
                        ch = {(x0, y0): "┌", (x1 - 1, y0): "┐", (x0, y1 - 1): "└"}.get((x, y), "┘")
                    elif edge_y:
                        ch = "─"
                    elif edge_x:
                        ch = "│"
                    grid[y][x] = ch
                    if fill:
                        style[y][x] = fill
                    elif sel and (edge_x or edge_y):
                        style[y][x] = "bold"
                    elif edge_x or edge_y:
                        style[y][x] = "bold" if b.focused else "dim"
            inner = max(1, x1 - x0 - 2)
            icon = model.STATE_ICON.get(b.status, "") if b.status else ""
            lines = [f"{icon} {b.label}".strip()[:inner], (b.pane_id + ("  ◀ you" if b.focused else ""))[:inner]]
            if self._drop and b.pane_id == self._drop[0]:
                lines.append(panes.ZONE_LABEL[self._drop[1]][:inner])
            cy = y0 + max(1, (y1 - y0) // 2 - 1)
            for i, line in enumerate(lines):
                yy = cy + i
                if yy >= y1 - 1:
                    break
                xs = x0 + 1 + max(0, (inner - len(line)) // 2)
                for j, ch in enumerate(line):
                    grid[yy][xs + j] = ch
                    style[yy][xs + j] = fill or (("bold" if sel else "bold") if i == 0 else "dim")
        out = Text()
        for y in range(H):
            for x in range(W):
                out.append(grid[y][x], style=style[y][x] or None)
            if y < H - 1:
                out.append("\n")
        return out

    def on_click(self, event: events.Click) -> None:
        if self._suppress_click:
            self._suppress_click = False
            return
        if not self.layout_data:
            return
        boxes, _, _ = self._scaled()
        for b, x0, y0, x1, y1 in boxes:
            if x0 <= event.x < x1 and y0 <= event.y < y1:
                self.post_message(self.Picked(b.pane_id, event.chain >= 2))
                return


class Navigator(App):
    ENABLE_COMMAND_PALETTE = False
    TITLE = "herdr navigator"

    CSS = ui.CSS + """
    Screen { background: $surface; overflow: hidden; }
    Tabs Tab { color: $text 75%; }
    Tabs Tab.-active { color: $text; text-style: bold; }
    #topbar { height: 1; padding: 0 1; background: $boost; }
    #tabs { height: 1fr; }
    TabPane { padding: 0; }
    DataTable { height: 1fr; }
    #proj-detail { width: 42%; min-width: 34; padding: 0 1; border-left: tall $primary 40%; }
    #proj-info { height: auto; text-wrap: nowrap; text-overflow: ellipsis; margin: 0 0 1 0; }
    #proj-items { height: 1fr; }
    #agent-bar .grow { width: 1fr; }
    #answer-row { display: none; margin: 0; }
    #answer-row.show { display: block; }
    .key-lbl { width: auto; color: $text-muted; padding: 0 1 0 0; }
    .empty-note { height: auto; padding: 1 2; color: $text-muted; display: none; }
    .empty-note.show { display: block; }
    #proj-filter { width: 1fr; min-width: 12; margin: 0 0 0 1; }
    #agent-detail { height: 40%; min-height: 9; max-height: 16; border-top: tall $primary 40%; padding: 0 0 0 0; }
    #agent-preview { height: 1fr; padding: 0 1; }
    #agent-send { margin: 0; }
    #agent-keys { margin: 0; }
    #agent-msg { width: 1fr; margin: 0 1 0 0; }
    #pane-map { width: 1fr; height: 1fr; min-height: 8; }
    #pane-tools { width: 26; padding: 0 1; }
    #pane-tools Button { width: 100%; margin: 0 0 0 0; }
    #pane-tools .bar { padding: 0; margin: 0; }
    #pane-tools .bar Button { width: 1fr; min-width: 3; margin: 0 0 0 1; }
    #pane-tools .bar Static { width: 5; color: $text-muted; }
    #pane-tools .gap { height: 1; }
    #pane-selected { height: 3; }
    #pane-rename { display: none; margin: 1 0 0 0; }
    #pane-rename.show { display: block; }
    #saved-row { margin: 1 0 0 0; padding: 0; }
    #layout-name { width: 1fr; margin: 0 1 0 0; }
    #saved-table { height: 5; }
    #saved-table.empty { display: none; }
    /* small windows: give the content the room labels need, so nothing is cut off */
    App.-narrow #set-nav { width: 15; }
    App.-narrow #set-body { padding: 0 1; }
    App.-narrow .form-row .lbl { width: 14; }
    App.-narrow .key-lbl { display: none; }
    App.-narrow #proj-detail { min-width: 28; }
    App.-narrow #btn-layout, App.-narrow #btn-resume { display: none; }
    """

    BINDINGS = [
        Binding("1", "tab('projects')", "Projects", show=False),
        Binding("2", "tab('agents')", "Agents", show=False),
        Binding("3", "tab('sessions')", "Sessions", show=False),
        Binding("4", "tab('panes')", "Layout", show=False),
        Binding("5", "tab('usage')", "Usage", show=False),
        Binding("6", "tab('settings')", "Settings", show=False),
        Binding("enter", "open", "Go", priority=False, key_display="⏎"),
        Binding("space", "toggle_sidebar", "Sidebar"),
        Binding("r", "resume_project", "Sessions"),
        Binding("slash", "search", "Search", key_display="/"),
        Binding("m", "message", "Message"),
        Binding("g", "attention", "Next waiting"),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("escape", "back", "Close"),
        # everything below works but stays out of the footer (buttons cover it)
        Binding("c", "new('claude')", "New Claude", show=False),
        Binding("N", "new_dialog", "New session…", show=False),
        Binding("x", "new('codex')", "New Codex", show=False),
        Binding("e", "new('pi')", "New Pi", show=False),
        Binding("u", "new('opencode')", "New OpenCode", show=False),
        Binding("y", "new('kilo')", "New Kilo", show=False),
        Binding("l", "restore_layout", "Restore layout", show=False),
        Binding("p", "toggle_project", "This project/all", show=False),
        Binding("b", "filter('needs')", "Needs you", show=False),
        Binding("w", "filter('working')", "Working", show=False),
        Binding("d", "filter('needs')", "Needs you", show=False),
        Binding("i", "filter('idle')", "Parked", show=False),
        Binding("a", "filter('')", "All", show=False),
        Binding("o", "filter('outside')", "Elsewhere", show=False),
        Binding("less_than_sign", "sidebar_width('-6')", "Sidebar narrower", show=False),
        Binding("greater_than_sign", "sidebar_width('+6')", "Sidebar wider", show=False),
        Binding("left", "pane_sel('left')", "", show=False),
        Binding("right", "pane_sel('right')", "", show=False),
        Binding("up", "pane_sel('up')", "", show=False),
        Binding("down", "pane_sel('down')", "", show=False),
        Binding("shift+left", "pane_op('swap','left')", "", show=False),
        Binding("shift+right", "pane_op('swap','right')", "", show=False),
        Binding("shift+up", "pane_op('swap','up')", "", show=False),
        Binding("shift+down", "pane_op('swap','down')", "", show=False),
        Binding("ctrl+left", "pane_op('resize','left')", "", show=False),
        Binding("ctrl+right", "pane_op('resize','right')", "", show=False),
        Binding("ctrl+up", "pane_op('resize','up')", "", show=False),
        Binding("ctrl+down", "pane_op('resize','down')", "", show=False),
        Binding("v", "pane_op('split','right')", "Split →", show=False),
        Binding("s", "pane_op('split','down')", "Split ↓", show=False),
        Binding("z", "pane_op('zoom')", "Zoom", show=False),
        Binding("equals_sign", "pane_op('equalize')", "Even out", show=False),
        Binding("t", "pane_op('newtab')", "To new tab", show=False),
        Binding("n", "pane_rename", "Rename", show=False),
        Binding("delete", "pane_op('close')", "Close pane", show=False),
        Binding("f5", "refresh", "Refresh"),
        Binding("q", "quit", "Close", show=False),
    ]

    def __init__(self, start_tab: str = "projects") -> None:
        super().__init__()
        self.search_first = start_tab == "resume"  # F2: straight into the session search
        self.agent_sort = "recent" if start_tab == "recent" else "needs"  # F7: where you were last
        self.answer_to: tuple[str, str] | None = None  # (pane, name) the Answer buttons type into
        self.settings_section = SETTINGS_SECTION.get(start_tab, "logon")
        if start_tab in SETTINGS_SECTION:
            start_tab = "settings"
        start_tab = TAB_ALIAS.get(start_tab, start_tab)
        self.start_tab = start_tab if start_tab in TABS else "projects"
        from . import startup
        place = startup.ui_state().get("place") or {}
        self._place = place if time.time() - place.get("at", 0) < 8 * 3600 else {}
        if start_tab == "projects" and self._place.get("tab") in TABS:  # F1: back where you were
            self.start_tab = self._place["tab"]
        self.world: World | None = None
        # the world loads while the screen is still being built (they take about as long)
        from concurrent.futures import ThreadPoolExecutor
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="nav-prefetch")
        self._prefetch = self._pool.submit(model.build)
        self._pool.shutdown(wait=False)
        self.current_project = None          # project of the focused workspace
        self.agent_filter = ""
        self.selected_key: str | None = self._place.get("project")  # project root, or root|wt|label
        self.agent_rows: dict[str, tuple[Agent, int]] = {}
        self.pane_layout = None
        self.pane_selected: str | None = None
        self.close_armed = ""
        self.marked: set[str] = set()          # Agents: panes picked for "send to many"
        from . import digest
        self.seen_before = digest.last_seen()  # for "while you were away"
        self.digest_checked = False

    # ---- layout ---------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield TopBar("", id="topbar")
        with TabbedContent(initial=self.start_tab, id="tabs"):
            with TabPane("1 Projects", id="projects"):
                with Horizontal(classes="bar"):
                    yield Btn("▶ Open", id="btn-go", variant="primary", tooltip="Go to this project (Enter)")
                    yield Btn("+ New agent ▾", id="btn-new", tooltip="Start an agent here: pick a CLI, or set it up")
                    yield Btn("☰ Sessions", id="btn-resume", tooltip="Its sessions: resume, tick for logon (r)")
                    yield Btn("☑ Sidebar", id="btn-sidebar", tooltip="Show or hide it in herdr's sidebar (Space)")
                    yield Btn("▦ Restore layout", id="btn-layout", tooltip="Restore its newest saved layout (l)")
                    yield Btn("⎇ Finish worktree", id="btn-wt-finish",
                              tooltip="Put this worktree away: check it, close it, remove the checkout")
                    yield Field(placeholder="🔍 filter  ( / )", id="proj-filter")
                with Horizontal():
                    with Vertical():
                        yield Static("", id="proj-empty", classes="empty-note")
                        yield ProjTable(id="proj-table")
                    with Vertical(id="proj-detail"):
                        yield Static(id="proj-info")
                        yield ClickTwiceTable(id="proj-items", show_header=False)
            with TabPane("2 Agents", id="agents"):
                with Horizontal(id="agent-bar", classes="bar"):
                    for label, f in AGENT_FILTERS:
                        yield Btn(label, id=f"flt-{f or 'all'}")
                    yield Static("", classes="grow")
                    yield Btn("⇅ Running", id="agent-sort", tooltip="Sort: running, then who needs you, then most recent; or where you were last")
                    yield Btn("↻ Relaunch…", id="btn-relaunch-all",
                              tooltip="After switching account: restart every open session in its pane, so it runs "
                                      "under the account you are signed in with now (Remote Control included)")
                    yield Btn("⚑ Next", id="btn-attention", variant="warning",
                              tooltip="Select the next agent that needs you and type your answer (g)")
                yield Static("", id="agent-empty", classes="empty-note")
                yield AgentTable(id="agent-table")
                with Vertical(id="agent-detail"):
                    yield VerticalScroll(Static(id="agent-preview"))
                    with Horizontal(id="answer-row", classes="bar"):
                        yield Static("Answer:", classes="key-lbl")
                        for i in range(1, 7):
                            yield Btn(str(i), id=f"ans-{i}", variant="warning")
                    with Horizontal(id="agent-send", classes="bar"):
                        yield Field(placeholder="✉ Message to the selected agent, or to every ☑ ticked one  (m)",
                                    id="agent-msg")
                        yield Btn("Send", id="send-msg", variant="primary",
                                  tooltip="Send to the selected agent, or to every ☑ ticked one")
                        yield Btn("✕ Untick all", id="mark-clear", tooltip="Clear the ticks")
                    with Horizontal(id="agent-keys", classes="bar"):
                        yield Static("In the agent:", classes="key-lbl")
                        yield Btn("⏎ Enter", id="key-enter", tooltip="Press Enter in the agent (accept)")
                        yield Btn("Esc", id="key-esc", tooltip="Press Esc in the agent (cancel)")
                        yield Btn("^C Stop", id="key-ctrl_c", variant="error", tooltip="Interrupt the agent")
                        yield Btn("⇣ Compact", id="act-compact",
                                  tooltip="/compact with your instructions (⚙ Settings › Prompts): free up its context")
                        yield Btn("☰ Prompts ▾", id="act-prompts", tooltip="Saved prompts: send one in a click")
                        yield Btn("⇢ Hand off ▾", id="act-handoff", tooltip="Send its last answer to another agent")
            with TabPane("3 Sessions", id="sessions"):
                yield StartupPane(id="startup-pane")
            with TabPane("4 Layout", id="panes"):
                with Horizontal(id="shape-bar", classes="bar"):
                    yield Static("Arrange this tab:", classes="key-lbl")
                    for kind, label in arrange.SHAPES.items():
                        yield Btn(label, id=f"shape-{kind}", tooltip=f"Arrange this tab's panes: {label[2:].lower()}")
                    yield Btn("＋ New tab ▾", id="presets-toggle", tooltip="Open a new tab with a ready-made layout")
                with Horizontal():
                    with Vertical():
                        yield LayoutMap(id="pane-map")
                        yield Field(placeholder="New pane name, Enter saves (empty clears)", id="pane-rename")
                        with Horizontal(id="saved-row", classes="bar"):
                            yield Field(placeholder="Save this tab's layout as…", id="layout-name")
                            yield Btn("💾 Save", id="layout-save", tooltip="Save this tab's layout for the project")
                            yield Btn("▦ Restore", id="layout-restore", tooltip="Restore the saved layout selected below")
                            yield Btn("🗑 Delete", id="layout-delete", tooltip="Delete the selected saved layout")
                        yield ClickTwiceTable(id="saved-table")
                    with VerticalScroll(id="pane-tools"):
                        yield Static("", id="pane-selected")
                        yield Btn("⇥ Go to pane      ⏎", id="pop-focus", variant="primary")
                        yield Static("", classes="gap")
                        yield Btn("◫ Split right     v", id="pop-split-right")
                        yield Btn("⊟ Split down      s", id="pop-split-down")
                        yield Btn("⛶ Zoom            z", id="pop-zoom")
                        yield Btn("= Even out        =", id="pop-equalize")
                        yield Static("", classes="gap")
                        with Horizontal(classes="bar"):
                            yield Static("Swap")
                            for d, a in (("left", "←"), ("up", "↑"), ("down", "↓"), ("right", "→")):
                                yield Btn(a, id=f"pop-swap-{d}", tooltip=f"Swap with the pane {d} (Shift+arrow)")
                        with Horizontal(classes="bar"):
                            yield Static("Size")
                            for d, a in (("left", "◂"), ("up", "▴"), ("down", "▾"), ("right", "▸")):
                                yield Btn(a, id=f"pop-resize-{d}", tooltip=f"Resize {d} (Ctrl+arrow)")
                        yield Static("", classes="gap")
                        yield Static("⇧+arrows swap · Ctrl+arrows size", classes="hint")
                        yield Btn("↦ To new tab      t", id="pop-newtab")
                        yield Btn("✎ Rename          n", id="pop-rename")
                        yield Btn("🚨 Watch for errors", id="pane-watch",
                                  tooltip="Tell me (toast + phone) when this pane prints FAILED, a traceback, Error: …")
                        yield Btn("✕ Close pane    Del", id="pop-close", variant="error")
            # Usage and Settings are built the first time they are opened (ensure_tab): together
            # ~130 widgets that every popup would otherwise compose and style before its first frame
            yield TabPane("5 Usage", id="usage")
            yield TabPane("6 ⚙ Settings", id="settings")
        yield Footer(compact=True)

    async def on_mount(self) -> None:
        await self.ensure_tab(self.start_tab)  # opened straight on Usage or Settings
        self.query_one("#proj-table", DataTable).add_columns("", "Side", "Project", "Agents", "Last")
        self.query_one("#agent-table", DataTable).add_columns("Send", "State", "CLI", "Agent", "Waits", "Ctx", "Project",
                                                              "Doing")
        self.query_one("#saved-table", DataTable).add_columns("Saved layout", "Panes", "Saved")
        self.render_keys()
        self.load_world()
        self.focus_table()
        self.set_interval(5, self.auto_refresh)
        self.ensure_daemon()

    def on_resize(self, ev) -> None:
        narrow = ev.size.width < 100
        if narrow != self.has_class("-narrow"):
            self.set_class(narrow, "-narrow")
            if self.world:
                self.fill_agents()  # shorter filter labels

    @work(thread=True, group="daemon")
    def ensure_daemon(self) -> None:
        from . import daemon
        daemon.start()  # no-op when it runs

    def auto_refresh(self) -> None:
        """Keep Projects and Agents live while the Navigator is open (only when something changed)."""
        if self.world is None or len(self.screen_stack) > 1 or self.active_tab() not in ("projects", "agents"):
            return
        self.load_live()

    @work(thread=True, exclusive=True, group="live")
    def load_live(self) -> None:
        world = model.build()
        self.call_from_thread(self.apply_live, world)

    @staticmethod
    def _live_sig(world: World) -> tuple:
        return tuple((a.key, a.status, a.activity, a.title, a.context.pct if a.context else -1,
                      len(a.subagents), a.workflows, a.jobs, a.waiting_since, bool(a.question)) for a in world.agents)

    def apply_live(self, world: World) -> None:
        if self.world is None or self._live_sig(world) == self._live_sig(self.world):
            return
        self.world = world
        self.render_topbar(world)
        self.fill_agents()
        if self.active_tab() == "projects":
            self.fill_projects()

    # ---- data -----------------------------------------------------------------------------
    @work(thread=True, exclusive=True)
    def load_world(self) -> None:
        pre, self._prefetch = getattr(self, "_prefetch", None), None
        try:
            world = pre.result(timeout=30) if pre else model.build()
        except Exception:
            world = model.build()
        self.call_from_thread(self.apply_world, world)

    def apply_world(self, world: World) -> None:
        self.world = world
        self.current_project = world.project_of_workspace(world.focused_workspace)
        if self.selected_key is None and self.current_project:
            self.selected_key = self.current_project.root
        self.render_topbar(world)
        self.fill_projects()
        self.fill_agents()
        pane = self.query_one(StartupPane)
        pane.show(world)
        if self.active_tab() == "usage" and self.query(UsagePane):
            self.query_one(UsagePane).load(world)
        nav_pane = os.environ.pop("NAV_PANE", None)  # right-click › Arrange panes: this pane selected
        if nav_pane:
            self.pane_selected = nav_pane
        relaunch = os.environ.pop("NAV_RELAUNCH", None)  # after a sign-in: offer the relaunch once
        if relaunch is not None:
            self.open_relaunch(relaunch)
            self.digest_checked = True
        new_ws = os.environ.pop("NAV_NEW", None)  # herdr workspace menu › New agent here
        if new_ws:
            p = world.project_of_workspace(new_ws)
            if p:
                self.new_only = True
                self.digest_checked = True
                self.new_agent_in(p.root + (WT_SEP + p.worktree if p.worktree else ""))
            else:
                self.notify("That workspace is not inside a project folder.", severity="warning")
        self.show_digest(world)
        self.set_hint()
        self.load_panes()
        self.fill_saved()

    def render_topbar(self, world: World) -> None:
        total = model.Counter(a.status for a in world.agents)
        outside = sum(1 for a in world.agents if not a.in_herdr)
        subs = sum(1 for a in world.agents for s in a.subagents if not s.workflow)
        where = self.current_project.label if self.current_project else "—"
        bar = Text.assemble(("▣ ", "bold"), (where, "bold"), ("   │   ", "dim"))
        bar.append_text(counts_text(total) or Text("no agents running", style="dim"))
        if outside:
            bar.append(f" · ↗ {outside} in other windows", style="dim")
        if subs:
            bar.append(f" · ↳ {subs} sub-agents", style="dim")
        full = [a for a in world.agents if a.context and a.context.level == "full"]
        warn = [a for a in world.agents if a.context and a.context.level == "warn"]
        if full:
            bar.append("   │   ", style="dim")
            bar.append(f"▲ context full: {', '.join(a.display[:18] for a in full[:2])}"
                       + (f" +{len(full) - 2}" if len(full) > 2 else ""), style=CTX_STYLE["full"])
        if warn:
            bar.append("   │   ", style="dim")
            bar.append(f"▰▰▱▱ {len(warn)} filling up", style=CTX_STYLE["warn"])
        if world.error:
            bar.append(f"    herdr unreachable", style="red")
        self.query_one("#topbar", Static).update(bar)

    # ---- panes ----------------------------------------------------------------------------
    @work(thread=True, exclusive=True, group="panes")
    def load_panes(self) -> None:
        names = {}
        for a in (self.world.agents if self.world else []):
            if a.pane_id:
                names[a.pane_id] = a.display
            if a.mirror_pane:
                names[a.mirror_pane] = "↗ " + a.display
        try:
            snap = model.herdr.snapshot()
            self._pane_info = {p["pane_id"]: p for p in snap.get("panes", [])}
            lay = panes.current(snap, names=names)
        except Exception:
            lay = None
        if self.agent_sort == "recent" and self.world:
            self.call_from_thread(self.fill_agents)
        self.call_from_thread(self.show_panes, lay)

    def show_panes(self, lay) -> None:
        self.pane_layout = lay
        ids = [b.pane_id for b in lay.panes] if lay else []
        if self.pane_selected not in ids:
            self.pane_selected = next((b.pane_id for b in lay.panes if b.focused), ids[0] if ids else None) \
                if lay else None
        self.query_one("#pane-map", LayoutMap).show(lay, self.pane_selected)
        b = next((b for b in lay.panes if b.pane_id == self.pane_selected), None) if lay else None
        info = Text()
        if b:
            info.append(f"{b.label[:26]}\n", style="bold")
            info.append(b.pane_id + ("  ⛶" if lay.zoomed else ""), style="dim")
        self.query_one("#pane-selected", Static).update(info)
        from . import watch
        on = bool(b and watch.for_pane(b.pane_id, watch.load()))
        btn = self.query_one("#pane-watch", Button)
        btn.label = "✕ Stop error watch" if on else "🚨 Watch for errors"
        btn.variant = "warning" if on else "default"

    @on(LayoutMap.Picked)
    def _picked(self, ev) -> None:
        self.pane_selected = ev.pane_id
        self.show_panes(self.pane_layout)
        if ev.double:
            self.action_pane_op("focus")

    def action_pane_sel(self, direction: str) -> None:
        if self.pane_layout and self.pane_selected:
            n = panes.neighbor(self.pane_layout, self.pane_selected, direction)
            if n:
                self.pane_selected = n
                self.show_panes(self.pane_layout)

    def action_pane_rename(self) -> None:
        inp = self.query_one("#pane-rename", Input)
        inp.add_class("show")
        inp.value = ""
        inp.focus()

    @on(Input.Submitted, "#pane-rename")
    def _rename_done(self, ev: Input.Submitted) -> None:
        ev.input.remove_class("show")
        self.run_pane_op("rename", ev.value.strip())
        self.query_one("#pane-map", LayoutMap).focus()

    def action_pane_op(self, op: str, arg: str = "") -> None:
        if not self.pane_selected:
            return
        if op == "close" and self.close_armed != self.pane_selected:
            self.close_armed = self.pane_selected
            self.notify(f"Close {self.pane_selected}? Its process ends. Press Del / ✕ again to confirm.",
                        severity="warning", timeout=5)
            return
        self.close_armed = ""
        self.run_pane_op(op, arg)

    @work(thread=True, exclusive=True, group="pane-op")
    def run_pane_op(self, op: str, arg: str = "") -> None:
        p = self.pane_selected
        lay = self.pane_layout
        try:
            msg = {
                "focus": lambda: panes.focus(p),
                "split": lambda: panes.split(p, arg),
                "zoom": lambda: panes.zoom(p),
                "swap": lambda: panes.swap(p, arg),
                "resize": lambda: panes.resize(p, arg),
                "equalize": lambda: panes.equalize(lay.tab_id),
                "newtab": lambda: panes.to_new_tab(p),
                "newws": lambda: panes.to_new_workspace(p),
                "rename": lambda: panes.rename(p, arg),
                "close": lambda: panes.close(p),
                "preset": lambda: panes.preset(arg, lay.workspace_id, self._pane_cwd(p)),
            }[op]()
        except Exception as e:
            self.call_from_thread(self.notify, str(e)[:300], title="herdr refused", severity="error")
            return
        if op == "focus":
            self.call_from_thread(self.exit, msg)  # a jump: the Navigator would cover the pane
            return
        if op == "split":
            self.pane_selected = None  # follow the new, focused pane
        self.call_from_thread(self.notify, msg, timeout=2)
        self.load_panes()

    # ---- drag and drop / saved layouts / sidebar width ------------------------------------
    @on(LayoutMap.Dropped)
    def _dropped(self, ev) -> None:
        self.pane_selected = ev.src
        self.run_drop(ev.src, ev.target, ev.where)

    @work(thread=True, exclusive=True, group="pane-op")
    def run_drop(self, src: str, target: str, where: str) -> None:
        try:
            msg = panes.drop(src, target, where, self.pane_layout.tab_id)
        except Exception as e:
            self.call_from_thread(self.notify, str(e)[:300], title="herdr refused", severity="error")
            return
        self.call_from_thread(self.notify, msg, timeout=3)
        self.load_panes()

    def layout_project(self):
        """The project the Panes tab saves to: the project of the tab being shown."""
        if not self.world or not self.pane_layout:
            return None
        p = self.world.project_of_workspace(self.pane_layout.workspace_id)
        return model.projects.Project(p.root, p.name, "", p.path) if p else None

    def fill_saved(self) -> None:
        t = self.query_one("#saved-table", DataTable)
        t.clear()
        t.set_class(True, "empty")
        p = self.layout_project() or (self.selected()[0].project if self.selected()[0] else None)
        if not p:
            return
        for name, spec in sorted(layouts.saved(p).items(), key=lambda kv: -kv[1].get("saved_at", 0)):
            leaves = layouts._leaves(spec["root"])
            t.add_row(Text(name, style="bold"), str(len(leaves)), age(spec.get("saved_at", 0)), key=name)
            t.set_class(False, "empty")

    def saved_selected(self) -> str | None:
        t = self.query_one("#saved-table", DataTable)
        if not t.row_count:
            return None
        return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value

    @on(Input.Submitted, "#layout-name")
    def _layout_name(self, ev: Input.Submitted) -> None:
        self.layout_op("save", ev.value.strip() or "default")
        ev.input.value = ""

    @on(DataTable.RowSelected, "#saved-table")
    def _saved_sel(self, ev: DataTable.RowSelected) -> None:
        if not ev.data_table.fresh_highlight():
            self.layout_op("restore", self.saved_selected())

    @work(thread=True, exclusive=True, group="layout-op")
    def layout_op(self, op: str, name: str | None) -> None:
        p = self.layout_project()
        if op == "restore_project":
            v, _ = self.selected()
            p = v.project if v else None
            data = layouts.saved(p) if p else {}
            name = max(data, key=lambda k: data[k].get("saved_at", 0)) if data else None
            op = "restore"
        if not p or not name:
            self.call_from_thread(self.notify, "Nothing to do: pick a layout (or save one in tab 5 first).",
                                  severity="warning")
            return
        try:
            msg = {"save": lambda: layouts.save(self.world, p, self.pane_layout.tab_id, name),
                   "restore": lambda: layouts.restore(self.world, p, name),
                   "delete": lambda: layouts.delete(p, name)}[op]()
        except Exception as e:
            self.call_from_thread(self.notify, str(e)[:300], title="herdr refused", severity="error")
            return
        if op == "restore" and not msg.startswith("✗"):
            self.call_from_thread(self.notify, msg, timeout=5)
            self.load_panes()
            return
        self.call_from_thread(self.notify, msg, timeout=4)
        self.call_from_thread(self.fill_saved)

    def action_restore_layout(self) -> None:
        self.layout_op("restore_project", None)

    def action_sidebar_width(self, delta: str) -> None:
        self.run_sidebar_width(delta)

    @work(thread=True, exclusive=True, group="sbw")
    def run_sidebar_width(self, delta: str) -> None:
        try:
            w = uiwidth.set_width(delta)
        except Exception as e:
            self.call_from_thread(self.notify, str(e)[:300], title="herdr refused", severity="error")
            return
        self.call_from_thread(self.notify, f"herdr sidebar width: {w} columns", timeout=2)

    # ---- agents: preview, message, keys ------------------------------------------------------
    def agent_selected(self):
        t = self.query_one("#agent-table", DataTable)
        if not t.row_count:
            return None, -1
        k = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        return self.agent_rows.get(k, (None, -1))

    @on(DataTable.RowHighlighted, "#agent-table")
    def _agent_hl(self) -> None:
        self.load_preview()

    @work(thread=True, exclusive=True, group="preview")
    def load_preview(self) -> None:
        a, sub = self.agent_selected()
        out = Text()
        if a is None:
            self.call_from_thread(self.query_one("#agent-preview", Static).update, out)
            self.call_from_thread(self.show_answers, None, [])
            return
        opts: list[tuple[str, str]] = []
        if a.question and sub < 0:
            opts = [(str(i), o) for i, o in enumerate(a.question.options[:6], 1)]
        word = {"blocked": "waiting on you", "reply": "needs a reply", "done": "finished",
                "idle": "parked", "inactive": "inactive (nothing new for an hour)"}.get(a.status, a.status)
        head = f"{a.display}  ·  {a.cli}  ·  {word}  ·  {a.project.label}"
        if a.context:
            head += f"  ·  context {a.context.short}" + (
                f" of {a.context.window // 1000}K ({a.context.pct}%)" if a.context.window else "")
        out.append(head + "\n", style="bold")
        if a.in_herdr and sub < 0:
            from . import watch
            for _, w in watch.for_pane(a.pane_id, watch.load()):
                out.append(watch.describe(w) + "  (right-click to stop)\n", style="dim")
        if a.question and sub < 0:
            q = a.question
            out.append(f"❓ {q.text}\n", style="bold #fe8019")
            for i, o in enumerate(q.options, 1):
                out.append(f"   {i}. {o}\n", style="red")
            if q.more:
                out.append(f"   (+{q.more} more)\n", style="dim")
            out.append("Click an answer below, or Enter to jump to its pane.\n\n", style="dim")
        if sub >= 0 and sub < len(a.subagents):
            sa = a.subagents[sub]
            out.append(f"↳ sub-agent {sa.name} ({sa.kind}, {sa.model})\n{sa.description}\n{sa.activity}\n")
        elif a.in_herdr:
            try:
                raw = model.herdr.run("agent", "read", a.pane_id, "--source", "recent-unwrapped",
                                      "--lines", "40").get("raw", "")
            except Exception as e:
                raw = f"(could not read: {e})"
            if not opts and a.status == "blocked":
                opts = prompt_options(raw)
            out.append(NL_JOIN(conversation_lines(raw)[-12:]))
        else:
            from . import mirror
            s = next((s for s in self.world.sessions if s.id == a.session_id), None)
            path = s.path if s else ""
            if not path and a.session_id:
                hits = list(model.live._claude_root().glob(f"*/{a.session_id}.jsonl"))
                path = str(hits[0]) if hits else ""
            for line in (mirror.feed(path, 10) if path else []):
                out.append_text(line)
                out.append("\n")
            out.append("↗ runs in another window: type there. The mirror resumes it here once it exits.",
                       style="dim")
            opts = []
        self.call_from_thread(self.query_one("#agent-preview", Static).update, out)
        self.call_from_thread(self.show_answers, a if sub < 0 else None, opts)

    def show_answers(self, a, opts: list[tuple[str, str]]) -> None:
        """One button per numbered choice the agent offers; a click presses that number in it."""
        row = self.query_one("#answer-row")
        row.set_class(bool(a and opts and a.in_herdr), "show")
        self.answer_to = (a.pane_id, a.display) if a else None
        for i in range(1, 7):
            b = self.query_one(f"#ans-{i}", Button)
            hit = next((label for n, label in opts if n == str(i)), None)
            b.display = hit is not None
            if hit is not None:
                b.label = f"{i} {clip(hit, 26)}"

    def action_message(self) -> None:
        self.query_one("#agent-msg", Input).focus()

    @on(Input.Submitted, "#agent-msg")
    def _msg(self, ev: Input.Submitted) -> None:
        self.send_to_agent(ev.value)
        ev.input.value = ""

    def send_to_agent(self, text: str = "", key: str = "") -> None:
        if self.marked and self.world:
            for a in self.world.agents:
                if a.pane_id in self.marked:
                    self.run_send(a.pane_id, a.display, text, key)
            return
        a, _ = self.agent_selected()
        if a is None:
            self.notify("Select an agent first: the highlighted row is a project heading.", timeout=4)
            return
        if not a.in_herdr:
            self.notify(f"'{a.display}' runs in another terminal window, so herdr can't type into it. "
                        "Use that window, or resume it here after it exits.", severity="warning", timeout=8)
            return
        self.run_send(a.pane_id, a.display, text, key)

    def send_compact(self) -> None:
        """⇣ Compact: /compact plus your instructions, per agent (each CLI gets what it accepts)."""
        from . import compact
        targets = [a for a in (self.world.agents if self.world else []) if a.pane_id in self.marked]
        if not targets:
            a, _ = self.agent_selected()
            targets = [a] if a else []
        if not targets:
            self.notify("Select an agent first: the highlighted row is a project heading.", timeout=4)
        for a in targets:
            if a.in_herdr:
                self.run_send(a.pane_id, a.display, compact.command(a.cli), "")
            else:
                self.notify(f"'{a.display}' runs in another terminal window: compact it there.", timeout=6)

    # ---- saved prompts and hand-off -----------------------------------------------------------
    def _prompts(self) -> dict[str, str]:
        from . import prompts
        return prompts.all_()

    def _menu_at(self, widget) -> tuple[int, int]:
        """Above a button at the bottom of the screen."""
        r = widget.region
        return (r.x, max(0, r.y - 12))

    def _menu_below(self, widget) -> tuple[int, int]:
        r = widget.region
        return (r.x, r.y + 1)

    def new_menu(self, widget) -> None:
        """+ New agent ▾: one line per installed CLI, then the full dialog."""
        v, wt = self.selected()
        where = (v.project.name + (f" ⎇ {wt.label}" if wt else "")) if v else "here"
        items = [("-head", f"New agent in {where[:30]}")]
        for cli in installed_clis():
            key = CLI_NEW_KEY.get(cli)
            items.append((f"cli:{cli}", f"  {cli:<10}" + (f"  {key}" if key else "")))
        items.append(("dialog", "  … More options: name, worktree, model   N"))
        self.push_screen(ContextMenu(items, self._menu_below(widget)),
                         lambda c: c and c[0] != "-" and (self.action_new_dialog() if c == "dialog"
                                                          else self.action_new(c[4:])))

    def prompts_menu(self, widget) -> None:
        items = [(f"p:{name}", f"{name}") for name in self._prompts()]
        msg = self.query_one("#agent-msg", Input).value.strip()
        if msg:
            items.append(("save", "＋ Save the message as a prompt"))
        items.append(("edit", "✎ Where to edit them"))
        self.push_screen(ContextMenu(items, self._menu_at(widget)), self._prompt_chosen)

    def _prompt_chosen(self, choice: str | None) -> None:
        from . import prompts
        if not choice:
            return
        if choice.startswith("p:"):
            self.send_to_agent(self._prompts().get(choice[2:], ""))
        elif choice == "save":
            text = self.query_one("#agent-msg", Input).value.strip()
            self.push_screen(Prompt("Name for this prompt", text[:50]),
                             lambda name: name and (prompts.save(name, text), self.notify(f"Saved prompt '{name}'")))
        elif choice == "edit":
            self.notify(f"[prompts] in {settings.settings_path()}  ·  your saved ones: {prompts.path()}",
                        title="Prompts", timeout=12)

    def handoff_menu(self, widget) -> None:
        src, sub = self.agent_selected()
        if not src or sub >= 0:
            self.notify("Select the agent whose answer you want to hand off.", severity="warning")
            return
        targets = [a for a in (self.world.agents if self.world else []) if a.in_herdr and a.pane_id != src.pane_id]
        if not targets:
            self.notify("No other agent in herdr to hand it to.", severity="warning")
            return
        items = [(f"t:{a.pane_id}", f"{a.cli:<6} {a.display[:34]}") for a in targets]
        self.push_screen(ContextMenu(items, self._menu_at(widget)), lambda c: c and self.run_handoff(src, c[2:]))

    @work(thread=True, group="send")
    def run_handoff(self, src, target_pane: str) -> None:
        from . import insight
        answer = insight.last_answer(src.cli, src.transcript)
        if not answer:
            self.call_from_thread(self.notify, f"No answer found for {src.display}.", severity="warning")
            return
        tpl = settings.load().handoff
        text = tpl.replace("{name}", src.display).replace("{cli}", src.cli) \
            .replace("{project}", src.project.label).replace("{answer}", answer)
        target = next((a for a in self.world.agents if a.pane_id == target_pane), None)
        self.run_send(target_pane, target.display if target else target_pane, text, "")

    @work(thread=True, group="send")
    def run_send(self, pane: str, name: str, text: str, key: str) -> None:
        try:
            if key:
                model.herdr.run("agent", "send-keys", pane, key)
                msg = f"sent {key} to {name}"
            elif text.strip():
                model.herdr.run("agent", "prompt", pane, text)
                msg = f"✉ sent to {name}"
            else:
                return
        except Exception as e:
            err = str(e)
            if "agent_blocked" in err:
                err = f"{name} is waiting on a question or approval: answer it with ⏎ Enter / Esc, or jump there."
            self.call_from_thread(self.notify, err[:300], title="not sent", severity="warning", timeout=8)
            return
        self.call_from_thread(self.notify, msg, timeout=3)
        time.sleep(1.5)
        self.load_preview()

    def action_attention(self) -> None:
        """Select the next agent that needs you (after the selected one) and put you in the message
        box, with its last words in the preview. Enter still jumps to its pane."""
        if not self.world:
            return
        if self.active_tab() != "agents":
            self.action_tab("agents")
        t = self.query_one("#agent-table", DataTable)

        def needing() -> list[int]:
            out = []
            for i in range(t.row_count):
                a, sub = self.agent_rows.get(t.coordinate_to_cell_key((i, 0)).row_key.value, (None, -1))
                if a and sub < 0 and a.status in NEEDS_YOU:
                    out.append(i)
            return out
        rows = needing()
        if not rows and any(a.status in NEEDS_YOU for a in self.world.agents):
            self.agent_filter, self.agent_sort = "", "needs"
            self.fill_agents()
            rows = needing()
        if not rows:
            self.notify("Nothing needs you right now.", timeout=3)
            return
        nxt = next((i for i in rows if i > t.cursor_row), rows[0])
        t.move_cursor(row=nxt)
        self.query_one("#agent-msg", Input).focus()

    @work(thread=True, exclusive=True, group="pane-op")
    def run_shape(self, kind: str) -> None:
        lay = self.pane_layout
        if not lay:
            return
        try:
            msg = arrange.arrange(lay.tab_id, kind)
        except Exception as e:
            self.call_from_thread(self.notify, str(e)[:300], title="herdr refused", severity="error")
            return
        self.call_from_thread(self.notify, msg, timeout=2)
        self.load_panes()

    def _pane_cwd(self, pane_id) -> str:
        snap = model.herdr.snapshot()
        return next((p.get("cwd", "") for p in snap.get("panes", []) if p.get("pane_id") == pane_id), "") \
            or os.path.expanduser("~")

    # ---- projects -------------------------------------------------------------------------
    def selected(self):
        """(ProjectView, Worktree | None) for the highlighted project row."""
        if not self.world or not self.selected_key:
            return None, None
        root, _, wt = self.selected_key.partition(WT_SEP)
        v = self.world.view(root)
        return v, (v.worktrees.get(wt) if v and wt else None)

    def fill_projects(self) -> None:
        t = self.query_one("#proj-table", DataTable)
        t.clear()
        target_row, row = 0, 0
        in_sidebar_header = False
        from . import startup
        folded = startup.ui_state().get("projects_folded", {})
        flt = self.query_one("#proj-filter", Input).value.strip().lower()
        self.proj_open = {}
        for v in self.world.projects:
            # worktrees with something running or open; the detail panel lists all of them
            show = [w for w in v.worktrees.values() if w.agents or w.workspace]
            show.sort(key=lambda w: (not w.agents, -max((s.mtime for s in w.sessions), default=0)))
            if flt and flt not in v.project.name.lower() and not any(
                    flt in w.label.lower() or any(flt in a.display.lower() for a in w.agents) for w in show):
                continue
            here = self.current_project and v.project.root == self.current_project.root
            box = Text("☑", style="bold") if v.in_sidebar else Text("☐", style="dim")
            is_open = bool(flt) or not folded.get(v.project.root, False)
            self.proj_open[v.project.root] = is_open
            chevron = Text(("▾" if is_open else "▸") if show else "", style="bold")
            name = Text(v.project.name[:30], style="bold" if here else ("bold" if v.in_sidebar else ""))
            if here:
                name.append("  ◀ here", style="bold")
            if show and not is_open:
                name.append(f"  ⎇{len(show)}", style="dim")

            t.add_row(chevron, box, name, summarize(v.counts()) or "",
                      age(v.last_activity) if v.last_activity else "", key=v.project.root)
            if v.project.root == self.selected_key:
                target_row = row
            row += 1
            for wt in (show[:12] if is_open else []):
                k = f"{v.project.root}{WT_SEP}{wt.label}"
                names = sorted({a.display for a in wt.agents})
                label = Text(f"  ⎇ {', '.join(names) or wt.label}"[:40], style="bold" if wt.agents else "dim")
                last = max((s.mtime for s in wt.sessions), default=0)
                t.add_row("", "", label, summarize(model.Counter(a.status for a in wt.agents)) or "",
                          age(last) if last else "", key=k)
                if k == self.selected_key:
                    target_row = row
                row += 1
        if t.row_count:
            t.move_cursor(row=target_row)
        self.set_empty("#proj-empty", "#proj-table",
                       f"No project matches “{flt}”." if flt else "No projects yet: start an agent in any folder.")
        self.show_project_detail()

    def show_project_detail(self) -> None:
        """The selected project: where it is, then its agents (jump) and sessions (resume) as rows."""
        v, wt = self.selected()
        self.query_one("#btn-wt-finish", Button).display = bool(wt and wt.exists)
        info = self.query_one("#proj-info", Static)
        items = self.query_one("#proj-items", DataTable)
        items.clear()
        if not items.columns:
            items.add_columns("", "", "")
        if not v:
            info.update("")
            return
        agents = wt.agents if wt else v.agents
        sessions = wt.sessions if wt else v.sessions
        out = Text()
        out.append(v.project.name, style="bold")
        if wt:
            out.append(f" ⎇ {wt.label}", style="bold")
        out.append(f"\n{(wt.path if wt else v.project.path) or v.project.root}", style="dim")
        extra = []
        if not wt and v.worktrees:
            quiet = [w for w in v.worktrees.values() if not w.agents]
            if quiet:
                extra.append(f"⎇ {len(quiet)} idle worktrees")
        saved_layouts = layouts.saved(v.project)
        if saved_layouts and not wt:
            extra.append("▦ layouts: " + ", ".join(saved_layouts))
        if extra:
            out.append("\n" + "   ".join(extra), style="dim")
        out.append("\nClick a row twice (or Enter): go to the agent / resume the session", style="dim italic")
        info.update(out)

        def head(key: str, text: str) -> None:
            items.add_row("", Text(text, style="bold"), "", key=key)

        if agents:
            head("h:agents", "Running")
            for a in sorted(agents, key=lambda a: model.STATE_ORDER.get(a.status, 9)):
                name = Text(a.display[:32], style="bold")
                if not a.in_herdr:
                    name.append("  ↗", style="dim")
                items.add_row(Text(STATE_ICON.get(a.status, "?"), style=STATE_STYLE.get(a.status, "")), name,
                              Text((a.activity or "")[:24], style="dim"), key=f"a:{a.key}")
                for i, sa in enumerate(a.subagents):
                    items.add_row("", Text(f"  {'⟳' if sa.workflow else '↳'} {sa.name}"[:32], style="dim"),
                                  Text((sa.activity or "")[:30], style="dim"), key=f"x:{a.key}:{i}")
        live_ids = {a.session_id for a in agents}
        rest = [s_ for s_ in sessions if s_.id not in live_ids][:10]
        if rest:
            head("h:sessions", "Recent sessions")
            for s_ in rest:
                items.add_row(Text(age(s_.mtime).strip(), style="dim"),
                              Text.assemble(cli_tag(s_.cli), " ", (s_.title[:30], "")),
                              "", key=f"s:{s_.cli}:{s_.id}")
        if not agents and not rest:
            head("h:none", "Nothing yet: + New agent starts one here")

    @on(DataTable.RowSelected, "#proj-items")
    def _proj_item(self, ev: DataTable.RowSelected) -> None:
        if ev.data_table.fresh_highlight() or not self.world:
            return
        key = ev.row_key.value or ""
        if key.startswith("a:"):
            a = next((a for a in self.world.agents if a.key == key[2:]), None)
            if a and a.in_herdr:
                self.finish(lambda: (model.herdr.focus_agent(a.pane_id), f"→ {a.pane_id}")[1], jump=True)
            elif a and a.mirror_pane:
                self.finish(lambda: panes.focus(a.mirror_pane), jump=True)
            elif a:
                self.notify(f"'{a.display}' runs in another window: switch to it there.", timeout=6)
        elif key.startswith("s:"):
            cli, _, sid = key[2:].partition(":")
            s_ = next((x for x in self.world.sessions if x.cli == cli and x.id == sid), None)
            if s_:
                self.finish(lambda: launch.resume(self.world, s_, focus=False), busy=f"▶ opening {s_.title[:40]}…")

    @on(DataTable.RowHighlighted, "#proj-table")
    def _proj_hl(self, ev: DataTable.RowHighlighted) -> None:
        if ev.row_key is not None and ev.row_key.value:
            self.selected_key = ev.row_key.value
            self.show_project_detail()

    @on(DataTable.RowSelected, "#proj-table")
    def _proj_sel(self, ev: DataTable.RowSelected) -> None:
        if not ev.data_table.fresh_highlight():
            self.action_open()

    @on(ClickTwiceTable.BoxClicked)
    def _box(self, ev: ClickTwiceTable.BoxClicked) -> None:
        if ev.table.id == "agent-table":
            if ev.row_key.startswith("grp:"):
                self.fold_agents(ev.row_key, None)
            else:
                self.toggle_mark(ev.row_key)
            return
        self.selected_key = ev.row_key
        self.action_toggle_sidebar()

    @on(TopBar.Clicked)
    def _top_clicked(self) -> None:
        """The top bar leads to what it reports: waiting agents first, else every agent."""
        waiting = self.world and any(a.status in NEEDS_YOU for a in self.world.agents)
        self.agent_filter = "needs" if waiting else ""
        self.action_tab("agents")
        if self.world:
            self.fill_agents()

    def set_empty(self, note_id: str, table_id: str, text: str) -> None:
        """An empty list says why, and what to do instead."""
        note = self.query_one(note_id, Static)
        empty = self.query_one(table_id, DataTable).row_count == 0
        note.update(text if empty else "")
        note.set_class(empty, "show")

    @on(ClickTwiceTable.Fold)
    def _fold(self, ev: ClickTwiceTable.Fold) -> None:
        if ev.table.id == "agent-table":
            self.fold_agents(ev.row_key, ev.open_)
        else:
            self.fold_project(ev.row_key, ev.open_)

    def fold_agents(self, key: str, open_: bool | None) -> None:
        """Fold or unfold a project's group in Agents (← → on any of its rows). A folded group
        still shows the agents that need you."""
        from . import startup
        if self.agent_sort == "recent":
            return
        if key.startswith("grp:"):
            root = key[4:]
        else:
            a, _ = self.agent_rows.get(key, (None, -1))
            if not a:
                return
            root = a.project.root
        folded = dict(startup.ui_state().get("agents_folded", {}))
        is_open = not folded.get(root, False)
        want = (not is_open) if open_ is None else open_
        if want == is_open:
            return
        folded[root] = not want
        startup.set_ui("agents_folded", folded)
        from . import sync
        sync.spawn_background()  # herdr's Agents panel folds the same project
        self.fill_agents()
        t = self.query_one("#agent-table", DataTable)
        try:
            t.move_cursor(row=t.get_row_index("grp:" + root))
        except Exception:
            pass

    def fold_project(self, key: str, open_: bool | None) -> None:
        from . import startup
        root = key.partition(WT_SEP)[0]
        is_open = getattr(self, "proj_open", {}).get(root, True)
        want = (not is_open) if open_ is None else open_
        if want == is_open:
            return
        folded = dict(startup.ui_state().get("projects_folded", {}))
        folded[root] = not want
        startup.set_ui("projects_folded", folded)
        self.selected_key = root
        self.fill_projects()

    @on(Input.Changed, "#proj-filter")
    def _proj_filter(self) -> None:
        self.fill_projects()

    @on(Input.Submitted, "#proj-filter")
    def _proj_filter_done(self) -> None:
        self.query_one("#proj-table", DataTable).focus()

    @on(ClickTwiceTable.Menu)
    def _row_menu(self, ev: ClickTwiceTable.Menu) -> None:
        if ev.table.id == "proj-table":
            self.selected_key = ev.row_key
            self.show_project_detail()
            self.push_screen(ContextMenu(self.project_menu_items(), ev.at), self.on_project_menu)
        elif ev.table.id == "agent-table":
            a, sub = self.agent_rows.get(ev.row_key, (None, -1))
            if a and sub < 0:
                self.push_screen(ContextMenu(self.agent_menu_items(a), ev.at), lambda c: self.on_agent_menu(a, c))
            elif ev.row_key.startswith("grp:"):
                root = ev.row_key[4:]
                items = [("new", "＋ New agent in this project…"), ("fold", "▸ / ▾ Fold or unfold")]
                self.push_screen(ContextMenu(items, ev.at), lambda c: c == "new" and self.new_agent_in(root)
                                 or c == "fold" and self.fold_agents(ev.row_key, None))

    def project_menu_items(self) -> list[tuple[str, str]]:
        v, wt = self.selected()
        if not v:
            return []
        items = [("open", "▶ Open it"), ("new", "+ New agent here…"), ("sessions", "☰ Its sessions")]
        if not wt:
            items.append(("sidebar", "☐ Hide from herdr's sidebar" if v.in_sidebar else "☑ Show in herdr's sidebar"))
            items.append(("layout", "▦ Restore its saved layout"))
            if any(w.agents or w.workspace for w in v.worktrees.values()):
                open_ = getattr(self, "proj_open", {}).get(v.project.root, True)
                items.append(("fold", "▸ Fold its worktrees" if open_ else "▾ Unfold its worktrees"))
        elif wt.exists:
            items.append(("finish", "⎇ Finish this worktree…"))
        return items

    def on_project_menu(self, choice: str | None) -> None:
        if not choice:
            return
        {"open": self.action_open, "new": self.action_new_dialog, "sessions": self.action_resume_project,
         "sidebar": self.action_toggle_sidebar, "layout": self.action_restore_layout,
         "finish": self.finish_worktree, "fold": lambda: self.fold_project(self.selected_key or "", None)}[choice]()

    def agent_menu_items(self, a) -> list[tuple[str, str]]:
        from . import startup
        items = [("goto", "→ Go to it")]
        if a.in_herdr:
            items += [("msg", "✉ Message it…"), ("enter", "⏎ Press Enter (accept)"), ("esc", "Esc (cancel)"),
                      ("stop", "^C Stop it"), ("compact", "⇣ Compact its context"), ("handoff", "⇢ Hand off its answer…"),
                      ("relaunch", "↻ Relaunch it…"),
                      ("mark", "☐ Untick for Send" if a.pane_id in self.marked else "☑ Tick for Send to many"),
                      ("w-done", "🔔 Tell me when it finishes"),
                      ("w-chain", "⛓ When it finishes, hand its answer to…")]
            from . import watch
            items += [(f"wx:{k}", f"✕ Stop: {watch.describe(w)}") for k, w in watch.for_pane(a.pane_id)]
        items.append(("new-beside", f"＋ New agent in {a.project.label[:30]}…"))
        if a.session_id and self.world:
            key = f"{a.cli}:{a.session_id}"
            row = next((r for g in startup.plan(self.world.sessions) for r in g.rows if startup.skey(r.session) == key),
                       None)
            if row is not None:
                items.append(("logon", "☐ Don't reopen at logon" if row.ticked else "☑ Reopen at logon"))
        return items

    def on_agent_menu(self, a, choice: str | None) -> None:
        from . import startup
        if not choice:
            return
        if choice == "goto":
            self.action_open()
        elif choice == "msg":
            self.action_message()
        elif choice in ("enter", "esc", "stop"):
            self.run_send(a.pane_id, a.display, "", {"enter": "enter", "esc": "esc", "stop": "ctrl+c"}[choice])
        elif choice == "compact":
            from . import compact
            self.run_send(a.pane_id, a.display, compact.command(a.cli), "")
        elif choice == "handoff":
            self.handoff_menu(self.query_one("#act-handoff", Button))
        elif choice == "relaunch":
            self.open_relaunch(a.cli, [a.pane_id])
        elif choice == "w-done":
            from . import watch
            self.notify(watch.start("done", a.pane_id, a.display), timeout=4)
            self.fill_agents()
        elif choice == "w-chain":
            self.chain_menu(a)
        elif choice.startswith("wx:"):
            from . import watch
            watch.cancel(choice[3:])
            self.notify("Stopped watching.", timeout=2)
            self.fill_agents()
        elif choice == "new-beside":
            self.new_agent_in(a.project.root + (WT_SEP + a.project.worktree if a.project.worktree else ""))
        elif choice == "mark":
            self.toggle_mark(a.key)
        elif choice == "logon":
            key = f"{a.cli}:{a.session_id}"
            row = next((r for g in startup.plan(self.world.sessions) for r in g.rows
                        if startup.skey(r.session) == key), None)
            startup.set_ticks({key: not (row and row.ticked)}, [a.project.root] if not (row and row.ticked) else [])
            self.notify("Reopens at logon." if not (row and row.ticked) else "Won't reopen at logon.", timeout=2)
            self.query_one(StartupPane).refresh_rows()

    def chain_menu(self, src) -> None:
        targets = [a for a in (self.world.agents if self.world else []) if a.in_herdr and a.pane_id != src.pane_id]
        if not targets:
            self.notify("No other agent in herdr to hand it to.", severity="warning")
            return
        items = [("-head", f"When {src.display[:24]} finishes, send its answer to")]
        items += [(f"t:{a.pane_id}", f"{a.cli:<6} {a.display[:34]}") for a in targets]

        def chosen(c):
            if not c or not c.startswith("t:"):
                return
            from . import watch
            t = next(a for a in targets if a.pane_id == c[2:])
            self.notify(watch.start("chain", src.pane_id, src.display, t.pane_id, t.display), timeout=5)
            self.fill_agents()
        self.push_screen(ContextMenu(items, self._menu_below(self.query_one("#agent-table"))), chosen)

    def new_agent_in(self, key: str, dialog: bool = True) -> None:
        """New agent in this project (or worktree), from Agents or from herdr's workspace menu."""
        self.selected_key = key
        if dialog:
            self.action_new_dialog()

    def open_relaunch(self, cli: str = "", panes: list[str] | None = None) -> None:
        """Every relaunch (all, one CLI, one agent) goes through the same dialog."""
        if self.world is None:
            return
        from .startup_ui import RelaunchSheet
        self.push_screen(RelaunchSheet(self.world, cli, panes))

    def action_help(self) -> None:
        from .startup_ui import HelpScreen
        title, body = HELP.get(self.active_tab(), ("", ""))
        self.push_screen(HelpScreen(title, body))

    def toggle_mark(self, key: str) -> None:
        """Mark agents to message several at once (only agents in herdr can be typed into)."""
        a, sub = self.agent_rows.get(key, (None, -1))
        if not a or sub >= 0:
            return
        if not a.in_herdr:
            self.notify(f"'{a.display}' runs in another window: herdr can't type into it.", severity="warning")
            return
        self.marked ^= {a.pane_id}
        self.fill_agents()

    # ---- agents ---------------------------------------------------------------------------
    def fill_agents(self) -> None:
        t = self.query_one("#agent-table", DataTable)
        keep = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value if t.row_count else None
        scroll = t.scroll_y
        t.clear()
        self.agent_rows = {}
        rows = []
        for a in self.world.agents:
            if self.agent_filter == "outside":
                if a.in_herdr:
                    continue
            elif self.agent_filter == "needs":
                if a.status not in NEEDS_YOU:
                    continue
            elif self.agent_filter == "idle":  # Parked: idle, and inactive (idle for an hour)
                if a.status not in ("idle", "inactive"):
                    continue
            elif self.agent_filter and a.status != self.agent_filter:
                continue
            rows.append(a)
        info = getattr(self, "_pane_info", {})
        visited = {e["pane_id"]: e["at"] for e in history.recent(set(info))}
        grouped = self.agent_sort != "recent"
        if not grouped:
            rows.sort(key=lambda a: -max(visited.get(a.pane_id, 0), visited.get(a.mirror_pane, 0)))
        else:  # nested under their project: running, then needs you, then idle; most recent first
            first: dict[str, tuple] = {}
            for a in rows:
                first[a.project.root] = min(first.get(a.project.root, (99,)), model.rank(a))
            rows.sort(key=lambda a: (first[a.project.root], a.project.name.lower(), model.rank(a),
                                     a.display.lower()))
        from . import startup, watch
        folded = startup.ui_state().get("agents_folded", {}) if grouped else {}
        watches, watch_icons = watch.load(), watch.KINDS
        group = None
        for a in rows:
            if grouped and a.project.root != group:
                group = a.project.root
                mine = [x for x in rows if x.project.root == group]
                shut = folded.get(group, False)
                head = Text(a.project.name[:30], style="bold")
                hidden = sum(1 for x in mine if x.status not in NEEDS_YOU) if shut else 0
                t.add_row(Text("▸" if shut else "▾", style="bold"), "", "", head, "", "",
                          Text(summarize(model.Counter(x.status for x in mine)), style="dim"),
                          Text(f"{hidden} more folded: → or click ▸ to show" if hidden else "", style="dim"),
                          key="grp:" + group)
            if grouped and folded.get(group, False) and a.status not in NEEDS_YOU:
                continue
            here = self.current_project and a.project.root == self.current_project.root
            who = Text(("  " if grouped else "") + a.display[:30], style="bold")
            if not a.in_herdr:
                who.append("  ↗", style="dim")
            marks = "".join(sorted({watch_icons.get(w.get("kind"), "") for w in watches.values()
                                    if w.get("pane") == a.pane_id}))
            if marks:
                who.append(" " + marks)
            if a.workflows + a.jobs:  # sub-agents get their own ↳ rows below; background tasks a count
                who.append(f" ⟳{a.workflows + a.jobs}", style="#83a598")
            if grouped:  # the project is the group above; say only which checkout
                proj = Text(("⎇ " + a.project.worktree)[:26] if a.project.worktree else "main",
                            style="dim" if a.project.worktree else "dim")
            else:
                proj = Text(a.project.label[:26], style="bold" if here else "")
            raw_doing = a.activity or a.title or ""
            doing = Text("❓ " + clip(a.question.text, 52), style="bold #fe8019") if a.question else clip(raw_doing, 56)
            box = (Text("☑", style="bold") if a.pane_id in self.marked else Text("☐", style="dim")) \
                if a.in_herdr else ""
            waits = Text(age(a.waiting_since), style=STATE_STYLE.get(a.status, "")) \
                if a.status in NEEDS_YOU and a.waiting_since else ""
            if self.agent_sort == "recent":
                at = max(visited.get(a.pane_id, 0), visited.get(a.mirror_pane, 0))
                waits = Text(age(at) if at else "", style="dim")
            t.add_row(box, Text(STATE_ICON.get(a.status, "?"), style=STATE_STYLE.get(a.status, "")), cli_tag(a.cli),
                      who, waits, ctx_text(a), proj, doing, key=a.key)
            self.agent_rows[a.key] = (a, -1)
            for i, sa in enumerate(a.subagents):
                k = f"{a.key}#sub{i}"
                t.add_row("", Text("⟳" if sa.workflow else "↳", style="dim"), "", Text(f"  {sa.name}", style="dim"), "", "",
                          Text(sa.kind or "", style="dim"), clip(sa.activity or sa.description or "", 56), key=k)
                self.agent_rows[k] = (a, i)
        if self.agent_sort == "recent" and not self.agent_filter:
            agent_panes = {a.pane_id for a in self.world.agents} | {a.mirror_pane for a in self.world.agents}
            for pid, at in sorted(visited.items(), key=lambda kv: -kv[1]):
                if pid in agent_panes:
                    continue
                pinfo = info.get(pid, {})
                proj = self.world.project_of_workspace(pinfo.get("workspace_id", ""))
                label = pinfo.get("label") or os.path.basename((pinfo.get("cwd") or "").rstrip("\\/")) or pid
                t.add_row("", Text("▫", style="dim"), "", Text(label[:30]), Text(age(at), style="dim"), "",
                          (proj.label if proj else "")[:26], Text("shell / other pane", style="dim"), key=f"pane:{pid}")
        cols = t.ordered_columns
        if len(cols) > 6:
            for i, want in ((4, "Visited" if not grouped else "Waits"), (6, "Where" if grouped else "Project")):
                if str(cols[i].label) != want:
                    cols[i].label = Text(want)
                    t.refresh()
        sort_btn = self.query_one("#agent-sort", Button)
        sort_btn.label = "⇅ Recent" if self.agent_sort == "recent" else "⇅ Running"
        counts = model.Counter(a.status for a in self.world.agents)
        counts["needs"] = sum(counts.get(x, 0) for x in NEEDS_YOU)
        counts["idle"] = counts.get("idle", 0) + counts.get("inactive", 0)
        counts["outside"] = sum(1 for a in self.world.agents if not a.in_herdr)
        counts["all"] = len(self.world.agents)
        names = {f or "all": label for label, f in AGENT_FILTERS}
        for b in self.query("#agent-bar Button"):
            if b.id and b.id.startswith("flt-"):
                f = b.id[4:]
                name = names[f].split()[0] if self.has_class("-narrow") and f != "all" else names[f]
                b.label = f"{name} {counts.get(f, 0)}"
                b.variant = "primary" if f == (self.agent_filter or "all") else "default"
        self.query_one("#agent-bar").refresh(layout=True)  # a count grew a digit: re-measure the buttons
        what = {"needs": "waiting on you", "working": "working", "idle": "parked",
                "outside": "running in other windows"}.get(self.agent_filter, "")
        self.set_empty("#agent-empty", "#agent-table",
                       ("Nothing needs you right now." if self.agent_filter == "needs" else
                        f"No agent is {what} right now. Press All to see every agent.") if what else
                       "No agents running. Start one: Projects (1) › + New agent, or resume one in Sessions (3).")
        keys = [t.coordinate_to_cell_key((i, 0)).row_key.value for i in range(t.row_count)]
        if keep in keys:
            t.move_cursor(row=keys.index(keep), scroll=False)
            t.scroll_to(y=scroll, animate=False)
        elif keys and self._place.get("agent") in keys and not getattr(self, "_placed", False):
            t.move_cursor(row=keys.index(self._place["agent"]))
        elif keys:  # a project header has nothing to act on: start on who needs you, else the first agent
            agent_rows = [i for i, k in enumerate(keys) if self.agent_rows.get(k, (None, -1))[1] == -1
                          and self.agent_rows.get(k, (None, -1))[0]]
            needy = [i for i in agent_rows if self.agent_rows[keys[i]][0].status in NEEDS_YOU]
            t.move_cursor(row=(needy or agent_rows or [0])[0])
        self._placed = bool(keys)
        live = {a.pane_id for a in self.world.agents if a.in_herdr}
        self.marked &= live
        send = self.query_one("#send-msg", Button)
        send.label = f"Send to {len(self.marked)}" if self.marked else "Send"
        self.query_one("#mark-clear", Button).display = bool(self.marked)

    @on(DataTable.RowSelected, "#agent-table")
    def _agent_sel(self, ev: DataTable.RowSelected) -> None:
        if not ev.data_table.fresh_highlight():
            self.action_open()

    # ---- keys -----------------------------------------------------------------------------
    def render_keys(self) -> None:
        """Settings › Keys (only once the Settings tab has been built: it renders itself then)."""
        if not self.query("#keys-body"):
            return
        prefix, groups, custom = keymap.effective()
        out = Text()
        out.append(f"Prefix {keymap.pretty(prefix, prefix)}", style="bold")
        out.append("  ·  press it, release, then the key\n\n", style="dim")

        def row(keys_: str, what: str, dim: bool = False) -> None:
            out.append(f"  {keys_:<36} ", style="dim" if dim else "bold")
            out.append(f"{what}\n", style="dim" if dim else "")

        if custom:
            out.append("Navigator\n", style="bold underline")
            for b in custom:
                row("   ".join(keymap.pretty(k, prefix) for k in b.keys), b.label.removeprefix("Navigator: "))
            out.append("\n")
        for title, rows in groups:
            out.append(f"{title}\n", style="bold underline")
            for b in rows:
                if b.keys:
                    row("   ".join(keymap.pretty(k, prefix) for k in b.keys), b.label)
            out.append("\n")
        out.append("Mouse\n", style="bold underline")
        row("drag sidebar edge", "resize the sidebar")
        row("drag a split border", "resize panes")
        row("right-click pane / space", "herdr menu: split, zoom, rename, close")
        row("Layout tab: drag a pane", "middle swaps · edge places it beside")
        row("Sessions: Enter / click ☐", "resume it / tick it for logon (bright = yours, dim = auto)")
        row("Sessions: right-click", "open, relaunch, launch options, back to automatic")
        row("Agents tab: click ☐", "tick agents: Send, ⏎, ⇣ compact and ☰ prompts go to all of them")
        row("Buttons with ▾", "open a small menu (new agent, prompts, hand off, new tab)")
        row("⚙ Settings", "logon restore, accounts, phone alerts, prompts: they save on their own")
        self.query_one("#keys-body", Static).update(out)

    # ---- actions --------------------------------------------------------------------------
    def active_tab(self) -> str:
        return self.query_one("#tabs", TabbedContent).active

    def focus_table(self) -> None:
        tab = self.active_tab()
        if tab == "sessions" and self.search_first:
            self.search_first = False  # after the screen's own auto-focus has run
            self.set_timer(0.15, lambda: self.query_one("#st-search", Input).focus())
            return
        if tab == "usage" and self.world:
            self.query_one(UsagePane).load(self.world)
            self.query_one("#usage-table", DataTable).focus()
            return
        if tab == "panes":
            self.query_one("#pane-map", LayoutMap).focus()
            self.load_panes()
            return
        if tab == "settings":
            pane = self.query_one(SettingsPane)
            pane.query_one("#set-nav").focus()
            if pane.section == "accounts":
                pane.load_accounts()
            return
        tid = {"projects": "#proj-table", "agents": "#agent-table", "sessions": "#start-table"}.get(tab)
        if tid:
            self.query_one(tid, DataTable).focus()

    def set_hint(self) -> None:
        pass  # the footer shows the keys that work on the current tab; tooltips explain buttons

    async def ensure_tab(self, tab: str) -> None:
        """Build the Usage or Settings tab on its first opening."""
        if tab == "usage" and not self.query(UsagePane):
            await self.query_one("#usage", TabPane).mount(UsagePane(id="usage-pane"))
        elif tab == "settings" and not self.query(SettingsPane):
            await self.query_one("#settings", TabPane).mount(SettingsPane(self.settings_section, id="settings-pane"))

    async def open_settings(self, section: str) -> None:
        """Settings, at one section (built first if it was never opened)."""
        self.settings_section = section
        built = bool(self.query(SettingsPane))
        self.action_tab("settings")
        await self.ensure_tab("settings")
        if built:
            self.query_one(SettingsPane).show_section(section)

    @on(TabbedContent.TabActivated)
    async def _tab_changed(self) -> None:
        await self.ensure_tab(self.active_tab())
        self.set_hint()
        self.focus_table()
        self.refresh_bindings()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        tab = self.active_tab() if self.is_mounted else self.start_tab
        typing = isinstance(self.focused, Input)
        if typing and action in ("new", "new_dialog", "resume_project", "search", "filter", "tab", "quit",
                                 "toggle_sidebar", "help"):
            return False
        if action in ("new", "new_dialog", "resume_project", "toggle_sidebar"):
            return tab == "projects"
        if action == "search":
            return tab in ("projects", "sessions")
        if action == "filter":
            return tab == "agents"
        if action in ("pane_sel", "pane_op", "pane_rename", "shape"):
            return tab == "panes" and not typing
        if action == "message":
            return tab == "agents" and not typing
        if action == "attention":
            return tab in ("agents", "projects", "recent") and not typing
        if action == "restore_layout":
            return tab == "projects" and not typing
        if action == "sidebar_width":
            return not typing
        if action == "open":
            return tab in ("projects", "agents", "panes", "recent")
        return True

    def action_tab(self, name: str) -> None:
        self.query_one("#tabs", TabbedContent).active = name

    def action_refresh(self) -> None:
        self.render_keys()
        self.refresh_world_and_sidebar()

    @work(thread=True, exclusive=True, group="sidebar")
    def refresh_world_and_sidebar(self) -> None:
        world = model.build()
        msgs = sidebar.refresh_worktrees(world)
        if msgs:
            sidebar.after_change()
            world = model.build()
            self.call_from_thread(self.notify, "\n".join(msgs), title="Sidebar")
        self.call_from_thread(self.apply_world, world)

    def action_back(self) -> None:
        f = self.focused
        if isinstance(f, Input):
            if any(isinstance(w, SettingsPane) for w in f.ancestors):
                # a saved setting: Esc cancels the edit (blur would save it), it never empties it
                orig = getattr(f, "_nav_orig", None)
                if orig is not None:
                    f.value = orig
                self.set_focus(None)
                return
            if f.value:  # search, filter and message boxes: Esc clears them first
                f.value = ""
                return
        self.exit()

    def action_search(self) -> None:
        if self.active_tab() == "projects":
            self.query_one("#proj-filter", Input).focus()
            return
        self.action_tab("sessions")
        self.query_one("#st-search", Input).focus()

    def action_resume_project(self) -> None:
        v, _ = self.selected()
        self.action_tab("sessions")
        if v:
            self.query_one(StartupPane).jump_to_project(v.project.root)

    def action_filter(self, state: str) -> None:
        self.agent_filter = state
        self.fill_agents()

    def action_toggle_sidebar(self) -> None:
        v, _ = self.selected()
        if v:
            self.toggle_sidebar_worker(v.project.root, not v.in_sidebar)

    @work(thread=True, exclusive=True, group="sidebar")
    def toggle_sidebar_worker(self, root: str, on: bool) -> None:
        try:
            msgs = sidebar.set_selected(self.world, root, on)
        except Exception as e:
            self.call_from_thread(self.notify, str(e)[:300], title="herdr refused", severity="error")
            return
        sidebar.after_change()
        world = model.build()
        if msgs:
            self.call_from_thread(self.notify, "\n".join(msgs), title="Sidebar")
        self.call_from_thread(self.apply_world, world)

    def action_new(self, cli: str) -> None:
        v, wt = self.selected()
        if not v:
            return
        if wt and wt.exists:
            p = model.projects.Project(v.project.root, v.project.name, wt.label, v.project.path, wt.path)
            self.finish(lambda: launch._open_in_tab(self.world, p, wt.path, f"{cli} {wt.label}",
                                                    settings.load().launch.get(f"{cli}_new", cli), focus=False),
                        busy=f"▶ starting {cli}…")
        else:
            self.finish(lambda: launch.new_agent(self.world, v.project, cli, focus=False), busy=f"▶ starting {cli}…")

    def finish_worktree(self) -> None:
        from . import worktrees
        v, wt = self.selected()
        if not (v and wt and wt.exists):
            return
        st = worktrees.status(wt.path, v.project.path or v.project.root, [a.display for a in wt.agents],
                              [wt.workspace.id] if wt.workspace else [])

        def done(action):
            if action == "remove":
                self.finish(lambda: worktrees.remove(st, v.project.path or v.project.root))
            elif action == "close":
                msgs = worktrees.close_workspaces(st)
                self.notify("\n".join(msgs) or "nothing to close")
                self.refresh_world_and_sidebar()
        self.push_screen(FinishWorktree(st, f"{v.project.name} ⎇ {wt.label}"), done)

    def show_digest(self, world: World) -> None:
        from . import digest
        if self.digest_checked:
            return
        self.digest_checked = True
        entries = digest.items(world, self.seen_before) if digest.due(self.seen_before) else []
        if not entries:
            digest.mark_seen()
            return

        def done(r) -> None:  # seen once acknowledged; "later" keeps it for the next open
            if not r:
                return
            digest.mark_seen()
            if r.get("pane"):
                self.show_agent(r["pane"])
        self.push_screen(Digest(entries, age(self.seen_before).strip()), done)

    def show_agent(self, pane: str) -> None:
        """Select this agent in the Agents tab (its preview below); Enter there jumps to it."""
        self.action_tab("agents")
        if self.agent_filter:
            self.agent_filter = ""
            self.fill_agents()
        t = self.query_one("#agent-table", DataTable)
        for i in range(t.row_count):
            a, sub = self.agent_rows.get(t.coordinate_to_cell_key((i, 0)).row_key.value, (None, -1))
            if a and sub < 0 and a.pane_id == pane:
                t.move_cursor(row=i)
                break
        else:
            self.notify("That agent is no longer open.", timeout=4)
        t.focus()

    def action_new_dialog(self) -> None:
        v, wt = self.selected()
        if not v:
            return
        folder = wt.path if wt and wt.exists else (v.project.path or v.project.root)
        is_git = os.path.isdir(os.path.join(v.project.path or v.project.root, ".git"))
        clis = installed_clis() or ["claude"]

        def done(r):
            if not r:
                if getattr(self, "new_only", False):
                    self.exit()  # opened only for this dialog, from herdr's workspace menu
                return
            p = v.project
            if wt and wt.exists and not r["branch"]:
                p = model.projects.Project(v.project.root, v.project.name, wt.label, v.project.path, wt.path)
            only = getattr(self, "new_only", False)  # from herdr's workspace menu: go to it
            self.finish(lambda: launch.new_session(self.world, p, r["folder"] or folder, r["cli"], r["name"],
                                                   r["prefs"], r["branch"], focus=only),
                        busy=f"▶ starting {r['name'] or r['cli']}…")
        self.push_screen(NewSession(clis, folder, v.project.label + (f" ⎇ {wt.label}" if wt else ""), is_git), done)

    def action_open(self) -> None:
        if not self.world:
            return
        tab = self.active_tab()
        if tab == "panes":
            self.action_pane_op("focus")
            return
        if tab == "projects":
            v, wt = self.selected()
            if v and wt:
                if not wt.exists:
                    self.notify(f"{wt.label} was removed from disk", severity="warning")
                    return
                p = model.projects.Project(v.project.root, v.project.name, wt.label, v.project.path, wt.path)
                self.finish(lambda: launch.goto_project(self.world, p, wt.path), jump=True)
            elif v:
                self.finish(lambda: launch.goto_project(self.world, v.project), jump=True)
        elif tab == "agents":
            t = self.query_one("#agent-table", DataTable)
            if not t.row_count:
                return
            k = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
            if k.startswith("pane:"):
                self.finish(lambda: panes.focus(k[5:]), jump=True)
                return
            if k.startswith("grp:"):
                self.fold_agents(k, None)
                return
            a, _ = self.agent_rows.get(k, (None, -1))
            if not a:
                return
            if a.in_herdr:
                self.finish(lambda: (model.herdr.focus_agent(a.pane_id), f"→ {a.pane_id}")[1], jump=True)
            elif a.mirror_pane:
                self.finish(lambda: panes.focus(a.mirror_pane), jump=True)
            else:
                self.notify(f"'{a.name}' runs in another terminal window (pid {a.pid}), so herdr can't "
                            "jump to it. To bring it here: exit it there, then resume it from Sessions (3).",
                            title="Outside herdr", timeout=10)

    def finish(self, fn, jump: bool = False, busy: str = "") -> None:
        """Run an action off the UI thread (the screen never freezes or greys out) and stay open
        where you are. Only a jump (go to a pane, agent or project) closes the Navigator, since it
        would cover what you jumped to."""
        self.notify(busy or "Working…", timeout=2)
        self._run_action(fn, jump)

    @work(thread=True, group="action")
    def _run_action(self, fn, jump: bool) -> None:
        try:
            msg = fn()
        except Exception as e:  # show it, keep the Navigator open
            self.call_from_thread(self.notify, str(e)[:300], title="herdr refused", severity="error", timeout=8)
            return
        if msg and msg.startswith("✗"):
            self.call_from_thread(self.notify, msg, severity="warning", timeout=10)
            return
        if jump or getattr(self, "new_only", False):
            self.call_from_thread(self.exit, msg)
            return
        if msg:
            self.call_from_thread(self.notify, msg, timeout=5)
        self.load_world()  # the new session shows up, the cursor stays where it was

    def exit(self, *args, **kw) -> None:  # type: ignore[override]
        if not getattr(self, "_nav_closing", False):
            self.remember_place()
        self._nav_closing = True
        super().exit(*args, **kw)

    def _handle_exception(self, error: Exception) -> None:
        """A background refresh that finds a widget gone (the Navigator closing, a dialog on top)
        is harmless: note it in the state dir instead of tearing the whole Navigator down."""
        from textual.css.query import NoMatches
        from textual.worker import WorkerFailed
        cause = error.error if isinstance(error, WorkerFailed) else error
        if isinstance(cause, NoMatches):
            try:
                with open(settings.state_dir() / "ui-errors.log", "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ignored: {cause}\n")
            except OSError:
                pass
            return
        super()._handle_exception(error)

    def call_from_thread(self, callback, *args, **kw):  # type: ignore[override]
        """Workers finishing while the Navigator closes must not crash it: their screen is gone."""
        from textual.css.query import NoMatches
        if getattr(self, "_nav_closing", False):
            return None

        def safe(*a, **k):
            try:
                return callback(*a, **k)
            except NoMatches:
                return None
        try:
            return super().call_from_thread(safe, *args, **kw)
        except RuntimeError:  # the event loop already stopped
            return None

    def remember_place(self) -> None:
        """Where you were (tab, project, agent row), so the next F1 opens right there."""
        from . import startup
        try:
            t = self.query_one("#agent-table", DataTable)
            agent = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value if t.row_count else None
            startup.set_ui("place", {"tab": self.active_tab(), "project": self.selected_key, "agent": agent,
                                     "at": time.time()})
        except Exception:
            pass

    # ---- buttons --------------------------------------------------------------------------
    @on(Button.Pressed)
    def _button(self, ev: Button.Pressed) -> None:
        bid = ev.button.id or ""
        actions = {
            "btn-go": self.action_open, "btn-resume": self.action_resume_project,
            "btn-new": lambda: self.new_menu(ev.button),
            "btn-sidebar": self.action_toggle_sidebar,
        }
        if bid in actions:
            actions[bid]()
        elif bid == "pane-watch":
            from . import watch
            lay = self.pane_layout
            b = next((b for b in lay.panes if b.pane_id == self.pane_selected), None) if lay else None
            if b:
                cur = watch.for_pane(b.pane_id)
                if cur:
                    for k, _ in cur:
                        watch.cancel(k)
                    self.notify("Stopped watching for errors.", timeout=2)
                else:
                    self.notify(watch.start("errors", b.pane_id, b.label or b.pane_id), timeout=4)
                self.show_panes(lay)
        elif bid.startswith("pop-"):
            parts = bid[4:].split("-")
            if parts[0] == "rename":
                self.action_pane_rename()
            else:
                self.action_pane_op(parts[0], parts[1] if len(parts) > 1 else "")
        elif bid == "send-msg":
            self.send_to_agent(self.query_one("#agent-msg", Input).value)
            self.query_one("#agent-msg", Input).value = ""
        elif bid == "key-ctrl_c" and getattr(self, "stop_armed", 0) < time.monotonic() - 5:
            self.stop_armed = time.monotonic()
            n = len(self.marked) or 1
            self.notify(f"Stop {'the selected agent' if n == 1 else f'{n} ticked agents'}? It interrupts what "
                        "it is doing. Click ^C Stop again within 5 s to confirm.", severity="warning", timeout=5)
        elif bid.startswith("key-"):
            self.stop_armed = 0
            self.send_to_agent(key=bid[4:].replace("_", "+"))
        elif bid == "act-compact":
            self.send_compact()
        elif bid == "act-prompts":
            self.prompts_menu(ev.button)
        elif bid == "act-handoff":
            self.handoff_menu(ev.button)
        elif bid == "mark-clear":
            self.marked = set()
            self.fill_agents()
        elif bid == "btn-attention":
            self.action_attention()
        elif bid == "btn-relaunch-all":
            self.open_relaunch()
        elif bid == "agent-sort":
            self.agent_sort = "needs" if self.agent_sort == "recent" else "recent"
            self.fill_agents()
        elif bid.startswith("ans-") and self.answer_to:
            pane, name = self.answer_to
            self.run_send(pane, name, "", bid[4:])
        elif bid == "layout-save":
            inp = self.query_one("#layout-name", Input)
            self.layout_op("save", inp.value.strip() or "default")
            inp.value = ""
        elif bid == "layout-restore":
            self.layout_op("restore", self.saved_selected())
        elif bid == "layout-delete":
            self.layout_op("delete", self.saved_selected())
        elif bid in ("sbw-minus", "sbw-plus"):
            self.action_sidebar_width("-6" if bid == "sbw-minus" else "+6")
        elif bid == "btn-wt-finish":
            self.finish_worktree()
        elif bid == "btn-layout":
            self.action_restore_layout()
        elif bid.startswith("shape-"):
            self.run_shape(bid[6:])
        elif bid == "presets-toggle":
            items = [(f"preset:{name}", name) for name in panes.PRESETS]
            self.push_screen(ContextMenu(items, self._menu_below(ev.button)),
                             lambda c: c and self.action_pane_op("preset", c[7:]))
        elif bid.startswith("flt-"):
            f = bid[4:]
            self.action_filter("" if f == "all" else f)


def main() -> None:
    tab = os.environ.pop("NAV_TAB", "") or (sys.argv[1] if len(sys.argv) > 1 else "projects")
    msg = Navigator(tab).run()
    if msg:
        print(msg)


if __name__ == "__main__":
    main()
