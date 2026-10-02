"""The Navigator's Sessions tab: search and resume, what reopens at logon, accounts, relaunching.

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

from . import accounts, autostart, launch, model, profiles, restore, settings, startup
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


class Prompt(ModalScreen):
    """One line of text. Returns it, or None."""

    DEFAULT_CSS = """
    Prompt { align: center middle; background: $background 50%; }
    #pr { width: 60; height: auto; border: round $primary; background: $panel; padding: 1 2; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self.title_, self.placeholder = title, placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="pr"):
            yield Static(Text(self.title_, style="bold"))
            yield Input(placeholder=self.placeholder, id="pr-in")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def _ok(self, ev: Input.Submitted) -> None:
        self.dismiss(ev.value.strip() or None)


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
    .acct-prof { width: 24; }
    #relaunch-text { height: auto; margin: 1 0 0 0; }
    #start-table { height: 1fr; }
    #st-search { width: 1fr; min-width: 20; }
    #st-cli { width: 22; }
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
            yield Input(placeholder="Search sessions…  ( / )", id="st-search")
            yield Select([], prompt="every CLI", id="st-cli")
            yield Button("All", id="st-all", tooltip="Every session, not only ticked and recent ones")
            yield Button("⏻ Logon: off", id="st-autostart",
                         tooltip="Reopen the ticked sessions in herdr every time you log on")
            yield Button("▶ Open ticked", id="st-open", variant="primary",
                         tooltip="Open every ticked session that is not running yet, now")
            yield Button("↻ Relaunch", id="st-relaunch", tooltip="Restart open sessions in place (after a sign-in)")
            yield Button("🔑 Accounts", id="st-accounts", tooltip="Sign in, sign out, switch account per CLI")
            yield Button("🔔 Alerts", id="st-alerts", tooltip="Alerts to your phone: Telegram, ntfy, WhatsApp, Discord…")
        with Vertical(id="acct-box"):
            for cli in accounts.clis():
                with Horizontal():
                    yield Static(Text(cli, style="bold"), classes="acct-name")
                    yield Static("…", id=f"acct-who-{cli}", classes="acct-who")
                    if profiles.supported(cli):
                        yield Select([], prompt="profile", id=f"acct-prof-{cli}", classes="acct-prof")
                        yield Button("⇄", id=f"acct-switch-{cli}",
                                     tooltip="Switch to the chosen profile, then relaunch its sessions")
                        yield Button("💾", id=f"acct-save-{cli}",
                                     tooltip="Save the current login as a profile, to switch back to it later")
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
        q = self.query_one("#st-search", Input).value.strip().lower().split()
        cli_sel = self.query_one("#st-cli", Select)
        cli = "" if cli_sel.value is Select.NULL else str(cli_sel.value)
        self.sync_cli_filter(world)

        def hit(r) -> bool:
            s = r.session
            hay = " ".join((s.title, s.last_prompt, s.project.label, s.branch, s.cli)).lower()
            return (not cli or s.cli == cli) and all(tok in hay for tok in q)
        filtering = bool(q or cli)
        for g in self.groups:
            ticked = len(g.ticked)
            if filtering:
                found = [r for r in g.rows if hit(r)]
                if not found:
                    continue
            recent = [r for r in g.rows if r.ticked or r.session.mtime >= cutoff]
            if not (recent or self.show_all or filtering):
                continue
            box = Text("☑" if g.on else "☐", style="bold" if g.pinned else "dim")
            label = Text(g.project.name, style="bold cyan" if g.on else "dim")
            label.append(f"   {ticked} of {len(g.rows)}" if g.on else "   not restored", style="dim")
            if not g.auto:
                label.append("  · auto-tick off", style="dim")
            t.add_row(box, "", label, "", "", "", key=f"P|{g.project.root}")
            if not g.on and not (self.show_all or filtering):
                continue
            rows = found if filtering else g.rows if self.show_all else (
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
        sumtext = Text.assemble(("At logon: ", "dim"), (f"{n_ticked} ticked", "bold"), f" · {n_open} open · ",
                                (f"{max(0, min(n_ticked, cap) - n_open)} to open", "bold green"))
        sumtext.append("      Enter resume · ☐ tick for logon · right-click more", style="dim")
        self.query_one("#start-sum", Static).update(sumtext)

    def sync_cli_filter(self, world) -> None:
        clis = sorted({s.cli for s in world.sessions})
        sel = self.query_one("#st-cli", Select)
        if clis != getattr(self, "_clis", None):
            self._clis = clis
            keep = sel.value
            sel.set_options([(c, c) for c in clis])
            if keep in clis:
                sel.value = keep
        sel.display = len(clis) > 1

    def jump_to_project(self, root: str) -> None:
        self.query_one("#st-search", Input).value = ""
        self.refresh_rows()
        t = self.query_one(StartTable)
        try:
            t.move_cursor(row=t.get_row_index(f"P|{root}"))
            t.scroll_to_row = None
        except Exception:
            pass
        t.focus()

    @on(Input.Changed, "#st-search")
    def _search(self) -> None:
        self.refresh_rows()

    @on(Input.Submitted, "#st-search")
    def _search_done(self) -> None:
        self.query_one(StartTable).focus()

    @on(Select.Changed, "#st-cli")
    def _cli(self) -> None:
        self.refresh_rows()

    def sync_autostart(self) -> None:
        on_ = autostart.installed()
        b = self.query_one("#st-autostart", Button)
        b.label = "⏻ Logon: on" if on_ else "⏻ Logon: off"
        b.variant = "success" if on_ else "default"

    @work(thread=True, group="accounts")
    def load_accounts(self) -> None:
        for cli in accounts.clis():
            who = accounts.status(cli)
            self.app.call_from_thread(self.show_account, cli, who)

    def show_account(self, cli: str, who: str) -> None:
        self.who = {**getattr(self, "who", {}), cli: who}
        act = profiles.active(cli)
        self.query_one(f"#acct-who-{cli}", Static).update(Text.assemble(who, (f"  ·  profile {act}" if act else "", "cyan")))
        if profiles.supported(cli):
            sel = self.query_one(f"#acct-prof-{cli}", Select)
            opts = [(f"{n}  {profiles.label(cli, n)}"[:40], n) for n in profiles.names(cli)]
            sel.set_options(opts)
            if act in profiles.names(cli):
                sel.value = act

    def save_profile(self, cli: str) -> None:
        def done(name):
            if name:
                msg = profiles.save_as(cli, name, getattr(self, "who", {}).get(cli, ""))
                self.app.notify(msg, severity="error" if msg.startswith("✗") else "information")
                self.load_accounts()
        self.app.push_screen(Prompt(f"Save the current {cli} login as profile", "e.g. work, private"), done)

    def switch_profile(self, cli: str) -> None:
        v = self.query_one(f"#acct-prof-{cli}", Select).value
        if v is Select.NULL:
            self.app.notify("Pick a profile first (💾 saves the current login as one).", severity="warning")
            return
        msg = profiles.switch(cli, str(v))
        self.app.notify(msg, severity="error" if msg.startswith("✗") else "information", timeout=8)
        if msg.startswith("⇄"):
            self.load_accounts()
            self.open_relaunch(cli)

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
        elif bid == "st-alerts":
            self.app.push_screen(AlertsDialog())
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
        elif bid.startswith("acct-switch-"):
            self.switch_profile(bid[12:])
        elif bid.startswith("acct-save-"):
            self.save_profile(bid[10:])
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


class NewSession(ModalScreen):
    """New session: CLI, folder, name, optional new worktree, launch options. Returns a dict."""

    DEFAULT_CSS = """
    NewSession { align: center middle; background: $background 50%; }
    #ns { width: 72; height: auto; border: round $primary; background: $panel; padding: 1 2; }
    #ns Horizontal { height: 3; }
    #ns .lbl { width: 18; margin-top: 1; color: $text-muted; }
    #ns Input, #ns Select { width: 1fr; }
    #ns-claude { height: auto; }
    #ns-buttons { margin-top: 1; }
    #ns-buttons Button { margin-right: 1; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, clis: list[str], folder: str, project_label: str, is_git: bool) -> None:
        super().__init__()
        self.clis, self.folder, self.project_label, self.is_git = clis, folder, project_label, is_git

    def compose(self) -> ComposeResult:
        rc_default = bool(settings.load().restore.get("claude_remote_control", False))
        with Vertical(id="ns"):
            yield Static(Text.assemble(("New session  ", "bold"), (self.project_label[:40], "cyan")))
            with Horizontal():
                yield Static("CLI", classes="lbl")
                yield Select([(c, c) for c in self.clis], value=self.clis[0], allow_blank=False, id="ns-cli")
            with Horizontal():
                yield Static("Name", classes="lbl")
                yield Input(placeholder="how it shows in herdr, Remote Control and the lists", id="ns-name")
            with Horizontal():
                yield Static("Folder", classes="lbl")
                yield Input(self.folder, id="ns-folder")
            if self.is_git:
                with Horizontal():
                    yield Static("New worktree", classes="lbl")
                    yield Switch(value=False, id="ns-wt")
                    yield Input(placeholder="branch name (default: the session name)", id="ns-branch")
            with Vertical(id="ns-claude"):
                with Horizontal():
                    yield Static("Model", classes="lbl")
                    yield Input(placeholder="default (e.g. opus, sonnet)", id="ns-model")
                with Horizontal():
                    yield Static("Effort", classes="lbl")
                    yield Select([(e, e) for e in startup.EFFORTS[1:]], prompt="default", id="ns-effort")
                with Horizontal():
                    yield Static("Permission mode", classes="lbl")
                    yield Select([(m, m) for m in startup.PERMISSION_MODES[1:]], prompt="default", id="ns-perm")
                with Horizontal():
                    yield Static("Remote Control", classes="lbl")
                    yield Switch(value=rc_default, id="ns-rc")
            with Horizontal():
                yield Static("Extra arguments", classes="lbl")
                yield Input(placeholder="appended to the command", id="ns-args")
            with Horizontal(id="ns-buttons"):
                yield Button("▶ Start", variant="primary", id="ns-go")
                yield Button("Cancel", id="ns-cancel")

    def on_mount(self) -> None:
        self.query_one("#ns-name", Input).focus()
        self._sync()

    @on(Select.Changed, "#ns-cli")
    def _sync(self) -> None:
        self.query_one("#ns-claude").display = self.query_one("#ns-cli", Select).value == "claude"

    @on(Input.Submitted)
    def _enter(self) -> None:
        self._go()

    @on(Button.Pressed)
    def _btn(self, ev: Button.Pressed) -> None:
        ev.stop()
        if ev.button.id == "ns-cancel":
            self.dismiss(None)
        elif ev.button.id == "ns-go":
            self._go()

    def _go(self) -> None:
        cli = self.query_one("#ns-cli", Select).value
        name = self.query_one("#ns-name", Input).value.strip()
        branch = ""
        if self.is_git and self.query_one("#ns-wt", Switch).value:
            branch = self.query_one("#ns-branch", Input).value.strip() or name.lower().replace(" ", "-")
            if not branch:
                self.app.notify("A new worktree needs a branch name (or a session name).", severity="warning")
                return
        prefs = {"args": self.query_one("#ns-args", Input).value.strip()}
        if cli == "claude":
            eff = self.query_one("#ns-effort", Select).value
            perm = self.query_one("#ns-perm", Select).value
            prefs.update(model=self.query_one("#ns-model", Input).value.strip(),
                         effort="" if eff is Select.NULL else eff,
                         permission_mode="" if perm is Select.NULL else perm,
                         remote_control=self.query_one("#ns-rc", Switch).value)
        self.dismiss({"cli": cli, "name": name, "folder": self.query_one("#ns-folder", Input).value.strip(),
                      "branch": branch, "prefs": prefs})


class FinishWorktree(ModalScreen):
    """Put a worktree away: its state, then close its workspace or remove the checkout."""

    DEFAULT_CSS = """
    FinishWorktree { align: center middle; background: $background 50%; }
    #fw { width: 76; height: auto; border: round $primary; background: $panel; padding: 1 2; }
    #fw-buttons { height: 3; margin-top: 1; }
    #fw-buttons Button { margin-right: 1; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, st, label: str) -> None:
        super().__init__()
        self.st, self.label = st, label

    def compose(self) -> ComposeResult:
        st = self.st
        t = Text.assemble(("Finish worktree  ", "bold"), (self.label, "bold magenta"), "\n", (st.path, "dim"), "\n\n")
        if st.branch:
            t.append(f"branch {st.branch}", style="bold")
            if st.base and st.base != st.branch:
                t.append(f"   {st.ahead} commit(s) not in {st.base}", style="yellow" if st.ahead else "green")
                if st.behind:
                    t.append(f" · {st.behind} behind", style="dim")
            t.append("\n")
        if st.dirty:
            t.append(f"{len(st.dirty)} uncommitted change(s):\n", style="bold red")
            for ln in st.dirty[:6]:
                t.append(f"   {ln}\n", style="red")
        else:
            t.append("working tree clean\n", style="green")
        if st.agents:
            t.append(f"running: {', '.join(st.agents)}\n", style="bold yellow")
        if st.ahead and not st.dirty:
            t.append("\nIts commits stay on the branch: merge it when you are ready.\n", style="dim")
        if st.blockers:
            t.append("\nCan't remove yet: " + "; ".join(st.blockers) + "\n", style="red")
        with Vertical(id="fw"):
            yield Static(t)
            with Horizontal(id="fw-buttons"):
                yield Button("⎇ Remove worktree", id="fw-remove", variant="error", disabled=not st.removable,
                             tooltip="Close its workspace and delete the checkout folder. The branch is kept.")
                yield Button("Close workspace", id="fw-close", disabled=bool(st.agents or not st.workspaces),
                             tooltip="Only close it in herdr; the checkout stays on disk")
                yield Button("Cancel", id="fw-cancel")

    @on(Button.Pressed)
    def _btn(self, ev: Button.Pressed) -> None:
        ev.stop()
        self.dismiss({"fw-remove": "remove", "fw-close": "close"}.get(ev.button.id or ""))


class Digest(ModalScreen):
    """While you were away. Pick an entry to jump to it; Esc closes."""

    DEFAULT_CSS = """
    Digest { align: center middle; background: $background 50%; }
    #dg { width: 110; max-width: 96%; height: auto; max-height: 90%; border: round $primary;
          background: $panel; padding: 1 2; }
    #dg OptionList { height: auto; max-height: 24; border: none; background: $panel; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Close")]
    ICON = {"asks": ("❓ asks", "bold red"), "finished": ("✔ done", "bold green"), "ended": ("■ ended", "dim")}

    def __init__(self, entries, away: str) -> None:
        super().__init__()
        self.entries, self.away = entries, away

    def compose(self) -> ComposeResult:
        opts = []
        for i, e in enumerate(self.entries):
            icon, style = self.ICON[e.kind]
            t = Text.assemble((f"{icon:<8}", style), (f"{e.name[:28]:<29}", "bold"), (f"{e.project[:24]:<25}", "cyan"),
                              (e.line[:40], "dim"))
            t.no_wrap = True
            opts.append(Option(t, id=str(i)))
        with Vertical(id="dg"):
            yield Static(Text.assemble(("While you were away", "bold"), (f"  ·  last look {self.away} ago", "dim"),
                                       ("    Enter jumps there · Esc closes", "dim")))
            yield OptionList(*opts)

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _pick(self, ev: OptionList.OptionSelected) -> None:
        e = self.entries[int(ev.option.id)]
        self.dismiss(e.pane_id or None)


class UsagePane(Vertical):
    """Tokens per project and session: today, the last 7 days, and how much of it was output."""

    DEFAULT_CSS = """
    #usage-sum { height: 1; padding: 0 1; color: $text-muted; }
    #usage-table { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield Static("Reading transcripts…", id="usage-sum")
        yield DataTable(id="usage-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#usage-table", DataTable).add_columns("Project / session", "CLI", "Today", "7 days",
                                                              "of it output", "Last")
        self.loaded = False

    def load(self, world) -> None:
        if not getattr(self, "loaded", False):
            self.loaded = True
            self._collect(world)

    @work(thread=True, exclusive=True, group="usage")
    def _collect(self, world) -> None:
        from . import usage
        data = usage.collect()
        self.app.call_from_thread(self._show, world, data)

    def _show(self, world, data) -> None:
        from datetime import date, timedelta
        from .app import cli_tag
        from . import usage
        today = date.today().isoformat()
        week = (date.today() - timedelta(days=usage.DAYS - 1)).isoformat()
        by_id = {s.id: s for s in world.sessions}
        groups: dict[str, list] = {}
        for (cli, sid), su in data.items():
            wk = su.period(week)
            if not wk.total:
                continue
            s = by_id.get(sid)
            proj = s.project.name if s else "other / hidden"   # worktrees count toward their repo
            groups.setdefault(proj, []).append((su, s, su.period(today), wk))
        t = self.query_one("#usage-table", DataTable)
        t.clear()
        grand_today, grand_week = usage.Tally(), usage.Tally()
        order = sorted(groups.items(), key=lambda kv: -sum(x[3].total for x in kv[1]))
        for proj, rows in order:
            pt, pw = usage.Tally(), usage.Tally()
            for _, _, td, wk in rows:
                pt.add(td)
                pw.add(wk)
            grand_today.add(pt)
            grand_week.add(pw)
            t.add_row(Text(proj, style="bold cyan"), "", usage.human(pt.total) if pt.total else "",
                      Text(usage.human(pw.total), style="bold"),
                      usage.human(pw.out), "", key=f"P|{proj}")
            for su, s, td, wk in sorted(rows, key=lambda x: -x[3].total)[:8]:
                title = Text("  " + (s.title[:44] if s else su.sid[:8]))
                if s and s.project.worktree:
                    title.append(f"  ⎇ {s.project.worktree}"[:20], style="yellow")
                t.add_row(title, cli_tag(su.cli), usage.human(td.total) if td.total else "",
                          usage.human(wk.total), usage.human(wk.out), age(s.mtime) if s else "", key=f"S|{su.sid}")
        self.query_one("#usage-sum", Static).update(Text.assemble(
            ("today ", "dim"), (usage.human(grand_today.total), "bold"), ("   ·   7 days ", "dim"),
            (usage.human(grand_week.total), "bold"), (f"   ·   of it output {usage.human(grand_week.out)}", "dim"),
            ("     tokens, cache reads included", "dim")))


class AlertsDialog(ModalScreen):
    """Set up phone alerts: Telegram, ntfy, a webhook (Discord/Slack/...), WhatsApp."""

    DEFAULT_CSS = """
    AlertsDialog { align: center middle; background: $background 50%; }
    #al { width: 90; max-width: 96%; height: auto; max-height: 96%; border: round $primary;
          background: $panel; padding: 1 2; overflow-y: auto; }
    #al Horizontal { height: 3; }
    #al .lbl { width: 16; margin-top: 1; color: $text-muted; }
    #al .hint { color: $text-muted; margin: 0 0 0 16; }
    #al Input { width: 1fr; }
    #al Button { margin-left: 1; }
    #al-status { margin: 1 0 0 0; height: auto; }
    #al-buttons { margin-top: 1; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Close")]

    def compose(self) -> ComposeResult:
        c = settings.load().alerts
        g = lambda k: str(c.get(k, "") or "")
        with Vertical(id="al"):
            yield Static(Text("Alerts to your phone", style="bold"))
            yield Static("", id="al-status")
            with Horizontal():
                yield Static("Telegram bot", classes="lbl")
                yield Input(g("telegram_bot_token"), password=True, placeholder="token from @BotFather", id="al-tg")
                yield Button("Connect", id="al-tg-connect", variant="primary",
                             tooltip="After you pressed Start in your bot's chat: finds the chat, sends a test")
            yield Static("@BotFather › /newbot › paste token › Start in your bot › Connect", classes="hint")
            with Horizontal():
                yield Static("ntfy topic", classes="lbl")
                yield Input(g("ntfy_topic"), placeholder="a name only you know", id="al-ntfy")
                yield Button("Random", id="al-ntfy-random")
            with Horizontal():
                yield Static("Webhook URL", classes="lbl")
                yield Input(g("webhook_url"), password=True, placeholder="Discord / Slack / Teams / Mattermost", id="al-hook")
            with Horizontal():
                yield Static("WhatsApp", classes="lbl")
                yield Input(g("whatsapp_phone"), placeholder="+49…", id="al-wa-phone")
                yield Input(g("whatsapp_apikey"), password=True, placeholder="CallMeBot API key", id="al-wa-key")
            with Horizontal():
                yield Static("Waiting alert", classes="lbl")
                yield Input(str(c.get("blocked_minutes", 10)), placeholder="minutes, 0 = off", id="al-min")
                yield Static("  restore result", classes="lbl")
                yield Switch(value=bool(c.get("on_restore", True)), id="al-restore")
            with Horizontal(id="al-buttons"):
                yield Button("Save", id="al-save", variant="primary")
                yield Button("Send test", id="al-test")
                yield Button("Close", id="al-close")

    def on_mount(self) -> None:
        self.show_status()

    def show_status(self, extra: str = "") -> None:
        on = alerts_mod().channels()
        t = Text()
        for ch, ok in on.items():
            t.append(f"{'●' if ok else '○'} {ch}   ", style="bold green" if ok else "dim")
        if extra:
            t.append("\n" + extra)
        self.query_one("#al-status", Static).update(t)

    def _values(self) -> dict:
        v = lambda i: self.query_one(i, Input).value.strip()
        try:
            mins = max(0, int(v("#al-min") or 0))
        except ValueError:
            mins = 10
        return {"telegram_bot_token": v("#al-tg"), "ntfy_topic": v("#al-ntfy"), "webhook_url": v("#al-hook"),
                "whatsapp_phone": v("#al-wa-phone"), "whatsapp_apikey": v("#al-wa-key"),
                "blocked_minutes": mins, "on_restore": self.query_one("#al-restore", Switch).value}

    @on(Button.Pressed)
    def _btn(self, ev: Button.Pressed) -> None:
        ev.stop()
        bid = ev.button.id or ""
        al = alerts_mod()
        if bid == "al-close":
            self.dismiss(None)
        elif bid == "al-ntfy-random":
            self.query_one("#al-ntfy", Input).value = al.random_topic()
        elif bid == "al-save":
            al.save(self._values())
            self.show_status("Saved.")
        elif bid == "al-test":
            al.save(self._values())
            self.run_test_send()
        elif bid == "al-tg-connect":
            self.connect_telegram(self.query_one("#al-tg", Input).value.strip())

    @work(thread=True, group="alerts")
    def run_test_send(self) -> None:
        res = alerts_mod().send("herdr navigator", "Test alert: alerts reach you here.", "white_check_mark")
        msg = "  ".join(f"{k}: {'delivered' if ok else 'FAILED'}" for k, ok in res.items()) or "no channel set up yet"
        self.app.call_from_thread(self.show_status, msg)

    @work(thread=True, group="alerts")
    def connect_telegram(self, token: str) -> None:
        al = alerts_mod()
        if not token:
            self.app.call_from_thread(self.show_status, "Paste the bot token first.")
            return
        chat, who = al.telegram_find_chat(token)
        if not chat:
            self.app.call_from_thread(self.show_status, who)
            return
        al.save({**self._values(), "telegram_bot_token": token, "telegram_chat_id": chat})
        ok = al.send("herdr navigator", "Telegram connected: alerts will arrive here.").get("telegram")
        self.app.call_from_thread(self.show_status,
                                  f"Telegram connected to {who or chat}" + ("; test sent." if ok else "; the test FAILED."))


def alerts_mod():
    from . import alerts
    return alerts
