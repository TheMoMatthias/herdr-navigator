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

STATE_STYLE = {"blocked": "bold red", "done": "bold green", "working": "yellow", "idle": "dim", "unknown": "magenta"}
CLI_STYLE = {
    "claude": "#d97757", "codex": "#10a37f", "pi": "#7aa2f7", "opencode": "#e5c07b", "kilo": "#f8f675",
    "gemini": "#4796e3", "qwen": "#8b7cf6", "copilot": "#a371f7", "droid": "#ff7b39", "amp": "#f34e3f",
    "cline": "#56b6c2", "cursor": "#c8c8c8", "cursor-agent": "#c8c8c8", "hermes": "#d4a72c",
}


# Order of the "+ New" picker and the Resume filter chips; anything else follows alphabetically.
CLI_ORDER = ["claude", "codex", "pi", "opencode", "kilo", "gemini", "qwen", "copilot",
             "droid", "amp", "cline", "cursor", "hermes"]
CLI_NEW_KEY = {"claude": "c", "codex": "x", "pi": "e", "opencode": "u", "kilo": "y"}
CLI_SHORT = {"opencode": "opencd", "copilot": "copilt", "cursor-agent": "cursor"}
TAG_WIDTH = 6


def cli_tag(cli: str) -> Text:
    """The source CLI as a coloured fixed-width name, so titles after it line up."""
    name = CLI_SHORT.get(cli, cli or "?")[:TAG_WIDTH]
    return Text(name.ljust(TAG_WIDTH), style=CLI_STYLE.get(cli, "dim"))


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
TABS = ["projects", "agents", "panes", "resume", "recent", "keys"]
WT_SEP = "|wt|"


def state_text(status: str) -> Text:
    return Text(f"{STATE_ICON.get(status, '?')} {status}", style=STATE_STYLE.get(status, ""))


