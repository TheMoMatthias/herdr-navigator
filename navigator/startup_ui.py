"""The Navigator's Startup tab: what reopens at logon, sign-in per CLI, relaunching sessions.

Rows are grouped by project. The box in front of a row is its tick: ☑ reopens at logon, ☐ does
not. A bright box is your own choice, a dim one the auto-tick's (newest sessions per lane).
Click the box (or Space) to tick/untick, double-click (or Enter) to open a session now,
right-click (or .) for everything else.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, OptionList, Select, Static, Switch
from textual.widgets.option_list import Option

from . import accounts, autostart, launch, model, restore, settings, startup
from .model import age

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
RECENT_DAYS = 7
UNTICKED_PER_PROJECT = 8


def background(*args: str) -> None:
    """Run `python -m navigator.<args>` detached, so it outlives the Navigator popup."""
    root = Path(__file__).resolve().parent.parent
    py = root / ".venv" / ("Scripts/pythonw.exe" if os.name == "nt" else "bin/python")
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONUTF8": "1"}
    subprocess.Popen([str(py), "-m", *args], cwd=str(root), env=env, creationflags=_DETACHED | _NO_WINDOW,
                     close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---- pop-ups ----------------------------------------------------------------------------------

class ContextMenu(ModalScreen):
    """A small menu at the mouse. Returns the chosen item's id, or None."""

    DEFAULT_CSS = """
    ContextMenu { align: left top; background: $background 30%; }
    ContextMenu OptionList { width: auto; min-width: 26; max-width: 44; height: auto; max-height: 16;
                             border: round $primary; background: $panel; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Close")]

    def __init__(self, items: list[tuple[str, str]], at: tuple[int, int]) -> None:
        super().__init__()
        self.items = items
        self.at = at

    def compose(self) -> ComposeResult:
        yield OptionList(*[Option(label, id=i) for i, label in self.items])

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        x, y = self.at
        w, h = self.app.size
        ol.styles.offset = (max(0, min(x, w - 46)), max(0, min(y, h - len(self.items) - 3)))
        ol.focus()

    @on(OptionList.OptionSelected)
    def _pick(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(ev.option.id)

    def on_click(self, event: events.Click) -> None:
        if not self.query_one(OptionList).region.contains(event.screen_x, event.screen_y):
            self.dismiss(None)


class LaunchOptions(ModalScreen):
    """How one session is relaunched: model, effort, permission mode, Remote Control, extra args."""

    DEFAULT_CSS = """
    LaunchOptions { align: center middle; background: $background 50%; }
    #lo { width: 64; height: auto; border: round $primary; background: $panel; padding: 1 2; }
    #lo Horizontal { height: 3; }
    #lo .lbl { width: 18; margin-top: 1; color: $text-muted; }
    #lo Input, #lo Select { width: 1fr; }
    #lo-buttons { margin-top: 1; }
    #lo-buttons Button { margin-right: 1; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, cli: str, title: str, prefs: dict) -> None:
        super().__init__()
        self.cli, self.title_, self.prefs = cli, title, prefs

    def compose(self) -> ComposeResult:
        p = self.prefs
        with Vertical(id="lo"):
            yield Static(Text.assemble(("Launch options  ", "bold"), (self.title_[:40], "cyan")))
            if self.cli == "claude":
                rc_default = bool(settings.load().restore.get("claude_remote_control", False))
                with Horizontal():
                    yield Static("Model", classes="lbl")
                    yield Input(p.get("model", ""), placeholder="default (e.g. opus, sonnet)", id="lo-model")
                with Horizontal():
                    yield Static("Effort", classes="lbl")
                    yield Select([(e, e) for e in startup.EFFORTS[1:]], value=p.get("effort") or Select.NULL,
                                 prompt="default", id="lo-effort")
                with Horizontal():
                    yield Static("Permission mode", classes="lbl")
                    yield Select([(m, m) for m in startup.PERMISSION_MODES[1:]],
                                 value=p.get("permission_mode") or Select.NULL, prompt="default", id="lo-perm")
                with Horizontal():
                    yield Static("Remote Control", classes="lbl")
                    yield Switch(value=bool(p.get("remote_control", rc_default)), id="lo-rc")
            with Horizontal():
                yield Static("Extra arguments", classes="lbl")
                yield Input(p.get("args", ""), placeholder="appended to the resume command", id="lo-args")
            with Horizontal(id="lo-buttons"):
                yield Button("Save", variant="primary", id="lo-save")
                yield Button("Reset", id="lo-reset", tooltip="Back to the defaults")
                yield Button("Cancel", id="lo-cancel")

    @on(Button.Pressed)
    def _btn(self, ev: Button.Pressed) -> None:
        if ev.button.id == "lo-cancel":
            self.dismiss(None)
        elif ev.button.id == "lo-reset":
            self.dismiss({})
        elif ev.button.id == "lo-save":
            out = {"args": self.query_one("#lo-args", Input).value.strip()}
            if self.cli == "claude":
                eff = self.query_one("#lo-effort", Select).value
                perm = self.query_one("#lo-perm", Select).value
                out.update(model=self.query_one("#lo-model", Input).value.strip(),
                           effort="" if eff is Select.NULL else eff,
                           permission_mode="" if perm is Select.NULL else perm,
                           remote_control=self.query_one("#lo-rc", Switch).value)
            self.dismiss(out)


