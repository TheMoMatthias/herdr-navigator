"""Mirror pane: stands in, inside herdr, for an agent session running in another terminal.

It reports that session's state to herdr (so it appears in the sidebar Agents panel under its
real name, with working/idle/blocked), shows a live feed of what the session is doing and its
running sub-agents, and once the other window exits it offers to resume the session right here.

Env: NAV_SESSION=<cli>:<session id>, HERDR_PANE_ID (set by herdr).
"""
from __future__ import annotations

import os
import sys
import time

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Static

from . import herdr, live, model, settings
from .sessions import _loads, load_sessions

SOURCE = f"plugin:{settings.PLUGIN_ID}"
STATE_STYLE = {"working": "yellow", "idle": "dim", "blocked": "bold red", "ended": "magenta"}


def feed(path: str, limit: int = 40) -> list[Text]:
    """Readable recent events from a Claude or Codex transcript tail."""
    out: list[Text] = []
    for d in live._tail_records(path, 256 * 1024):
        t = d.get("type")
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        if t == "user" and not d.get("isMeta") and not d.get("isSidechain"):
            c = (d.get("message") or {}).get("content")
            text = c if isinstance(c, str) else next(
                (x.get("text", "") for x in (c or []) if isinstance(x, dict) and x.get("type") == "text"), "")
            if text and not text.lstrip().startswith("<"):
                out.append(Text("👤 " + live._short(text, 300), style="bold cyan"))
        elif t == "assistant":
            for part in (d.get("message") or {}).get("content") or []:
                if part.get("type") == "text" and part.get("text", "").strip():
                    out.append(Text("💬 " + live._short(part["text"], 400)))
                elif part.get("type") == "tool_use":
                    out.append(Text("▸ " + live._describe_tool(part.get("name", "tool"), part.get("input")),
                                    style="dim"))
        elif t == "event_msg" and p.get("type") == "user_message":
            out.append(Text("👤 " + live._short(p.get("message", ""), 300), style="bold cyan"))
        elif t == "response_item" and p.get("type") in ("function_call", "custom_tool_call"):
            out.append(Text("▸ " + live._describe_tool(p.get("name", "tool"), p.get("arguments") or p.get("input")),
                            style="dim"))
        elif t == "response_item" and p.get("role") == "assistant":
            for part in p.get("content") or []:
                if isinstance(part, dict) and part.get("text"):
                    out.append(Text("💬 " + live._short(part["text"], 400)))
    return out[-limit:]