class ClickTwiceTable(DataTable):
    """Row table: a click on a new row selects it, a second click opens it.
    A click in the first column posts BoxClicked instead (the sidebar checkbox)."""

    BINDINGS = [Binding("j", "cursor_down", "Down", show=False), Binding("k", "cursor_up", "Up", show=False)]

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
        if meta.get("column") == 0 and meta.get("row", -1) >= 0 and self.id == "proj-table":
            row = meta["row"]
            self.move_cursor(row=row)
            rk = self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value
            self.post_message(self.BoxClicked(self, rk))
            event.stop()
            return
        await super()._on_click(event)


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
            fill = "bold reverse cyan" if sel else ""
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
                    if sel:
                        style[y][x] = fill
                    elif edge_x or edge_y:
                        style[y][x] = "bold green" if b.focused else "dim"
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
                    style[yy][xs + j] = fill or ("bold" if i == 0 else "dim")
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

    CSS = """
    Screen { background: $surface; }
    #topbar { height: 1; padding: 0 1; background: $boost; }
    #hint { height: 1; padding: 0 1; color: $text-muted; }
    #proj-detail { width: 44%; min-width: 36; padding: 0 1; border-left: tall $primary 40%; }
    .btnrow { height: 3; }
    .btnrow Button { margin: 0 1 0 0; min-width: 6; }
    #resume-bar, #agent-bar { height: 3; }
    #resume-search { width: 1fr; }
    DataTable { height: 1fr; }
    #keys-body { padding: 0 1; }
    #shape-bar { height: 3; }
    #shape-bar Button { margin: 0 1 0 0; min-width: 12; }
    #preset-row, #new-row { height: 3; display: none; }
    #new-row.show { display: block; }
    #new-row Button { margin: 0 1 0 0; min-width: 6; }
    #resume-cli { height: 3; }
    #resume-cli Button { margin: 0 1 0 0; min-width: 6; }
    #preset-row.show { display: block; }
    #pane-map { width: 1fr; height: 1fr; min-height: 10; }
    #pane-tools { width: 36; padding: 0 1; }
    .rowlabel { width: 5; margin-top: 1; color: $text-muted; }
    #pane-tools Horizontal { height: 3; }
    #pane-tools Button { width: 1fr; min-width: 4; margin: 0; }
    #pane-selected { height: 2; }
    #pane-rename { display: none; }
    #pane-rename.show { display: block; }
    #saved-row { height: 3; }
    #layout-name { width: 1fr; }
    #saved-table { height: 5; }
    #saved-table.empty { display: none; }
    #agent-detail { height: 14; border-top: tall $primary 40%; }
    #agent-preview { height: 1fr; padding: 0 1; }
    #agent-send { height: 3; }
    #agent-msg { width: 1fr; }
    """

    BINDINGS = [
        Binding("1", "tab('projects')", "Projects", show=False),
        Binding("2", "tab('agents')", "Agents", show=False),
        Binding("3", "tab('panes')", "Layout", show=False),
        Binding("4", "tab('resume')", "Resume", show=False),
        Binding("5", "tab('recent')", "Recent", show=False),
        Binding("6", "tab('keys')", "Keys", show=False),
        Binding("enter", "open", "Open", priority=False),
        Binding("space", "toggle_sidebar", "Sidebar"),
        Binding("r", "resume_project", "Resume"),
        Binding("slash", "search", "Search"),
        Binding("m", "message", "Message"),
        Binding("g", "attention", "Next ⚑"),
        Binding("escape", "back", "Close"),
        # everything below works but stays out of the footer (buttons cover it)
        Binding("c", "new('claude')", "New Claude", show=False),
        Binding("x", "new('codex')", "New Codex", show=False),
        Binding("e", "new('pi')", "New Pi", show=False),
        Binding("u", "new('opencode')", "New OpenCode", show=False),
        Binding("y", "new('kilo')", "New Kilo", show=False),
        Binding("l", "restore_layout", "Restore layout", show=False),
        Binding("p", "toggle_project", "This project/all", show=False),
        Binding("b", "filter('blocked')", "Blocked", show=False),
        Binding("w", "filter('working')", "Working", show=False),
        Binding("d", "filter('done')", "Done", show=False),
        Binding("i", "filter('idle')", "Idle", show=False),
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
        Binding("f5", "refresh", "Refresh", show=False),
        Binding("q", "quit", "Close", show=False),
    ]

    def __init__(self, start_tab: str = "projects") -> None:
        super().__init__()
        self.start_tab = start_tab if start_tab in TABS else "projects"
        self.world: World | None = None
        self.current_project = None          # project of the focused workspace
        self.only_project = True             # Resume: limit to the selected project
        self.agent_filter = ""
        self.cli_filter = ""                 # Resume: only this CLI's sessions ("" = all)
        self.selected_key: str | None = None  # project root, or root|wt|label
        self.agent_rows: dict[str, tuple[Agent, int]] = {}
        self.pane_layout = None
        self.pane_selected: str | None = None
        self.close_armed = ""

    # ---- layout ---------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("", id="topbar")
        with TabbedContent(initial=self.start_tab, id="tabs"):
            with TabPane("Projects", id="projects"):
                with Horizontal():
                    yield ClickTwiceTable(id="proj-table")
                    with Vertical(id="proj-detail"):
                        with Horizontal(classes="btnrow"):
                            yield Button("Open", id="btn-go", variant="primary", tooltip="Go to this project (Enter)")
                            yield Button("☑ Sidebar", id="btn-sidebar",
                                         tooltip="Show or hide this project in herdr's sidebar (Space)")
                            yield Button("Resume", id="btn-resume", tooltip="Resume one of its sessions (r)")
                            yield Button("+ New", id="btn-new", tooltip="Start a new agent here: pick a CLI")
                            yield Button("▦", id="btn-layout", tooltip="Restore the newest saved layout (l)")
                        with Horizontal(id="new-row"):
                            for cli in installed_clis():
                                key = CLI_NEW_KEY.get(cli)
                                yield Button(Text(cli, style=CLI_STYLE.get(cli, "")), id=f"new-{cli}",
                                             tooltip=f"New {cli} agent here" + (f" ({key})" if key else ""))
                        yield VerticalScroll(Static(id="proj-info"))
            with TabPane("Agents", id="agents"):
                with Horizontal(id="agent-bar", classes="btnrow"):
                    for label, f in (("All", ""), ("⚠ Blocked", "blocked"), ("✔ Done", "done"),
                                     ("◐ Working", "working"), ("○ Idle", "idle"), ("↗ Elsewhere", "outside")):
                        yield Button(label, id=f"flt-{f or 'all'}")
                yield ClickTwiceTable(id="agent-table")
                with Vertical(id="agent-detail"):
                    yield VerticalScroll(Static(id="agent-preview"))
                    with Horizontal(id="agent-send", classes="btnrow"):
                        yield Input(placeholder="Message…", id="agent-msg")
                        yield Button("Send", id="send-msg", variant="primary")
                        yield Button("⏎", id="key-enter", tooltip="Press Enter in the agent (accept)")
                        yield Button("Esc", id="key-esc", tooltip="Press Esc in the agent (cancel)")
                        yield Button("^C", id="key-ctrl_c", tooltip="Interrupt the agent")
                        yield Button("⚑ Next", id="btn-attention", tooltip="Jump to the next agent that needs you (g)")
            with TabPane("Layout", id="panes"):
                with Horizontal(id="shape-bar"):
                    for kind, label in arrange.SHAPES.items():
                        yield Button(label, id=f"shape-{kind}", tooltip=f"Arrange this tab's panes: {label[2:].lower()}")
                    yield Button("＋ New tab", id="presets-toggle", tooltip="Open a new tab with a ready-made layout")
                with Horizontal(id="preset-row"):
                    for i, name in enumerate(panes.PRESETS):
                        yield Button(name, id=f"preset-{i}")
                with Horizontal():
                    with Vertical():
                        yield LayoutMap(id="pane-map")
                        yield Input(placeholder="Pane name (empty clears)", id="pane-rename")
                        with Horizontal(id="saved-row"):
                            yield Input(placeholder="Save this tab as layout…", id="layout-name")
                            yield Button("💾", id="layout-save", tooltip="Save this tab's layout for the project")
                            yield Button("▦", id="layout-restore", tooltip="Restore the selected saved layout")
                            yield Button("🗑", id="layout-delete", tooltip="Delete the selected saved layout")
                        yield ClickTwiceTable(id="saved-table")
                    with Vertical(id="pane-tools"):
                        yield Static("", id="pane-selected")
                        with Horizontal():
                            yield Static("Pane", classes="rowlabel")
                            yield Button("⇥", id="pop-focus", variant="primary", tooltip="Go to pane (Enter / double-click)")
                            yield Button("◫", id="pop-split-right", tooltip="Split right (v)")
                            yield Button("⊟", id="pop-split-down", tooltip="Split down (s)")
                            yield Button("⛶", id="pop-zoom", tooltip="Zoom (z)")
                        with Horizontal():
                            yield Static("Swap", classes="rowlabel")
                            for d, a in (("left", "←"), ("up", "↑"), ("down", "↓"), ("right", "→")):
                                yield Button(a, id=f"pop-swap-{d}", tooltip=f"Swap with the pane {d} (Shift+arrow)")
                        with Horizontal():
                            yield Static("Size", classes="rowlabel")
                            for d, a in (("left", "◂"), ("up", "▴"), ("down", "▾"), ("right", "▸")):
                                yield Button(a, id=f"pop-resize-{d}", tooltip=f"Resize {d} (Ctrl+arrow)")
                        with Horizontal():
                            yield Static("More", classes="rowlabel")
                            yield Button("=", id="pop-equalize", tooltip="Even out all splits (=)")
                            yield Button("↦", id="pop-newtab", tooltip="Move pane to a new tab (t)")
                            yield Button("✎", id="pop-rename", tooltip="Rename pane (n)")
                            yield Button("✕", id="pop-close", variant="error", tooltip="Close pane (Del, twice)")
            with TabPane("Resume", id="resume"):
                with Horizontal(id="resume-bar", classes="btnrow"):
                    yield Input(placeholder="Search sessions…", id="resume-search")
                    yield Button("This project", id="btn-scope", tooltip="This project ↔ all projects (p)")
                with Horizontal(id="resume-cli"):
                    yield Button("All", id="clif-all")
                    for cli in sorted({c for c in CLI_STYLE if c != "cursor-agent"}, key=cli_rank):
                        yield Button(cli, id=f"clif-{cli}")
                yield ClickTwiceTable(id="resume-table")
            with TabPane("Recent", id="recent"):
                yield ClickTwiceTable(id="recent-table")
            with TabPane("Keys", id="keys"):
                yield VerticalScroll(Static(id="keys-body"))
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#proj-table", DataTable).add_columns("", "Project", "Agents", "Last")
        self.query_one("#agent-table", DataTable).add_columns("", "CLI", "Agent", "Project", "Doing")
        self.query_one("#resume-table", DataTable).add_columns("When", "CLI", "Project", "Session", "")
        self.query_one("#recent-table", DataTable).add_columns("When", "Project", "Pane", "")
        self.query_one("#saved-table", DataTable).add_columns("Saved layout", "Panes", "Saved")
        self.render_keys()
        self.load_world()
        self.focus_table()

    # ---- data -----------------------------------------------------------------------------
    @work(thread=True, exclusive=True)
    def load_world(self) -> None:
        world = model.build()
        self.call_from_thread(self.apply_world, world)

    def apply_world(self, world: World) -> None:
        self.world = world
        self.current_project = world.project_of_workspace(world.focused_workspace)
        if self.selected_key is None and self.current_project:
            self.selected_key = self.current_project.root
        total = model.Counter(a.status for a in world.agents)
        outside = sum(1 for a in world.agents if not a.in_herdr)
        subs = sum(len(a.subagents) for a in world.agents)
        where = self.current_project.label if self.current_project else "—"
        bar = Text.assemble(("▣ ", "cyan"), (where, "bold cyan"), "    ", summarize(total) or "no agents")
        if outside:
            bar.append(f"    ↗{outside} elsewhere", style="magenta")
        if subs:
            bar.append(f"    ↳{subs} sub-agents", style="yellow")
        if world.error:
            bar.append(f"    herdr unreachable", style="red")
        self.query_one("#topbar", Static).update(bar)
        self.fill_projects()
        self.fill_agents()
        self.fill_resume()
        self.set_hint()
        self.load_panes()
        self.fill_recent()
        self.fill_saved()

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
        self.call_from_thread(self.fill_recent)
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
            info.append(f"{b.label[:26]}\n", style="bold cyan")
            info.append(b.pane_id + ("  ⛶" if lay.zoomed else ""), style="dim")
        self.query_one("#pane-selected", Static).update(info)

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
            self.call_from_thread(self.exit, msg)
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
            self.call_from_thread(self.exit, msg)
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

    # ---- recent places ----------------------------------------------------------------------
    def fill_recent(self) -> None:
        t = self.query_one("#recent-table", DataTable)
        t.clear()
        if not self.world:
            return
        names = {a.pane_id: a for a in self.world.agents if a.pane_id}
        mirrors = {a.mirror_pane: a for a in self.world.agents if a.mirror_pane}
        info = getattr(self, "_pane_info", {})
        for e in history.recent(set(info)):
            pid = e["pane_id"]
            p = info[pid]
            a = names.get(pid) or mirrors.get(pid)
            proj = self.world.project_of_workspace(p.get("workspace_id", ""))
            label = (("↗ " if pid in mirrors else "") + a.display) if a else (
                p.get("label") or os.path.basename((p.get("cwd") or "").rstrip("\\/")) or pid)
            status = Text(model.STATE_ICON.get(a.status, ""), style=STATE_STYLE.get(a.status, "")) if a else ""
            t.add_row(age(e["at"]), (proj.label if proj else "?")[:30], label[:60], status, key=pid)

    @on(DataTable.RowSelected, "#recent-table")
    def _recent_sel(self, ev: DataTable.RowSelected) -> None:
        if not ev.data_table.fresh_highlight():
            self.action_open()

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
            return
        head = f"{a.display}  ·  {a.cli}  ·  {a.status}  ·  {a.project.label}"
        out.append(head + "\n", style="bold cyan")
        if sub >= 0 and sub < len(a.subagents):
            sa = a.subagents[sub]
            out.append(f"↳ sub-agent {sa.name} ({sa.kind}, {sa.model})\n{sa.description}\n{sa.activity}\n")
        elif a.in_herdr:
            try:
                raw = model.herdr.run("agent", "read", a.pane_id, "--source", "recent-unwrapped",
                                      "--lines", "14").get("raw", "")
            except Exception as e:
                raw = f"(could not read: {e})"
            lines = [l for l in raw.splitlines() if l.strip() and not set(l.strip()) <= set("─━═-")]
            out.append("\n".join(lines[-12:]))
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
                       style="dim magenta")
        self.call_from_thread(self.query_one("#agent-preview", Static).update, out)

    def action_message(self) -> None:
        self.query_one("#agent-msg", Input).focus()

    @on(Input.Submitted, "#agent-msg")
    def _msg(self, ev: Input.Submitted) -> None:
        self.send_to_agent(ev.value)
        ev.input.value = ""

    def send_to_agent(self, text: str = "", key: str = "") -> None:
        a, _ = self.agent_selected()
        if a is None:
            return
        if not a.in_herdr:
            self.notify(f"'{a.display}' runs in another terminal window, so herdr can't type into it. "
                        "Use that window, or resume it here after it exits.", severity="warning", timeout=8)
            return
        self.run_send(a.pane_id, a.display, text, key)

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
        self.finish(attention.next_)

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
        for v in self.world.projects:
            here = self.current_project and v.project.root == self.current_project.root
            box = Text("☑", style="bold green") if v.in_sidebar else Text("☐", style="dim")
            name = Text(v.project.name, style="bold cyan" if here else ("bold" if v.in_sidebar else ""))
            if here:
                name.append("  ◀ here", style="cyan")

            t.add_row(box, name, summarize(v.counts()) or "",
                      age(v.last_activity) if v.last_activity else "", key=v.project.root)
            if v.project.root == self.selected_key:
                target_row = row
            row += 1
            # worktrees with something running or open; the detail panel lists all of them
            show = [w for w in v.worktrees.values() if w.agents or w.workspace]
            show.sort(key=lambda w: (not w.agents, -max((s.mtime for s in w.sessions), default=0)))
            for wt in show[:12]:
                k = f"{v.project.root}{WT_SEP}{wt.label}"
                names = sorted({a.display for a in wt.agents})
                label = Text(f"  ⎇ {', '.join(names) or wt.label}"[:40], style="bold" if wt.agents else "dim")
                last = max((s.mtime for s in wt.sessions), default=0)
                t.add_row("", label, summarize(model.Counter(a.status for a in wt.agents)) or "",
                          age(last) if last else "", key=k)
                if k == self.selected_key:
                    target_row = row
                row += 1
        if t.row_count:
            t.move_cursor(row=target_row)
        self.show_project_detail()

    def show_project_detail(self) -> None:
        v, wt = self.selected()
        info = self.query_one("#proj-info", Static)
        if not v:
            info.update("")
            return
        agents = wt.agents if wt else v.agents
        sessions = wt.sessions if wt else v.sessions
        out = Text()
        out.append(v.project.name, style="bold cyan")
        if wt:
            out.append(f" ⎇ {wt.label}", style="bold magenta")
        out.append(f"\n{(wt.path if wt else v.project.path) or v.project.root}\n\n", style="dim")
        if agents:
            for a in sorted(agents, key=lambda a: model.STATE_ORDER.get(a.status, 9)):
                out.append_text(state_text(a.status))
                out.append(f"  {a.display}", style="bold")
                if not a.in_herdr:
                    out.append("  ↗", style="magenta")
                if a.activity:
                    out.append(f"\n     {a.activity[:60]}", style="dim")
                for sa in a.subagents:
                    out.append(f"\n     ↳ {sa.name}", style="yellow")
                    if sa.activity:
                        out.append(f"  {sa.activity[:44]}", style="dim")
                out.append("\n")
            out.append("\n")
        if not wt and v.worktrees:
            quiet = [w for w in v.worktrees.values() if not w.agents]
            if quiet:
                out.append(f"⎇ {len(quiet)} idle worktrees\n\n", style="dim")
        saved_layouts = layouts.saved(v.project)
        if saved_layouts and not wt:
            out.append("▦ " + ", ".join(saved_layouts) + "\n\n", style="dim")
        for s_ in sessions[:6]:
            live = self.world.live_sessions.get(s_.id)
            out.append(f"{age(s_.mtime):>4}  ", style="dim")
            out.append(cli_tag(s_.cli))
            out.append(" ")
            out.append(s_.title[:48], style=CLI_STYLE.get(s_.cli, ""))
            if live:
                out.append("  ●" if live.in_herdr else "  ↗", style="green" if live.in_herdr else "magenta")
            out.append("\n")
        info.update(out)

    @on(DataTable.RowHighlighted, "#proj-table")
    def _proj_hl(self, ev: DataTable.RowHighlighted) -> None:
        if ev.row_key is not None and ev.row_key.value:
            self.selected_key = ev.row_key.value
            self.show_project_detail()
            if self.only_project:
                self.fill_resume()

    @on(DataTable.RowSelected, "#proj-table")
    def _proj_sel(self, ev: DataTable.RowSelected) -> None:
        if not ev.data_table.fresh_highlight():
            self.action_open()

    @on(ClickTwiceTable.BoxClicked)
    def _box(self, ev: ClickTwiceTable.BoxClicked) -> None:
        self.selected_key = ev.row_key
        self.action_toggle_sidebar()

    # ---- agents ---------------------------------------------------------------------------
    def fill_agents(self) -> None:
        t = self.query_one("#agent-table", DataTable)
        t.clear()
        self.agent_rows = {}
        rows = []
        for a in self.world.agents:
            if self.agent_filter == "outside":
                if a.in_herdr:
                    continue
            elif self.agent_filter and a.status != self.agent_filter:
                continue
            rows.append(a)
        rows.sort(key=lambda a: (model.STATE_ORDER.get(a.status, 9), a.project.name.lower(), a.project.worktree))
        for a in rows:
            here = self.current_project and a.project.root == self.current_project.root
            who = Text(a.display[:34], style="bold")
            if not a.in_herdr:
                who.append("  ↗", style="magenta")
            proj = Text(a.project.label[:30], style="cyan" if here else "")
            t.add_row(Text(STATE_ICON.get(a.status, "?"), style=STATE_STYLE.get(a.status, "")), cli_tag(a.cli), who, proj,
                      (a.activity or a.title)[:70], key=a.key)
            self.agent_rows[a.key] = (a, -1)
            for i, sa in enumerate(a.subagents):
                k = f"{a.key}#sub{i}"
                t.add_row(Text("↳", style="yellow"), "", Text(f"  {sa.name}", style="yellow"),
                          Text(sa.kind or "", style="dim"), (sa.activity or sa.description)[:70], key=k)
                self.agent_rows[k] = (a, i)
        for b in self.query("#agent-bar Button"):
            b.variant = "primary" if b.id == f"flt-{self.agent_filter or 'all'}" else "default"

    @on(DataTable.RowSelected, "#agent-table")
    def _agent_sel(self, ev: DataTable.RowSelected) -> None:
        if not ev.data_table.fresh_highlight():
            self.action_open()

    # ---- resume ---------------------------------------------------------------------------
    def fill_resume(self) -> None:
        if not self.world:
            return
        t = self.query_one("#resume-table", DataTable)
        q = self.query_one("#resume-search", Input).value.strip().lower()
        v, wt = self.selected()
        t.clear()
        in_scope = [s for s in self.world.sessions
                    if not (self.only_project and v)
                    or (s.project.root == v.project.root and not (wt and s.project.worktree != wt.label))]
        self.sync_cli_chips(in_scope)
        for s in in_scope:  # already newest first
            if self.cli_filter and s.cli != self.cli_filter:
                continue
            if q and not all(tok in " ".join((s.title, s.last_prompt, s.project.label, s.branch, s.cli)).lower()
                             for tok in q.split()):
                continue
            live = self.world.live_sessions.get(s.id)
            mark = ""
            if live:
                mark = Text("● open", style="green") if live.in_herdr else Text("↗ elsewhere", style="magenta")
            t.add_row(age(s.mtime), cli_tag(s.cli), s.project.label[:30],
                      s.title[:80], mark, key=f"{s.cli}:{s.id}")
        scope = self.query_one("#btn-scope", Button)
        name = (v.project.name + (f" ⎇ {wt.label}" if wt else "")) if v else ""
        scope.label = (name[:22] if (self.only_project and v) else "All projects")
        scope.variant = "primary" if self.only_project else "default"

    def sync_cli_chips(self, in_scope) -> None:
        """'All · claude 17 · pi 44 …' above the Resume list: only CLIs that have sessions in
        scope, and no row at all when there is just one."""
        counts: dict[str, int] = {}
        for s in in_scope:
            counts[s.cli] = counts.get(s.cli, 0) + 1
        clis = sorted(counts, key=cli_rank)
        if self.cli_filter not in counts:
            self.cli_filter = ""
        bar = self.query_one("#resume-cli", Horizontal)
        bar.display = len(clis) > 1
        for b in bar.query(Button):
            c = (b.id or "")[5:]
            b.display = c == "all" or c in counts
            if c == "all":
                b.label = f"All {len(in_scope)}"
            else:
                b.label = Text(f"{c} {counts.get(c, 0)}", style=CLI_STYLE.get(c, ""))
            b.variant = "primary" if (c == "all" and not self.cli_filter) or c == self.cli_filter else "default"

    @on(Input.Changed, "#resume-search")
    def _search(self) -> None:
        self.fill_resume()

    @on(Input.Submitted, "#resume-search")
    def _search_done(self) -> None:
        self.query_one("#resume-table", DataTable).focus()

    @on(DataTable.RowSelected, "#resume-table")
    def _resume_sel(self, ev: DataTable.RowSelected) -> None:
        if not ev.data_table.fresh_highlight():
            self.action_open()

    # ---- keys -----------------------------------------------------------------------------
    def render_keys(self) -> None:
        prefix, groups, custom = keymap.effective()
        out = Text()
        out.append(f"Prefix {keymap.pretty(prefix, prefix)}", style="bold")
        out.append("  ·  press it, release, then the key\n\n", style="dim")

        def row(keys_: str, what: str, dim: bool = False) -> None:
            out.append(f"  {keys_:<32}", style="dim" if dim else "bold yellow")
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
        self.query_one("#keys-body", Static).update(out)

    # ---- actions --------------------------------------------------------------------------
    def active_tab(self) -> str:
        return self.query_one("#tabs", TabbedContent).active

    def focus_table(self) -> None:
        tab = self.active_tab()
        if tab == "panes":
            self.query_one("#pane-map", LayoutMap).focus()
            self.load_panes()
            return
        tid = {"projects": "#proj-table", "agents": "#agent-table", "resume": "#resume-table",
               "recent": "#recent-table"}.get(tab)
        if tid:
            self.query_one(tid, DataTable).focus()

    def set_hint(self) -> None:
        pass  # the footer shows the keys that work on the current tab; tooltips explain buttons

    @on(TabbedContent.TabActivated)
    def _tab_changed(self) -> None:
        self.set_hint()
        self.focus_table()
        self.refresh_bindings()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        tab = self.active_tab() if self.is_mounted else self.start_tab
        typing = isinstance(self.focused, Input)
        if typing and action in ("new", "resume_project", "toggle_project", "filter", "tab", "quit",
                                 "toggle_sidebar"):
            return False
        if action in ("new", "resume_project", "toggle_sidebar"):
            return tab == "projects"
        if action in ("toggle_project", "search"):
            return tab == "resume"
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
        if action == "search":
            return tab == "resume"
        if action == "sidebar_width":
            return not typing
        if action == "open":
            return tab in ("projects", "agents", "resume", "panes", "recent")
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
        if isinstance(self.focused, Input) and self.focused.value:
            self.focused.value = ""
            return
        self.exit()

    def action_search(self) -> None:
        self.query_one("#resume-search", Input).focus()

    def action_toggle_project(self) -> None:
        self.only_project = not self.only_project
        self.fill_resume()

    def action_resume_project(self) -> None:
        self.only_project = True
        self.action_tab("resume")
        self.fill_resume()

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
                                                    settings.load().launch.get(f"{cli}_new", cli)))
        else:
            self.finish(lambda: launch.new_agent(self.world, v.project, cli))

    def action_open(self) -> None:
        if not self.world:
            return
        tab = self.active_tab()
        if tab == "panes":
            self.action_pane_op("focus")
            return
        if tab == "recent":
            t = self.query_one("#recent-table", DataTable)
            if t.row_count:
                pid = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
                self.finish(lambda: panes.focus(pid))
            return
        if tab == "projects":
            v, wt = self.selected()
            if v and wt:
                if not wt.exists:
                    self.notify(f"{wt.label} was removed from disk", severity="warning")
                    return
                p = model.projects.Project(v.project.root, v.project.name, wt.label, v.project.path, wt.path)
                self.finish(lambda: launch.goto_project(self.world, p, wt.path))
            elif v:
                self.finish(lambda: launch.goto_project(self.world, v.project))
        elif tab == "agents":
            t = self.query_one("#agent-table", DataTable)
            if not t.row_count:
                return
            k = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
            a, _ = self.agent_rows.get(k, (None, -1))
            if not a:
                return
            if a.in_herdr:
                self.finish(lambda: (model.herdr.focus_agent(a.pane_id), f"→ {a.pane_id}")[1])
            elif a.mirror_pane:
                self.finish(lambda: panes.focus(a.mirror_pane))
            else:
                self.notify(f"'{a.name}' runs in another terminal window (pid {a.pid}), so herdr can't "
                            "jump to it. To bring it here: exit it there, then resume it from tab 3.",
                            title="Outside herdr", timeout=10)
        elif tab == "resume":
            t = self.query_one("#resume-table", DataTable)
            if t.row_count:
                k = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
                s = next((s for s in self.world.sessions if f"{s.cli}:{s.id}" == k), None)
                if s:
                    self.finish(lambda: launch.resume(self.world, s))

    def finish(self, fn) -> None:
        try:
            msg = fn()
        except Exception as e:  # show it, keep the Navigator open
            self.notify(str(e)[:300], title="herdr refused", severity="error", timeout=8)
            return
        if msg and msg.startswith("✗"):
            self.notify(msg, severity="warning", timeout=10)
            return
        self.exit(msg)

    # ---- buttons --------------------------------------------------------------------------
    @on(Button.Pressed)
    def _button(self, ev: Button.Pressed) -> None:
        bid = ev.button.id or ""
        actions = {
            "btn-go": self.action_open, "btn-resume": self.action_resume_project,
            "btn-new": lambda: self.query_one("#new-row").toggle_class("show"),
            "btn-scope": self.action_toggle_project, "btn-sidebar": self.action_toggle_sidebar,
        }
        if bid in actions:
            actions[bid]()
        elif bid.startswith("pop-"):
            parts = bid[4:].split("-")
            if parts[0] == "rename":
                self.action_pane_rename()
            else:
                self.action_pane_op(parts[0], parts[1] if len(parts) > 1 else "")
        elif bid == "send-msg":
            self.send_to_agent(self.query_one("#agent-msg", Input).value)
            self.query_one("#agent-msg", Input).value = ""
        elif bid.startswith("key-"):
            self.send_to_agent(key=bid[4:].replace("_", "+"))
        elif bid == "btn-attention":
            self.action_attention()
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
        elif bid == "btn-layout":
            self.action_restore_layout()
        elif bid.startswith("shape-"):
            self.run_shape(bid[6:])
        elif bid == "presets-toggle":
            self.query_one("#preset-row").toggle_class("show")
        elif bid.startswith("preset-"):
            self.action_pane_op("preset", list(panes.PRESETS)[int(bid[7:])])
        elif bid.startswith("new-"):
            self.query_one("#new-row").remove_class("show")
            self.action_new(bid[4:])
        elif bid.startswith("clif-"):
            c = bid[5:]
            self.cli_filter = "" if c == "all" else c
            self.fill_resume()
        elif bid.startswith("flt-"):
            f = bid[4:]
            self.action_filter("" if f == "all" else f)


def main() -> None:
    tab = sys.argv[1] if len(sys.argv) > 1 else "projects"
    msg = Navigator(tab).run()
    if msg:
        print(msg)


if __name__ == "__main__":
    main()