# ---- the table --------------------------------------------------------------------------------

class StartTable(DataTable):
    """Click the box column to tick, a row twice (or Enter) to open, right-click for the menu."""

    class Box(Message):
        def __init__(self, key: str) -> None:
            super().__init__()
            self.key = key

    class Menu(Message):
        def __init__(self, key: str, at: tuple[int, int]) -> None:
            super().__init__()
            self.key = key
            self.at = at

    class Open(Message):
        def __init__(self, key: str) -> None:
            super().__init__()
            self.key = key

    BINDINGS = [("j", "cursor_down"), ("k", "cursor_up"),
                Binding("space", "box", "Tick"), Binding("enter", "select_cursor", "Open"),
                Binding("period", "menu", "Menu"),
                Binding("shift+f10", "menu", "Menu", show=False)]

    def action_box(self) -> None:
        if self.key_at_cursor():
            self.post_message(self.Box(self.key_at_cursor()))

    def action_menu(self) -> None:
        if self.key_at_cursor():
            self.post_message(self.Menu(self.key_at_cursor(), None))

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self._last = (0.0, "")

    def key_at_cursor(self) -> str:
        if not self.row_count:
            return ""
        return self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value or ""

    async def _on_click(self, event: events.Click) -> None:
        meta = event.style.meta if event.style else {}
        row = meta.get("row", -1)
        if row is None or row < 0:
            await super()._on_click(event)
            return
        self.move_cursor(row=row)
        key = self.key_at_cursor()
        event.stop()
        if event.button == 3:
            self.post_message(self.Menu(key, (event.screen_x, event.screen_y)))
        elif meta.get("column") == 0:
            self.post_message(self.Box(key))
        else:
            t, k = self._last
            if k == key and time.monotonic() - t < 0.5:
                self.post_message(self.Open(key))
            self._last = (time.monotonic(), key)