class Mirror(App):
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    #head { height: auto; padding: 0 1; background: $boost; }
    #subs { height: auto; padding: 0 1; }
    #feed { padding: 0 1; }
    Horizontal { height: 1; margin: 1 0; }
    Horizontal Button { margin: 0 1 0 0; }
    Button { margin: 0 1 0 0; }
    """
    BINDINGS = [
        Binding("r", "resume", "Resume here (after it exits)"),
        Binding("f1", "navigator", "Navigator"),
        Binding("q", "close", "Close mirror"),
    ]

    def __init__(self, cli: str, sid: str) -> None:
        super().__init__()
        self.cli, self.sid = cli, sid
        self.pane = os.environ.get("HERDR_PANE_ID", "")
        self.session = None
        self.last_state = ""
        self.ended = False

    def compose(self) -> ComposeResult:
        yield Static(id="head")
        yield Static(id="subs")
        with Horizontal():
            yield Button("▶ Resume here", id="resume", disabled=True, compact=True, variant="primary")
            yield Button("☰ Navigator", id="nav", compact=True)
            yield Button("✕ Close mirror", id="close", compact=True)
        yield VerticalScroll(Static(id="feed"), id="feedbox")
        yield Footer()

    def on_mount(self) -> None:
        self.tick()
        self.set_interval(3, self.tick)

    @work(thread=True, exclusive=True)
    def tick(self) -> None:
        if self.session is None:
            self.session = next((s for s in load_sessions(include_hidden=True)
                                 if s.cli == self.cli and s.id == self.sid), None)
        running = next((r for r in live.running([self.session] if self.session else [])
                        if r.session_id == self.sid), None)
        if running is None and self.cli == "claude":
            running = next((r for r in live._claude_registry(time.time()) if r.session_id == self.sid), None)
            if running and self.session:
                running.transcript = self.session.path
                running.activity = live.activity(self.session.path)
                running.subagents = live.claude_subagents(self.session.path, time.time())
        self.call_from_thread(self.render_state, running)

    def render_state(self, r) -> None:
        s = self.session
        name = model.session_name(r, s, "") or (s.title if s else self.sid[:8])
        state = model.EXTERNAL_STATUS.get(r.status, "unknown") if r else "ended"
        self.ended = r is None
        head = Text()
        head.append(f"{name}", style="bold")
        head.append(f"   {self.cli}", style="dim")
        head.append(f"   {state}", style=STATE_STYLE.get(state, ""))
        proj = r.project.label if r else (s.project.label if s else "")
        head.append(f"   {proj}\n", style="cyan")
        if r:
            head.append(f"Running in another terminal window (pid {r.pid}). This pane mirrors it live; "
                        "type in that window. ", style="dim")
            head.append("Once it exits, ▶ Resume continues it here.", style="dim")
        else:
            head.append("The session has exited its other window. ▶ Resume continues it here in herdr.",
                        style="bold green")
        self.query_one("#head", Static).update(head)
        subs = Text()
        for sa in (r.subagents if r else []):
            subs.append(f"↳ {sa.name}", style="yellow")
            subs.append(f" ({sa.kind or 'agent'}{', ' + sa.model if sa.model else ''}) {sa.description[:50]}  ")
            subs.append(f"{sa.activity[:60]}\n", style="dim")
        self.query_one("#subs", Static).update(subs)
        self.query_one("#resume", Button).disabled = not self.ended or s is None
        self.query_one("#resume", Button).variant = "success" if self.ended else "default"
        if s:
            lines = feed(s.path)
            self.query_one("#feed", Static).update(Text("\n").join(lines) if lines else Text("(no activity yet)"))
            self.query_one("#feedbox", VerticalScroll).scroll_end(animate=False)
        self.report(state, name, r.activity if r else "", len(r.subagents) if r else 0, proj)

    def report(self, state: str, name: str, activity: str, subs: int, proj: str) -> None:
        if not self.pane:
            return
        key = (state, name, activity, subs, proj, int(time.time() // 60))  # re-assert every minute
        if key == self.last_state:
            return
        self.last_state = key
        if state == "ended":
            herdr.run("pane", "release-agent", self.pane, "--source", SOURCE, "--agent", self.cli, check=False)
        else:
            herdr.run("pane", "report-agent", self.pane, "--source", SOURCE, "--agent", self.cli,
                      "--state", state if state in ("idle", "working", "blocked") else "unknown",
                      "--message", activity[:120] or name, check=False)
        herdr.run("pane", "report-metadata", self.pane, "--source", SOURCE,
                  "--token", f"session={name}", "--token", f"project={proj}",
                  # herdr's Agents panel tree: mirrors sort after the project groups, named
                  "--token", f"line=↗ {name[:34]}", "--token", f"lane=    {proj}",
                  *(["--token", f"subagents=↳{subs}"] if subs else ["--clear-token", "subagents"]),
                  "--token", "where=↗ other window" if state != "ended" else "where=ended", check=False)

    def action_resume(self) -> None:
        if not self.ended or not self.session:
            self.notify("Still running in its other window. Exit it there first, so the "
                        "conversation isn't forked.", severity="warning")
            return
        s = self.session
        cmd = s.resume_command()
        # this pane becomes the session: leave the app, hand the terminal to the resumed CLI
        self.exit(("resume", s.cwd, cmd))

    def action_navigator(self) -> None:
        herdr.run("plugin", "pane", "open", "--plugin", settings.PLUGIN_ID, "--entrypoint", "navigator",
                  "--placement", "overlay", check=False)

    def action_close(self) -> None:
        self.exit(("close",))

    @on(Button.Pressed)
    def _b(self, ev: Button.Pressed) -> None:
        {"resume": self.action_resume, "nav": self.action_navigator, "close": self.action_close}[ev.button.id]()


def main() -> None:
    cli, _, sid = os.environ.get("NAV_SESSION", ":").partition(":")
    if not sid:
        print("NAV_SESSION=<cli>:<session id> is required")
        sys.exit(2)
    result = Mirror(cli, sid).run()
    pane = os.environ.get("HERDR_PANE_ID", "")
    herdr.run("pane", "release-agent", pane, "--source", SOURCE, "--agent", cli, check=False) if pane else None
    if result and result[0] == "resume":
        _, cwd, cmd = result
        from . import mirrors
        mirrors.forget(pane)  # from now on this pane is a real agent pane
        os.chdir(cwd)
        # replace this process with the agent CLI so the pane *is* the resumed session
        if os.name == "nt":
            import subprocess
            sys.exit(subprocess.call(cmd, shell=True))
        os.execvp("/bin/sh", ["/bin/sh", "-c", cmd])


if __name__ == "__main__":
    main()
