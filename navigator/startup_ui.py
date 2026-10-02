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
from textual.widgets import Button, Checkbox, DataTable, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from . import autostart, launch, model, restore, settings, startup
from .model import age
from .ui import Btn, Choice, Field, Tick

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
    ContextMenu OptionList { width: auto; min-width: 26; max-width: 50; height: auto; max-height: 20;
                             border: round $primary; background: $panel; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Close")]

    def __init__(self, items: list[tuple[str, str]], at: tuple[int, int]) -> None:
        super().__init__()
        self.items = items
        self.at = at

    def compose(self) -> ComposeResult:
        yield OptionList(*[Option(Text(label, style="bold dim") if i.startswith("-") else label, id=i,
                                  disabled=i.startswith("-")) for i, label in self.items])

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        x, y = self.at
        w, h = self.app.size
        ol.styles.offset = (max(0, min(x, w - 52)), max(0, min(y, h - min(20, len(self.items) + 2) - 1)))
        ol.focus()

    @on(OptionList.OptionSelected)
    def _pick(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(ev.option.id)

    def on_click(self, event: events.Click) -> None:
        if not self.query_one(OptionList).region.contains(event.screen_x, event.screen_y):
            self.dismiss(None)


LEGEND = Text.assemble(
    ("Symbols  ", "bold"),
    ("⚠", "bold red"), " waits for approval   ", ("⏳", "bold #ff9e64"), " asks you   ", ("✔", "bold green"),
    " done   ", ("◐", "yellow"), " working   ", ("○", "dim"), " parked\n         ", ("❓", "bold red"), " asks a question   ", ("↗", "magenta"), " other window   ",
    ("●", "green"), " running here\n         ",
    ("⎇", "#c678dd"), " worktree   ", ("↳", "yellow"), " sub-agent   ", ("▲", "bold red"), " context almost full   ",
    ("▸", ""), " tool it runs   ", ("☑", "green"), " on   ", ("☐", "dim"), " off   ",
    ("▾ ▸", "cyan"), " fold   ", ("▾", ""), " opens a menu")


class HelpScreen(ModalScreen):
    """What the current tab is for and how to work it."""

    DEFAULT_CSS = """
    HelpScreen { align: center middle; background: $background 50%; }
    #hp { width: 104; max-width: 96%; height: auto; max-height: 90%; border: round $primary;
          background: $panel; padding: 1 2; }
    #hp-title { text-style: bold; color: $accent; margin: 0 0 1 0; }
    #hp-foot { color: $text-muted; margin: 1 0 0 0; }
    #hp-legend { margin: 1 0 0 0; padding: 1 0 0 0; border-top: solid $primary 40%; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Close"), ("question_mark", "dismiss(None)", "Close")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.title_, self.body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(id="hp"):
            yield Static(self.title_, id="hp-title")
            yield Static(self.body)
            yield Static(LEGEND, id="hp-legend")
            yield Static("Keys 1–6 switch tabs · the footer shows the keys that work right now · "
                         "⚙ Settings › Keys lists every key · Esc closes", id="hp-foot")

    def on_click(self) -> None:
        self.dismiss(None)


class Prompt(ModalScreen):
    """One line of text. Returns it, or None."""

    DEFAULT_CSS = """
    Prompt { align: center middle; background: $background 50%; }
    #pr { width: 60; max-width: 96%; height: auto; border: round $primary; background: $panel; padding: 1 2; }
    #pr Static { margin: 0 0 1 0; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self.title_, self.placeholder = title, placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="pr"):
            yield Static(Text(self.title_, style="bold"))
            yield Field(placeholder=self.placeholder, id="pr-in")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def _ok(self, ev: Input.Submitted) -> None:
        self.dismiss(ev.value.strip() or None)


class LaunchOptions(ModalScreen):
    """How one session is relaunched: model, effort, permission mode, Remote Control, extra args."""

    DEFAULT_CSS = """
    LaunchOptions { align: center middle; background: $background 50%; }
    #lo { width: 70; max-width: 96%; height: auto; max-height: 96%; border: round $primary; background: $panel;
          padding: 1 2; }
    #lo > Static { margin: 0 0 1 0; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, cli: str, title: str, prefs: dict) -> None:
        super().__init__()
        self.cli, self.title_, self.prefs = cli, title, prefs

    def compose(self) -> ComposeResult:
        p = self.prefs
        with Vertical(id="lo"):
            yield Static(Text.assemble(("Launch options  ", "bold"), (self.title_[:44], "cyan")))
            if self.cli == "claude":
                rc_default = bool(settings.load().restore.get("claude_remote_control", False))
                with Horizontal(classes="form-row"):
                    yield Static("Model", classes="lbl")
                    yield Field(p.get("model", ""), placeholder="default (e.g. opus, sonnet)", id="lo-model")
                with Horizontal(classes="form-row"):
                    yield Static("Effort", classes="lbl")
                    yield Choice([(e, e) for e in startup.EFFORTS[1:]], value=p.get("effort") or Select.NULL,
                                 prompt="default", id="lo-effort")
                with Horizontal(classes="form-row"):
                    yield Static("Permission mode", classes="lbl")
                    yield Choice([(m, m) for m in startup.PERMISSION_MODES[1:]],
                                 value=p.get("permission_mode") or Select.NULL, prompt="default", id="lo-perm")
                yield Tick("Remote Control (use it from your phone)", bool(p.get("remote_control", rc_default)),
                           id="lo-rc")
            with Horizontal(classes="form-row"):
                yield Static("Extra arguments", classes="lbl")
                yield Field(p.get("args", ""), placeholder="appended to the resume command", id="lo-args")
            with Horizontal(classes="bar"):
                yield Btn("Save", variant="primary", id="lo-save")
                yield Btn("Reset", id="lo-reset", tooltip="Back to the defaults")
                yield Btn("Cancel", id="lo-cancel")

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
                           remote_control=self.query_one("#lo-rc", Checkbox).value)
            self.dismiss(out)


# ---- the table --------------------------------------------------------------------------------

class StartTable(DataTable):
    """A tree: project rows fold (▸/▾, ← →, Enter), the box column ticks (click or Space), a
    session row twice (or Enter) opens it, right-click (or .) shows everything else."""

    class Fold(Message):
        def __init__(self, key: str, open_: bool | None) -> None:
            super().__init__()
            self.key = key
            self.open_ = open_  # None toggles

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
                Binding("space", "box", "Logon on/off"), Binding("enter", "select_cursor", "Resume / fold", key_display="⏎"),
                Binding("left", "fold(False)", "Fold", show=False), Binding("right", "fold(True)", "Unfold", show=False),
                Binding("period", "menu", "Menu", key_display="."),
                Binding("shift+f10", "menu", "Menu", show=False)]

    def action_fold(self, open_: bool) -> None:
        k = self.key_at_cursor()
        if k:
            self.post_message(self.Fold(k, open_))

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
        elif meta.get("column") == 0 and key[:2] in ("P|", "M|"):
            self.post_message(self.Fold(key, None))
        elif meta.get("column") == 1:
            self.post_message(self.Box(key))
        else:
            t, k = self._last
            if k == key and time.monotonic() - t < 0.5:
                self.post_message(self.Open(key))
            self._last = (time.monotonic(), key)


class StartupPane(Vertical):
    DEFAULT_CSS = """
    #start-sum { width: 1fr; color: $text-muted; padding: 0 0 0 1; }
    #relaunch-box { height: auto; display: none; border: round $warning 60%; padding: 0 1; margin: 0 1 1 1; }
    #relaunch-box.show { display: block; }
    #relaunch-box .bar { margin: 1 0 0 0; padding: 0; }
    #relaunch-text { height: auto; }
    #start-table { height: 1fr; }
    #st-search { width: 1fr; min-width: 16; }
    #st-cli { width: 16; margin: 0 1; }
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.world: model.World | None = None
        self.groups: list[startup.ProjectRows] = []
        self.rows_by_key: dict[str, startup.Row] = {}
        self.full: set[str] = set()          # projects showing every session, not just the recent ones
        self.open_now: dict[str, bool] = {}  # which project rows are unfolded in the current view
        self.relaunch_cli: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="bar"):
            yield Field(placeholder="🔍 Search every session: title, prompt, project, branch  ( / )", id="st-search")
            yield Choice([], prompt="every CLI", id="st-cli")
            yield Btn("⊟ Fold all", id="st-fold", tooltip="Fold or unfold every project (← → fold one)")
        with Horizontal(classes="bar"):
            yield Btn("▶ Open ticked", id="st-open", variant="primary",
                      tooltip="Open every ticked session that is not running yet, now")
            yield Btn("☑ Logon list ▾", id="st-ticks", tooltip="Choose many at once what reopens at logon")
            yield Btn("↻ Relaunch ▾", id="st-relaunch", tooltip="Restart open sessions in place (after a sign-in)")
            yield Btn("⏻ Logon: off", id="st-autostart",
                      tooltip="Reopen the ticked sessions every time you log on (⚙ Settings › Logon for more)")
            yield Static("", id="start-sum")
        with Vertical(id="relaunch-box"):
            yield Static("", id="relaunch-text")
            with Horizontal(classes="bar"):
                yield Btn("↻ Relaunch", id="rl-go", variant="primary")
                yield Btn("Include busy ones", id="rl-busy", tooltip="Also restart sessions that are working right now")
                yield Btn("Cancel", id="rl-cancel")
        yield StartTable(id="start-table")

    def on_mount(self) -> None:
        self.query_one(StartTable).add_columns("", "Logon", "", "CLI", "Session", "Lane", "Last")
        self.sync_autostart()

    # ---- filling ------------------------------------------------------------------------------
    def _is_open(self, g, running: int, filtering: bool) -> bool:
        if filtering:
            return True
        folded = startup.ui_state().get("sessions_folded", {})
        root = g.project.root
        if root in folded:
            return not folded[root]
        return bool(g.ticked or running)  # by default only projects with something to show unfold

    def show(self, world: model.World) -> None:
        from .app import cli_tag  # shared look with the other tabs
        self.world = world
        self.groups = startup.plan(world.sessions)
        t = self.query_one(StartTable)
        keep = t.key_at_cursor()
        t.clear()
        self.rows_by_key = {}
        self.open_now = {}
        cutoff = time.time() - RECENT_DAYS * 86400
        q = self.query_one("#st-search", Input).value.strip().lower().split()
        cli_sel = self.query_one("#st-cli", Select)
        cli = "" if cli_sel.value is Select.NULL else str(cli_sel.value)
        self.sync_cli_filter(world)
        live = world.live_sessions

        def hit(r) -> bool:
            s = r.session
            hay = " ".join((s.title, s.last_prompt, s.project.label, s.branch, s.cli)).lower()
            return (not cli or s.cli == cli) and all(tok in hay for tok in q)
        filtering = bool(q or cli)
        n_ticked = sum(len(g.ticked) for g in self.groups)
        n_mine = sum(1 for g in self.groups for r in g.ticked if r.pinned)
        n_open = sum(1 for g in self.groups for r in g.ticked if r.session.id in live)
        for g in self.groups:
            root = g.project.root
            found = [r for r in g.rows if hit(r)] if filtering else g.rows
            if not found:
                continue
            running = [r for r in g.rows if r.session.id in live]
            is_open = self._is_open(g, len(running), filtering)
            self.open_now[root] = is_open
            chevron = Text("▾" if is_open else "▸", style="bold cyan")
            box = Text("☑" if g.on else "☐", style=("bold" if g.pinned else "dim") + (" green" if g.on else ""))
            label = Text(g.project.name[:24], style="bold cyan" if g.on else "bold")
            stats = []
            if g.on:
                stats.append(f"{len(g.ticked)} ticked")
            else:
                stats.append("off at logon")
            if running:
                stats.append(f"{len(running)} running")
            stats.append(f"{len(g.rows)} in all")
            label.append("   " + " · ".join(stats), style="dim")
            if not g.auto:
                label.append(" · auto-tick off", style="dim")
            t.add_row(chevron, box, "", "", label, "", "", key=f"P|{root}")
            if not is_open:
                continue
            if filtering or root in self.full:
                rows = list(found)
            else:
                must = [r for r in g.rows if r.ticked or r.session.id in live]
                rows = must + [r for r in g.rows if r not in must and r.session.mtime >= cutoff][:UNTICKED_PER_PROJECT]
            rows.sort(key=lambda r: -r.session.mtime)
            for r in rows:
                s = r.session
                k = f"S|{startup.skey(s)}"
                self.rows_by_key[k] = r
                a = live.get(s.id)
                ticked_here = r.ticked and g.on
                now = ""
                if a:  # running: here (●), in another window (↗), or asking you something (❓)
                    now = Text("●", style="bold green") if a.in_herdr else Text("↗", style="bold magenta")
                    if a.question:
                        now = Text("❓", style="bold red")
                tick = Text("☑" if r.ticked else "☐",
                            style=("bold" if r.pinned else "dim") + (" green" if ticked_here else ""))
                title = Text(s.title[:40])
                if r.ticked and not r.pinned:
                    title.append("  auto", style="dim italic")
                if r.prefs:
                    title.append("  ✎ options", style="dim")
                lane = Text(f"⎇ {s.project.worktree}"[:15], style="#c678dd") if s.project.worktree else ""
                t.add_row("", tick, now, cli_tag(s.cli), title, lane, age(s.mtime), key=k)
            hidden = len(found) - len(rows)
            if hidden > 0:
                more = f"… {hidden} older session{'s' * (hidden != 1)}: Enter shows all"
                t.add_row("", "", "", "", Text(more, style="dim italic"), "", "", key=f"M|{root}")
        if keep:
            try:
                t.move_cursor(row=t.get_row_index(keep))
            except Exception:
                pass
        cap = int(settings.load().restore.get("max_sessions", 30))
        sumtext = Text.assemble(("At logon ", "dim"), (f"{min(n_ticked, cap)} reopen", "bold green"),
                                (f" ({n_mine} yours, {n_ticked - n_mine} auto)", "dim"),
                                (f" · {n_open} of them run now", "dim"))
        if n_ticked > cap:
            sumtext.append(f" · capped at {cap}", style="bold yellow")
        self.query_one("#start-sum", Static).update(sumtext)
        fold = self.query_one("#st-fold", Button)
        fold.label = "⊞ Unfold all" if not any(self.open_now.values()) else "⊟ Fold all"

    def fold(self, key: str, open_: bool | None) -> None:
        """Fold or unfold a project (from its row or any of its session rows)."""
        if key.startswith("M|"):
            self.full.add(key[2:])
            self.refresh_rows()
            return
        root = key[2:] if key.startswith("P|") else None
        if root is None and key in self.rows_by_key:
            root = self.rows_by_key[key].session.project.root
        if root is None:
            return
        is_open = self.open_now.get(root, False)
        want = (not is_open) if open_ is None else open_
        if want == is_open:
            if key.startswith("S|") and not want:  # ← on a session: up to its project
                self._cursor_to(f"P|{root}")
            return
        folded = dict(startup.ui_state().get("sessions_folded", {}))
        folded[root] = not want
        startup.set_ui("sessions_folded", folded)
        if not want:
            self.full.discard(root)
        self.refresh_rows()
        self._cursor_to(f"P|{root}")

    def fold_all(self) -> None:
        want_open = not any(self.open_now.values())
        startup.set_ui("sessions_folded", {g.project.root: not want_open for g in self.groups})
        self.full.clear()
        self.refresh_rows()

    def _cursor_to(self, key: str) -> None:
        t = self.query_one(StartTable)
        try:
            t.move_cursor(row=t.get_row_index(key))
        except Exception:
            pass

    # ---- bulk ticks -----------------------------------------------------------------------------
    def ticks_menu(self, widget) -> None:
        running = [r for g in self.groups for r in g.rows if self.world and r.session.id in self.world.live_sessions]
        items = [("-head", "What reopens at logon"),
                 ("t-running", f"☑ Add the {len(running)} running now"),
                 ("t-only-running", f"☑ Exactly the {len(running)} running now, nothing else"),
                 ("t-none", "☐ Nothing (untick everything)"),
                 ("t-auto", "↺ Back to automatic (newest per lane)"),
                 ("t-rules", "… Automatic rules (Settings)")]
        r = widget.region
        self.app.push_screen(ContextMenu(items, (r.x, r.y + 1)), lambda c: c and c[0] != "-" and self.on_ticks(c))

    def on_ticks(self, choice: str | None) -> None:
        if not choice or not self.world:
            return
        live = self.world.live_sessions
        rows = [r for g in self.groups for r in g.rows]
        if choice == "t-rules":
            from .settings_ui import SettingsPane
            self.app.action_tab("settings")
            self.app.query_one(SettingsPane).show_section("logon")
            return
        if choice == "t-auto":
            startup.reset_all()
        elif choice == "t-running":
            run = [r for r in rows if r.session.id in live]
            startup.set_ticks({startup.skey(r.session): True for r in run},
                              sorted({r.session.project.root for r in run}))
        elif choice == "t-only-running":
            run_roots = sorted({r.session.project.root for r in rows if r.session.id in live})
            startup.set_ticks({startup.skey(r.session): r.session.id in live for r in rows}, run_roots)
        elif choice == "t-none":
            startup.set_ticks({startup.skey(r.session): False for r in rows})
        self.refresh_rows()

    def relaunch_menu(self, widget) -> None:
        if not self.world:
            return
        clis = sorted({a.cli for a in self.world.agents if a.in_herdr})
        items = [("", "↻ Every CLI")] + [(c, f"↻ Only {c}") for c in clis]
        items = [(f"r:{c}", label) for c, label in items]
        r = widget.region
        self.app.push_screen(ContextMenu(items, (r.x, r.y + 1)), lambda c: c and self.open_relaunch(c[2:]))

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
        folded = dict(startup.ui_state().get("sessions_folded", {}))
        folded[root] = False
        startup.set_ui("sessions_folded", folded)
        self.refresh_rows()
        self._cursor_to(f"P|{root}")
        self.query_one(StartTable).focus()

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
        b.label = "⏻ Logon restore: on" if on_ else "⏻ Logon restore: OFF"
        b.variant = "default" if on_ else "warning"
        try:
            from .settings_ui import SettingsPane
            pane = self.app.query_one(SettingsPane)
            pane.query_one("#lg-auto").value = on_
            pane.show_logon_status()
        except Exception:
            pass

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
            self.app.finish(lambda: launch.resume(self.world, r.session, focus=False),
                            busy=f"▶ opening {r.session.title[:40]}…")

    def menu_items(self, key: str) -> list[tuple[str, str]]:
        if key.startswith("M|"):
            return [("m-all", "☰ Show all its sessions")]
        if key.startswith("P|"):
            g = next((g for g in self.groups if g.project.root == key[2:]), None)
            if not g:
                return []
            open_ = self.open_now.get(g.project.root, False)
            return [("p-fold", "▸ Fold" if open_ else "▾ Unfold"),
                    ("p-all", f"☰ Show all {len(g.rows)} sessions"),
                    ("p-toggle", "☐ Nothing from this project at logon" if g.on else "☑ Restore this project at logon"),
                    ("p-open", f"▶ Open its {len(g.ticked)} ticked now"),
                    ("p-untick", "☐ Untick all its sessions"),
                    ("p-auto", "Auto-tick off here" if g.auto else "Auto-tick on here"),
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
        items += [("s-options", "✎ Launch options…"), ("s-copy", "⧉ Show resume command")]
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
        if choice == "m-all":
            self.fold(key, True)
            return
        if key.startswith("P|"):
            root = key[2:]
            g = next((g for g in self.groups if g.project.root == root), None)
            if not g:
                return
            if choice == "p-fold":
                self.fold(key, None)
                return
            if choice == "p-all":
                self.full.add(root)
                self.fold(key, True)
                self.refresh_rows()
                return
            if choice == "p-toggle":
                startup.set_project(root, on=not g.on)
            elif choice == "p-auto":
                startup.set_project(root, auto=not g.auto)
            elif choice == "p-untick":
                startup.set_ticks({startup.skey(r.session): False for r in g.rows})
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
            self.app.finish(lambda: (model.herdr.focus_agent(a.pane_id), f"→ {a.pane_id}")[1], jump=True)
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

    def open_ticked(self) -> None:
        if self.world:
            self.open_rows_bg([r for r in startup.selected(self.world.sessions)
                               if r.session.id not in self.world.live_sessions])

    def open_rows_bg(self, rows: list[startup.Row]) -> None:
        if not rows:
            self.app.notify("Nothing to open: everything ticked is running.")
            return
        background("navigator.restore", "now")
        self.app.notify(f"▶ opening {len(rows)} sessions in the background (restore.log has the details)",
                        timeout=6)
        self.app.set_timer(6, self.app.load_world)

    @on(StartTable.Box)
    def _box(self, ev: StartTable.Box) -> None:
        if ev.key.startswith(("P|", "S|")):
            self.toggle(ev.key)

    @on(StartTable.Fold)
    def _fold(self, ev: StartTable.Fold) -> None:
        self.fold(ev.key, ev.open_)

    @on(StartTable.Menu)
    def _menu(self, ev: StartTable.Menu) -> None:
        self.open_menu(ev.key, ev.at)

    @on(DataTable.RowSelected, "#start-table")
    def _enter(self, ev: DataTable.RowSelected) -> None:
        k = self.current()
        if k.startswith("S|"):
            self.open_session(k)
        elif k:
            self.fold(k, None)

    @on(StartTable.Open)
    def _open(self, ev: StartTable.Open) -> None:
        if ev.key.startswith("S|"):
            self.open_session(ev.key)
        else:
            self.fold(ev.key, None)

    @on(Button.Pressed)
    def _button(self, ev: Button.Pressed) -> None:
        bid = ev.button.id or ""
        ev.stop()
        if bid == "st-autostart":
            msg = autostart.uninstall() if autostart.installed() else autostart.install()
            self.sync_autostart()
            self.app.notify(msg, severity="error" if msg.startswith("✗") else "information")
        elif bid == "st-open":
            self.open_ticked()
        elif bid == "st-relaunch":
            self.relaunch_menu(ev.button)
        elif bid == "st-ticks":
            self.ticks_menu(ev.button)
        elif bid == "st-fold":
            self.fold_all()
        elif bid == "rl-go" or bid == "rl-busy":
            args = ["navigator.restore", "relaunch", self.relaunch_cli or ""]
            if bid == "rl-busy":
                args.append("--busy")
            background(*args)
            self.query_one("#relaunch-box").remove_class("show")
            self.app.notify("↻ relaunching in the background", timeout=6)
            self.app.set_timer(8, self.app.load_world)
        elif bid == "rl-cancel":
            self.query_one("#relaunch-box").remove_class("show")


class NewSession(ModalScreen):
    """New session: CLI, folder, name, optional new worktree, launch options. Returns a dict."""

    DEFAULT_CSS = """
    NewSession { align: center middle; background: $background 50%; }
    #ns { width: 76; max-width: 96%; height: auto; max-height: 96%; border: round $primary; background: $panel;
          padding: 1 2; }
    #ns > Static { margin: 0 0 1 0; }
    #ns-claude { height: auto; }
    #ns-wt-row Checkbox { width: 18; margin: 0; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, clis: list[str], folder: str, project_label: str, is_git: bool) -> None:
        super().__init__()
        self.clis, self.folder, self.project_label, self.is_git = clis, folder, project_label, is_git

    def compose(self) -> ComposeResult:
        rc_default = bool(settings.load().restore.get("claude_remote_control", False))
        with Vertical(id="ns"):
            yield Static(Text.assemble(("New session  ", "bold"), (self.project_label[:40], "cyan")))
            with Horizontal(classes="form-row"):
                yield Static("CLI", classes="lbl")
                yield Choice([(c, c) for c in self.clis], value=self.clis[0], allow_blank=False, id="ns-cli")
            with Horizontal(classes="form-row"):
                yield Static("Name", classes="lbl")
                yield Field(placeholder="optional: shown in herdr, the lists and Remote Control", id="ns-name")
            with Horizontal(classes="form-row"):
                yield Static("Folder", classes="lbl")
                yield Field(self.folder, id="ns-folder")
            if self.is_git:
                with Horizontal(classes="form-row", id="ns-wt-row"):
                    yield Tick("New worktree", False, id="ns-wt")
                    yield Field(placeholder="branch name (default: the session name)", id="ns-branch")
            with Vertical(id="ns-claude"):
                with Horizontal(classes="form-row"):
                    yield Static("Model", classes="lbl")
                    yield Field(placeholder="default (e.g. opus, sonnet)", id="ns-model")
                with Horizontal(classes="form-row"):
                    yield Static("Effort", classes="lbl")
                    yield Choice([(e, e) for e in startup.EFFORTS[1:]], prompt="default", id="ns-effort")
                with Horizontal(classes="form-row"):
                    yield Static("Permission mode", classes="lbl")
                    yield Choice([(m, m) for m in startup.PERMISSION_MODES[1:]], prompt="default", id="ns-perm")
                yield Tick("Remote Control (use it from your phone)", rc_default, id="ns-rc")
            with Horizontal(classes="form-row"):
                yield Static("Extra arguments", classes="lbl")
                yield Field(placeholder="appended to the command", id="ns-args")
            with Horizontal(classes="bar"):
                yield Btn("▶ Start", variant="primary", id="ns-go")
                yield Btn("Cancel", id="ns-cancel")

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
        if self.is_git and self.query_one("#ns-wt", Checkbox).value:
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
                         remote_control=self.query_one("#ns-rc", Checkbox).value)
        self.dismiss({"cli": cli, "name": name, "folder": self.query_one("#ns-folder", Input).value.strip(),
                      "branch": branch, "prefs": prefs})


class FinishWorktree(ModalScreen):
    """Put a worktree away: its state, then close its workspace or remove the checkout."""

    DEFAULT_CSS = """
    FinishWorktree { align: center middle; background: $background 50%; }
    #fw { width: 80; max-width: 96%; height: auto; max-height: 96%; border: round $primary; background: $panel;
          padding: 1 2; }
    #fw-buttons { margin: 1 0 0 0; padding: 0; }
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
            with Horizontal(id="fw-buttons", classes="bar"):
                yield Btn("⎇ Remove worktree", id="fw-remove", variant="error", disabled=not st.removable,
                          tooltip="Close its workspace and delete the checkout folder. The branch is kept.")
                yield Btn("Close workspace", id="fw-close", disabled=bool(st.agents or not st.workspaces),
                          tooltip="Only close it in herdr; the checkout stays on disk")
                yield Btn("Cancel", id="fw-cancel")

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
        self.query_one("#usage-table", DataTable).add_columns("Project / session", "CLI", "Today", "7 days", "Output", "Cache reads", "Last")
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
        work_ = lambda t_: t_.fresh + t_.out
        order = sorted(groups.items(), key=lambda kv: -sum(work_(x[3]) for x in kv[1]))
        for proj, rows in order:
            pt, pw = usage.Tally(), usage.Tally()
            for _, _, td, wk in rows:
                pt.add(td)
                pw.add(wk)
            grand_today.add(pt)
            grand_week.add(pw)
            t.add_row(Text(proj, style="bold cyan"), "", usage.human(work_(pt)) if work_(pt) else "",
                      Text(usage.human(work_(pw)), style="bold"), usage.human(pw.out),
                      Text(usage.human(pw.cached), style="dim"), "", key=f"P|{proj}")
            for su, s, td, wk in sorted(rows, key=lambda x: -work_(x[3]))[:8]:
                title = Text("  " + (s.title[:44] if s else su.sid[:8]))
                if s and s.project.worktree:
                    title.append(f"  ⎇ {s.project.worktree}"[:20], style="#c678dd")
                t.add_row(title, cli_tag(su.cli), usage.human(work_(td)) if work_(td) else "",
                          usage.human(work_(wk)), usage.human(wk.out), Text(usage.human(wk.cached), style="dim"),
                          age(s.mtime) if s else "", key=f"S|{su.sid}")
        self.query_one("#usage-sum", Static).update(Text.assemble(
            ("Tokens worked (new input + output)   today ", "dim"), (usage.human(work_(grand_today)), "bold"),
            ("  ·  7 days ", "dim"), (usage.human(work_(grand_week)), "bold"),
            (f"  ·  of it output {usage.human(grand_week.out)}", "dim"),
            (f"   ·   cache reads {usage.human(grand_week.cached)} (cheap, shown apart)", "dim")))