class StartupPane(Vertical):
    DEFAULT_CSS = """
    StartupPane .btnrow { height: 3; }
    StartupPane .btnrow Button { margin: 0 1 0 0; min-width: 6; }
    #start-sum { height: 1; padding: 0 1; color: $text-muted; }
    #acct-box, #relaunch-box { height: auto; display: none; border: round $primary 50%; padding: 0 1; }
    #acct-box.show, #relaunch-box.show { display: block; }
    #acct-box Horizontal, #relaunch-box Horizontal { height: 3; }
    .acct-name { width: 10; margin-top: 1; }
    .acct-who { width: 1fr; margin-top: 1; color: $text-muted; }
    #relaunch-text { height: auto; margin: 1 0 0 0; }
    #start-table { height: 1fr; }
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.world: model.World | None = None
        self.groups: list[startup.ProjectRows] = []
        self.rows_by_key: dict[str, startup.Row] = {}
        self.show_all = False
        self.relaunch_cli: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="btnrow"):
            yield Button("⏻ At logon: off", id="st-autostart",
                         tooltip="Reopen the ticked sessions in herdr every time you log on")
            yield Button("▶ Open ticked", id="st-open", variant="primary",
                         tooltip="Open every ticked session that is not running yet, now")
            yield Button("↻ Relaunch", id="st-relaunch", tooltip="Restart open sessions in place (after a sign-in)")
            yield Button("🔑 Accounts", id="st-accounts", tooltip="Sign in or out per CLI")
            yield Button("All", id="st-all", tooltip="Show every session, not just recent and ticked ones")
        with Vertical(id="acct-box"):
            for cli in accounts.clis():
                with Horizontal():
                    yield Static(Text(cli, style="bold"), classes="acct-name")
                    yield Static("…", id=f"acct-who-{cli}", classes="acct-who")
                    yield Button("Sign in", id=f"acct-in-{cli}", variant="primary",
                                 tooltip="Opens the CLI's sign-in in a new tab; relaunch is offered when done")
                    yield Button("Sign out", id=f"acct-out-{cli}")
        with Vertical(id="relaunch-box"):
            yield Static("", id="relaunch-text")
            with Horizontal():
                yield Button("↻ Relaunch", id="rl-go", variant="primary")
                yield Button("Include busy", id="rl-busy", tooltip="Also restart sessions that are working right now")
                yield Button("Cancel", id="rl-cancel")
        yield Static("", id="start-sum")
        yield StartTable(id="start-table")

    def on_mount(self) -> None:
        self.query_one(StartTable).add_columns("", "CLI", "Session", "Lane", "Last", "Now")
        self.sync_autostart()

    # ---- filling ------------------------------------------------------------------------------
    def show(self, world: model.World) -> None:
        from .app import cli_tag  # shared look with the other tabs
        self.world = world
        self.groups = startup.plan(world.sessions)
        t = self.query_one(StartTable)
        keep = t.key_at_cursor()
        t.clear()
        self.rows_by_key = {}
        cutoff = time.time() - RECENT_DAYS * 86400
        n_ticked = n_open = 0
        for g in self.groups:
            ticked = len(g.ticked)
            recent = [r for r in g.rows if r.ticked or r.session.mtime >= cutoff]
            if not (recent or self.show_all):
                continue
            box = Text("☑" if g.on else "☐", style="bold" if g.pinned else "dim")
            label = Text(g.project.name, style="bold cyan" if g.on else "dim")
            label.append(f"   {ticked} of {len(g.rows)}" if g.on else "   not restored", style="dim")
            if not g.auto:
                label.append("  · auto-tick off", style="dim")
            t.add_row(box, "", label, "", "", "", key=f"P|{g.project.root}")
            if not g.on and not self.show_all:
                continue
            rows = g.rows if self.show_all else (
                [r for r in g.rows if r.ticked]
                + [r for r in g.rows if not r.ticked and r.session.mtime >= cutoff][:UNTICKED_PER_PROJECT])
            rows.sort(key=lambda r: -r.session.mtime)
            for r in rows:
                s = r.session
                k = f"S|{startup.skey(s)}"
                self.rows_by_key[k] = r
                live = world.live_sessions.get(s.id)
                ticked_here = r.ticked and g.on
                n_ticked += ticked_here
                now = ""
                if live:
                    n_open += ticked_here
                    now = Text("● open", style="green") if live.in_herdr else Text("↗ elsewhere", style="magenta")
                    if live.question:
                        now = Text("❓ asks", style="bold red")
                box = Text("☑" if r.ticked else "☐", style=("bold" if r.pinned else "dim") + (" green" if ticked_here else ""))
                title = Text("  " + s.title[:70])
                if r.prefs:
                    title.append("  ⚙", style="dim")
                lane = Text(f"⎇ {s.project.worktree}"[:18], style="yellow") if s.project.worktree else Text("main", style="dim")
                t.add_row(box, cli_tag(s.cli), title, lane, age(s.mtime), now, key=k)
        if keep:
            try:
                t.move_cursor(row=t.get_row_index(keep))
            except Exception:
                pass
        cap = int(settings.load().restore.get("max_sessions", 30))
        sumtext = Text.assemble((f"{n_ticked} ticked", "bold"), f"  ·  {n_open} already open  ·  ",
                                (f"{max(0, min(n_ticked, cap) - n_open)} would open", "bold green"))
        sumtext.append("     ☑ bright = your choice · dim = auto (newest per lane)", style="dim")
        self.query_one("#start-sum", Static).update(sumtext)

    def sync_autostart(self) -> None:
        on_ = autostart.installed()
        b = self.query_one("#st-autostart", Button)
        b.label = "⏻ At logon: on" if on_ else "⏻ At logon: off"
        b.variant = "success" if on_ else "default"

    @work(thread=True, group="accounts")
    def load_accounts(self) -> None:
        for cli in accounts.clis():
            who = accounts.status(cli)
            self.app.call_from_thread(self.query_one(f"#acct-who-{cli}", Static).update, who)

    # ---- relaunch sheet -----------------------------------------------------------------------
    def open_relaunch(self, cli: str = "") -> None:
        self.relaunch_cli = cli
        t = restore.relaunch_targets(cli, self.world)
        what = cli or "every CLI"
        txt = Text.assemble(("Relaunch ", "bold"), (what, "bold cyan"), "  —  restarts each session in its own pane\n")
        txt.append(f"  ↻ {len(t['restart'])} restart: ", style="green")
        txt.append(", ".join(a.display for a in t["restart"])[:300] or "none")
        if t["busy"]:
            txt.append(f"\n  ◐ {len(t['busy'])} working, left alone unless you include busy: ", style="yellow")
            txt.append(", ".join(a.display for a in t["busy"])[:200])
        if t["elsewhere"]:
            txt.append(f"\n  ↗ {len(t['elsewhere'])} in other terminals, restart them there: ", style="magenta")
            txt.append(", ".join(a.display for a in t["elsewhere"])[:200])
        self.query_one("#relaunch-text", Static).update(txt)
        self.query_one("#relaunch-box").add_class("show")

    # ---- actions ------------------------------------------------------------------------------
    def current(self) -> str:
        return self.query_one(StartTable).key_at_cursor()

    def toggle(self, key: str) -> None:
        if key.startswith("P|"):
            g = next((g for g in self.groups if g.project.root == key[2:]), None)
            if g:
                startup.set_project(g.project.root, on=not g.on)
        elif key in self.rows_by_key:
            r = self.rows_by_key[key]
            startup.set_tick(key[2:], not r.ticked)
        self.refresh_rows()

    def refresh_rows(self) -> None:
        if self.world:
            self.show(self.world)

    def open_session(self, key: str) -> None:
        r = self.rows_by_key.get(key)
        if r and self.world:
            self.app.finish(lambda: launch.resume(self.world, r.session))

    def menu_items(self, key: str) -> list[tuple[str, str]]:
        if key.startswith("P|"):
            g = next((g for g in self.groups if g.project.root == key[2:]), None)
            if not g:
                return []
            return [("p-toggle", "☐ Don't restore this project" if g.on else "☑ Restore this project"),
                    ("p-auto", "Auto-tick off here" if g.auto else "Auto-tick on here"),
                    ("p-open", f"▶ Open its {len(g.ticked)} ticked now"),
                    ("p-untick", "☐ Untick all its sessions"),
                    ("p-reset", "↺ Back to automatic")]
        r = self.rows_by_key.get(key)
        if not r:
            return []
        live = self.world.live_sessions.get(r.session.id) if self.world else None
        items = []
        if live and live.in_herdr:
            items += [("s-goto", "→ Go to it"), ("s-relaunch", "↻ Relaunch it (restart in place)")]
        elif not live:
            items += [("s-open", "▶ Open it now")]
        items += [("s-tick", "☐ Untick" if r.ticked else "☑ Tick for logon")]
        if r.pinned:
            items += [("s-auto", "↺ Back to automatic")]
        items += [("s-options", "⚙ Launch options…"), ("s-copy", "⧉ Show resume command")]
        return items

    def open_menu(self, key: str, at: tuple[int, int] | None = None) -> None:
        items = self.menu_items(key)
        if not items:
            return
        if at is None:
            t = self.query_one(StartTable)
            at = (t.region.x + 4, t.region.y + 1 + t.cursor_row - int(t.scroll_y) + 1)
        self.app.push_screen(ContextMenu(items, at), lambda choice: self.on_menu(key, choice))

    def on_menu(self, key: str, choice: str | None) -> None:
        if not choice:
            return
        if key.startswith("P|"):
            root = key[2:]
            g = next((g for g in self.groups if g.project.root == root), None)
            if not g:
                return
            if choice == "p-toggle":
                startup.set_project(root, on=not g.on)
            elif choice == "p-auto":
                startup.set_project(root, auto=not g.auto)
            elif choice == "p-untick":
                for r in g.rows:
                    startup.set_tick(startup.skey(r.session), False)
            elif choice == "p-reset":
                data = startup.load()
                data["projects"].pop(root, None)
                for r in g.rows:
                    sd = data["sessions"].get(startup.skey(r.session))
                    if sd:
                        sd.pop("tick", None)
                        if not sd:
                            data["sessions"].pop(startup.skey(r.session), None)
                startup.save(data)
            elif choice == "p-open":
                self.open_rows_bg([r for r in g.ticked if not (self.world and r.session.id in self.world.live_sessions)])
                return
            self.refresh_rows()
            return
        r = self.rows_by_key.get(key)
        if not r:
            return
        s = r.session
        if choice == "s-open":
            self.open_session(key)
        elif choice == "s-goto":
            a = self.world.live_sessions.get(s.id)
            self.app.finish(lambda: (model.herdr.focus_agent(a.pane_id), f"→ {a.pane_id}")[1])
        elif choice == "s-relaunch":
            a = self.world.live_sessions.get(s.id)
            self.relaunch_one(a.pane_id, s.cli, s.id, a.display)
        elif choice == "s-tick":
            startup.set_tick(key[2:], not r.ticked)
            self.refresh_rows()
        elif choice == "s-auto":
            startup.set_tick(key[2:], None)
            self.refresh_rows()
        elif choice == "s-options":
            self.app.push_screen(LaunchOptions(s.cli, s.title, r.prefs),
                                 lambda prefs: self._save_prefs(key, prefs))
        elif choice == "s-copy":
            cmd = startup.launch_command(s.cli, s.id, s.title if s.named else "", r.prefs)
            self.app.copy_to_clipboard(cmd)
            self.app.notify(cmd, title="Resume command (copied)", timeout=12)

    def _save_prefs(self, key: str, prefs: dict | None) -> None:
        if prefs is None:
            return
        startup.set_prefs(key[2:], prefs)
        self.app.notify("Used the next time it is opened or relaunched.", title="Launch options saved")
        self.refresh_rows()

    @work(thread=True, group="relaunch")
    def relaunch_one(self, pane: str, cli: str, sid: str, name: str) -> None:
        msg = restore.relaunch_in_place(pane, cli, sid, name)
        self.app.call_from_thread(self.app.notify, msg, severity="warning" if msg.startswith("✗") else "information")

    def open_rows_bg(self, rows: list[startup.Row]) -> None:
        if not rows:
            self.app.notify("Nothing to open: everything ticked is running.")
            return
        background("navigator.restore", "now")
        self.app.exit(f"▶ opening {len(rows)} sessions in the background (restore.log has the details)")

    @on(StartTable.Box)
    def _box(self, ev: StartTable.Box) -> None:
        self.toggle(ev.key)

    @on(StartTable.Menu)
    def _menu(self, ev: StartTable.Menu) -> None:
        self.open_menu(ev.key, ev.at)

    @on(DataTable.RowSelected, "#start-table")
    def _enter(self, ev: DataTable.RowSelected) -> None:
        k = self.current()
        if k.startswith("S|"):
            self.open_session(k)

    @on(StartTable.Open)
    def _open(self, ev: StartTable.Open) -> None:
        if ev.key.startswith("S|"):
            self.open_session(ev.key)
        else:
            self.toggle(ev.key)

    @on(Button.Pressed)
    def _button(self, ev: Button.Pressed) -> None:
        bid = ev.button.id or ""
        ev.stop()
        if bid == "st-autostart":
            msg = autostart.uninstall() if autostart.installed() else autostart.install()
            self.sync_autostart()
            self.app.notify(msg, severity="error" if msg.startswith("✗") else "information")
        elif bid == "st-open":
            if not self.world:
                return
            rows = [r for r in startup.selected(self.world.sessions) if r.session.id not in self.world.live_sessions]
            self.open_rows_bg(rows)
        elif bid == "st-relaunch":
            self.open_relaunch("")
        elif bid == "st-accounts":
            box = self.query_one("#acct-box")
            box.toggle_class("show")
            if box.has_class("show"):
                self.load_accounts()
        elif bid == "st-all":
            self.show_all = not self.show_all
            ev.button.variant = "primary" if self.show_all else "default"
            self.refresh_rows()
        elif bid.startswith("acct-in-"):
            self.app.finish(lambda: accounts.sign_in(bid[8:]))
        elif bid.startswith("acct-out-"):
            cli = bid[9:]
            self.app.finish(lambda: accounts.sign_out(cli))
        elif bid == "rl-go" or bid == "rl-busy":
            args = ["navigator.restore", "relaunch", self.relaunch_cli or ""]
            if bid == "rl-busy":
                args.append("--busy")
            background(*args)
            self.app.exit("↻ relaunching in the background")
        elif bid == "rl-cancel":
            self.query_one("#relaunch-box").remove_class("show")
