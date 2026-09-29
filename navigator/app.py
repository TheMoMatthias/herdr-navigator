"""herdr Navigator: one clickable, keyboard-driven screen for projects, worktrees, agents,
sub-agents and session resume.

Every row, tab and button is clickable; every action also has a key, and the footer always
shows the keys that work right now (click them too). Click a row once to select it, click it
again (or press Enter) to open it, so a stray click never launches anything. Clicking the
☐/☑ box of a project puts it on (or takes it off) herdr's sidebar.
"""
from __future__ import annotations

import sys
import time

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, DataTable, Footer, Input, Static, TabbedContent, TabPane

from . import keys as keymap
from . import launch, model, settings, sidebar
from .model import STATE_ICON, Agent, World, age, summarize

STATE_STYLE = {"blocked": "bold red", "done": "bold green", "working": "yellow", "idle": "dim", "unknown": "magenta"}
CLI_STYLE = {"claude": "#d97757", "codex": "#10a37f"}
TABS = ["projects", "agents", "resume", "keys"]
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


class Navigator(App):
    ENABLE_COMMAND_PALETTE = False
    TITLE = "herdr navigator"

    CSS = """
    Screen { background: $surface; }
    #topbar { height: 1; padding: 0 1; background: $boost; }
    #hint { height: 1; padding: 0 1; color: $text-muted; }
    #proj-detail { width: 44%; min-width: 36; padding: 0 1; border-left: tall $primary 40%; }
    .btnrow { height: 3; }
    .btnrow Button { margin: 0 1 0 0; min-width: 8; }
    #resume-bar, #agent-bar { height: 3; }
    #resume-search { width: 1fr; }
    DataTable { height: 1fr; }
    #keys-body { padding: 0 1; }
    """

    BINDINGS = [
        Binding("1", "tab('projects')", "Projects"),
        Binding("2", "tab('agents')", "Agents"),
        Binding("3", "tab('resume')", "Resume"),
        Binding("4", "tab('keys')", "Keys"),
        Binding("enter", "open", "Open", priority=False),
        Binding("space", "toggle_sidebar", "☑ Sidebar"),
        Binding("c", "new('claude')", "New Claude"),
        Binding("x", "new('codex')", "New Codex"),
        Binding("r", "resume_project", "Resume here"),
        Binding("p", "toggle_project", "This project/all"),
        Binding("slash", "search", "Search"),
        Binding("b", "filter('blocked')", "Blocked", show=False),
        Binding("w", "filter('working')", "Working", show=False),
        Binding("d", "filter('done')", "Done", show=False),
        Binding("i", "filter('idle')", "Idle", show=False),
        Binding("a", "filter('')", "All", show=False),
        Binding("o", "filter('outside')", "Outside herdr", show=False),
        Binding("f5", "refresh", "Refresh"),
        Binding("escape", "back", "Close"),
        Binding("q", "quit", "Close", show=False),
    ]

    def __init__(self, start_tab: str = "projects") -> None:
        super().__init__()
        self.start_tab = start_tab if start_tab in TABS else "projects"
        self.world: World | None = None
        self.current_project = None          # project of the focused workspace
        self.only_project = True             # Resume: limit to the selected project
        self.agent_filter = ""
        self.selected_key: str | None = None  # project root, or root|wt|label
        self.agent_rows: dict[str, tuple[Agent, int]] = {}

    # ---- layout ---------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("herdr navigator · loading…", id="topbar")
        with TabbedContent(initial=self.start_tab, id="tabs"):
            with TabPane("1 Projects", id="projects"):
                with Horizontal():
                    yield ClickTwiceTable(id="proj-table")
                    with Vertical(id="proj-detail"):
                        with Horizontal(classes="btnrow"):
                            yield Button("Go ⏎", id="btn-go", variant="primary")
                            yield Button("☑ Sidebar ␣", id="btn-sidebar")
                            yield Button("Resume r", id="btn-resume")
                            yield Button("+Claude c", id="btn-claude")
                            yield Button("+Codex x", id="btn-codex")
                        yield VerticalScroll(Static(id="proj-info"))
            with TabPane("2 Agents", id="agents"):
                with Horizontal(id="agent-bar", classes="btnrow"):
                    for label, f in (("All a", ""), ("⚠ Blocked b", "blocked"), ("✔ Done d", "done"),
                                     ("◐ Working w", "working"), ("○ Idle i", "idle"), ("⧉ Outside o", "outside")):
                        yield Button(label, id=f"flt-{f or 'all'}")
                yield ClickTwiceTable(id="agent-table")
            with TabPane("3 Resume", id="resume"):
                with Horizontal(id="resume-bar", classes="btnrow"):
                    yield Input(placeholder="/ search title, prompt, project, worktree, branch…", id="resume-search")
                    yield Button("This project p", id="btn-scope")
                yield ClickTwiceTable(id="resume-table")
            with TabPane("4 Keys", id="keys"):
                yield VerticalScroll(Static(id="keys-body"))
        yield Static("", id="hint")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#proj-table", DataTable).add_columns("Sidebar", "Project / worktree", "Agents", "Last", "Sessions")
        self.query_one("#agent-table", DataTable).add_columns("State", "Project", "Agent", "Where", "Doing now")
        self.query_one("#resume-table", DataTable).add_columns("When", "CLI", "Project", "Title", "Branch", "")
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
        bar = Text.assemble(("herdr navigator", "bold"), "  ·  you are in ", (where, "bold cyan"),
                            "  ·  agents ", summarize(total) or "none")
        if outside:
            bar.append(f"  ·  ⧉{outside} outside herdr", style="magenta")
        if subs:
            bar.append(f"  ·  ↳{subs} sub-agents", style="dim")
        bar.append("  ·  F1 opens this anytime", style="dim italic")
        if world.error:
            bar.append(f"  ·  herdr unreachable: {world.error[:60]}", style="red")
        self.query_one("#topbar", Static).update(bar)
        self.fill_projects()
        self.fill_agents()
        self.fill_resume()
        self.set_hint()

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
            if v.worktrees:
                active = len(v.active_worktrees())
                name.append(f"  ⎇{len(v.worktrees)}" + (f" ({active} active)" if active else ""), style="dim")
            t.add_row(box, name, summarize(v.counts()) or "·",
                      age(v.last_activity) if v.last_activity else "·", str(len(v.sessions)), key=v.project.root)
            if v.project.root == self.selected_key:
                target_row = row
            row += 1
            # worktrees with something running or open; the detail panel lists all of them
            show = [w for w in v.worktrees.values() if w.agents or w.workspace]
            show.sort(key=lambda w: (not w.agents, -max((s.mtime for s in w.sessions), default=0)))
            for wt in show[:12]:
                k = f"{v.project.root}{WT_SEP}{wt.label}"
                label = Text(f"   ⎇ {wt.label}", style="bold" if wt.agents else "dim")
                if wt.workspace:
                    label.append(f"  {wt.workspace.id}", style="green")
                last = max((s.mtime for s in wt.sessions), default=0)
                t.add_row("", label, summarize(model.Counter(a.status for a in wt.agents)) or "·",
                          age(last) if last else "·", str(len(wt.sessions)), key=k)
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
        out.append(f"\n{(wt.path if wt else v.project.path) or v.project.root}\n", style="dim")
        out.append("In herdr's sidebar: ", style="dim")
        out.append("yes ☑" if v.in_sidebar else "no ☐  (press Space or click the box)",
                   style="green" if v.in_sidebar else "yellow")
        out.append("\n\n")
        if not wt and v.worktrees:
            out.append("Worktrees\n", style="bold underline")
            for w in sorted(v.worktrees.values(), key=lambda w: (not w.agents, w.label))[:15]:
                out.append(f"  ⎇ {w.label:<22}", style="bold" if w.agents else "dim")
                out.append(summarize(model.Counter(a.status for a in w.agents)) or "·")
                out.append(f"  {len(w.sessions)} sessions", style="dim")
                if w.workspace:
                    out.append(f"  in sidebar ({w.workspace.id})", style="green")
                elif not w.exists:
                    out.append("  (removed)", style="dim red")
                out.append("\n")
            out.append("\n")
        out.append("Agents\n", style="bold underline")
        if agents:
            for a in sorted(agents, key=lambda a: model.STATE_ORDER.get(a.status, 9)):
                out.append("  ")
                out.append_text(state_text(a.status))
                out.append(f"  {a.cli} ")
                out.append(a.name or a.title[:30], style="bold")
                where = a.pane_id if a.in_herdr else f"outside herdr, pid {a.pid}"
                out.append(f"  [{where}]\n", style="dim" if a.in_herdr else "magenta")
                if a.activity:
                    out.append(f"      {a.activity[:70]}\n", style="dim")
                for sa in a.subagents:
                    out.append(f"      ↳ {sa.name}", style="yellow")
                    out.append(f" ({sa.kind or 'agent'}{', ' + sa.model if sa.model else ''}) ", style="dim")
                    out.append(f"{sa.description[:40]}\n")
                    if sa.activity:
                        out.append(f"          {sa.activity[:64]}\n", style="dim")
        else:
            out.append("  none running\n", style="dim")
        out.append("\nRecent sessions  (r to resume)\n", style="bold underline")
        for s in sessions[:8]:
            live = self.world.live_sessions.get(s.id)
            out.append(f"  {age(s.mtime):>4} ", style="dim")
            out.append(f"{s.cli:<6}", style=CLI_STYLE.get(s.cli, ""))
            wtl = f"[{s.project.worktree}] " if s.project.worktree and not wt else ""
            out.append(f" {wtl}{s.title[:46]}")
            if live:
                out.append(" ● live" if live.in_herdr else " ⧉ running outside", style="green" if live.in_herdr else "magenta")
            out.append("\n")
        if not sessions:
            out.append("  none in the last %d days\n" % settings.load().max_age_days, style="dim")
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
            proj = Text(a.project.label, style="bold cyan" if here else "")
            who = Text(f"{a.cli} ", style=CLI_STYLE.get(a.cli, ""))
            who.append(a.name or "", style="bold")
            if a.in_herdr:
                where = Text(f"{a.pane_id} · {self.world.tab_labels.get(a.tab_id, '')}" + (" ◀" if a.focused else ""))
            else:
                where = Text(f"⧉ outside, pid {a.pid}", style="magenta")
            doing = a.activity or a.title
            t.add_row(state_text(a.status), proj, who, where, doing[:80], key=a.key)
            self.agent_rows[a.key] = (a, -1)
            for i, sa in enumerate(a.subagents):
                k = f"{a.key}#sub{i}"
                t.add_row(Text("  ↳ running", style="yellow"), Text(""),
                          Text(f"  {sa.name}", style="yellow"),
                          Text(f"{sa.kind or 'sub-agent'}{' · ' + sa.model if sa.model else ''}", style="dim"),
                          (sa.activity or sa.description)[:80], key=k)
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
        for s in self.world.sessions:
            if self.only_project and v:
                if s.project.root != v.project.root or (wt and s.project.worktree != wt.label):
                    continue
            if q and not all(tok in " ".join((s.title, s.last_prompt, s.project.label, s.branch, s.cli)).lower()
                             for tok in q.split()):
                continue
            live = self.world.live_sessions.get(s.id)
            mark = ""
            if live:
                mark = Text("● live", style="green") if live.in_herdr else Text("⧉ outside", style="magenta")
            t.add_row(age(s.mtime), Text(s.cli, style=CLI_STYLE.get(s.cli, "")), s.project.label,
                      s.title[:70], s.branch[:18], mark, key=f"{s.cli}:{s.id}")
        scope = self.query_one("#btn-scope", Button)
        name = (v.project.name + (f" ⎇ {wt.label}" if wt else "")) if v else ""
        scope.label = f"Only {name} p" if (self.only_project and v) else "All projects p"
        scope.variant = "primary" if self.only_project else "default"

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
        out.append("F1 opens the Navigator from anywhere in herdr.\n", style="bold green")
        out.append("How keys work: ", style="bold")
        out.append(f"press the prefix {keymap.pretty(prefix, prefix)}, release, then the key. ")
        out.append("Direct chords (Ctrl+Alt+…, F-keys) work without the prefix.\n", style="dim")
        out.append("herdr itself is clickable too: sidebar rows, tabs, pane borders, right-click menus.\n\n",
                   style="dim")
        if custom:
            out.append("Navigator & custom commands\n", style="bold underline")
            for b in custom:
                ks = "   ".join(keymap.pretty(k, prefix) for k in b.keys)
                out.append(f"  {ks:<34}", style="bold yellow")
                out.append(f"{b.label}\n")
            out.append("\n")
        for title, rows in groups:
            out.append(f"{title}\n", style="bold underline")
            for b in rows:
                if b.keys:
                    ks = "   ".join(keymap.pretty(k, prefix) for k in b.keys)
                    out.append(f"  {ks:<34}", style="bold yellow")
                    out.append(f"{b.label}\n")
                else:
                    out.append(f"  {'(unbound)':<34}", style="dim")
                    out.append(f"{b.label}\n", style="dim")
            out.append("\n")
        out.append("Inside the Navigator\n", style="bold underline")
        for k, what in (("1 2 3 4", "switch tab (or click it)"), ("↑ ↓  j k", "move"),
                        ("Enter / click twice", "open the selected row"),
                        ("Space / click ☐", "show the project in herdr's sidebar (or hide it)"),
                        ("c / x", "new Claude / Codex in the project or worktree"),
                        ("r", "resume a session of this project/worktree"), ("p", "Resume: this project ↔ all"),
                        ("/", "search sessions"), ("b d w i o a", "Agents: filter by state / outside herdr"),
                        ("F5", "refresh (also opens newly active worktrees)"), ("Esc", "clear search / close")):
            out.append(f"  {k:<34}", style="bold yellow")
            out.append(f"{what}\n")
        self.query_one("#keys-body", Static).update(out)

    # ---- actions --------------------------------------------------------------------------
    def active_tab(self) -> str:
        return self.query_one("#tabs", TabbedContent).active

    def focus_table(self) -> None:
        tid = {"projects": "#proj-table", "agents": "#agent-table", "resume": "#resume-table"}.get(self.active_tab())
        if tid:
            self.query_one(tid, DataTable).focus()

    def set_hint(self) -> None:
        hint = {
            "projects": "Click twice (or ⏎) to go there · click ☐/☑ (or Space) to show/hide in herdr's sidebar · c/x new agent · r resume",
            "agents": "Everything running: herdr panes, ⧉ sessions in other terminals, ↳ sub-agents · click twice (or ⏎) to jump",
            "resume": "Type to search · click twice (or ⏎) to resume in its project · ● live jumps there · ⧉ runs elsewhere",
            "keys": "Your live key bindings, read from herdr's defaults + config.toml · F1 opens the Navigator anytime",
        }.get(self.active_tab(), "")
        self.query_one("#hint", Static).update(Text(hint, style="dim"))

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
        if action == "open":
            return tab in ("projects", "agents", "resume")
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
            "btn-claude": lambda: self.action_new("claude"), "btn-codex": lambda: self.action_new("codex"),
            "btn-scope": self.action_toggle_project, "btn-sidebar": self.action_toggle_sidebar,
        }
        if bid in actions:
            actions[bid]()
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
