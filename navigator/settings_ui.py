"""The Navigator's ⚙ Settings tab: everything you set up once, in one place.

A section list on the left, the section on the right:
  Logon      reopen ticked sessions at logon, and how the auto-tick picks them
  Accounts   sign in / out per CLI, switch between saved logins
  Alerts     phone alerts: Telegram, ntfy, a webhook (Discord, Slack...), WhatsApp
  Prompts    one-click prompts and the hand-off text
  General    sidebar, workspaces, which sessions are listed
  Keys       every key, the Navigator's and herdr's
Changes save on their own when you leave a field or tick a box: into navigator.toml, comments kept.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, ContentSwitcher, DataTable, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from . import accounts, autostart, profiles, prompts, settings
from .ui import Btn, Choice, Field, Tick

SECTIONS = [("logon", "⏻  Logon restore"), ("accounts", "◉  Accounts"), ("alerts", "◔  Phone alerts"),
            ("prompts", "☰  Prompts"), ("general", "⚙  General"), ("updates", "⟳  Updates"), ("keys", "⌨  Keys")]


def open_file(path: Path) -> None:
    """Open a file in the system's default app (your editor for .toml, .log, .json)."""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _int(value: str, default: int) -> int:
    try:
        return max(0, int(float(value)))
    except ValueError:
        return default


def _num(value: str, default: float) -> float:
    try:
        return max(0.0, float(value))
    except ValueError:
        return default


class SettingsPane(Horizontal):
    DEFAULT_CSS = """
    SettingsPane { height: 1fr; }
    #set-nav { width: 22; height: 1fr; border: none; border-right: tall $primary 30%; padding: 0; }
    #set-body { width: 1fr; height: 1fr; padding: 0 2; }
    #set-body > VerticalScroll { height: 1fr; }
    .acct { height: auto; margin: 0 0 1 0; padding: 0 0 0 1; border-left: tall $primary 40%; }
    .acct-row { height: 1; margin: 0 0 1 0; }
    .acct-row.last { margin: 0; }
    .acct-name { width: 10; text-style: bold; }
    .acct-who { width: 1fr; color: $text-muted; }
    .acct-row Select { width: 1fr; max-width: 34; }
    .acct-row Button { margin: 0 1 0 0; min-width: 3; width: auto; }
    #al-status, #logon-status, #acct-note { height: auto; margin: 0 0 1 0; }
    #al-tg-status { height: auto; margin: 0 0 1 20; }
    #prompt-table { height: auto; max-height: 14; margin: 0 0 1 0; }
    #keys-body { padding: 0; }
    """

    def __init__(self, section: str = "logon", **kw) -> None:
        super().__init__(**kw)
        self.section = section if section in dict(SECTIONS) else "logon"

    def compose(self) -> ComposeResult:
        yield OptionList(*[Option(label, id=key) for key, label in SECTIONS], id="set-nav")
        with ContentSwitcher(initial=self.section, id="set-body"):
            with VerticalScroll(id="logon"):
                yield from self._logon()
            with VerticalScroll(id="accounts"):
                yield from self._accounts()
            with VerticalScroll(id="alerts"):
                yield from self._alerts()
            with VerticalScroll(id="prompts"):
                yield from self._prompts()
            with VerticalScroll(id="general"):
                yield from self._general()
            with VerticalScroll(id="updates"):
                yield from self._updates()
            with VerticalScroll(id="keys"):
                yield Static(id="keys-body")

    def on_mount(self) -> None:
        nav = self.query_one("#set-nav", OptionList)
        nav.highlighted = [k for k, _ in SECTIONS].index(self.section)
        self.show_logon_status()
        self.show_alert_status()
        self.fill_prompts()
        a = settings.load().alerts
        if a.get("telegram_bot_token") and not a.get("telegram_chat_id"):
            self.tg_status("Token saved, chat not connected yet: press Connect.", "yellow")
        elif a.get("telegram_chat_id"):
            self.tg_status("● Connected.", "green")

    def show_section(self, key: str) -> None:
        if key not in dict(SECTIONS):
            return
        self.section = key
        self.query_one("#set-body", ContentSwitcher).current = key
        nav = self.query_one("#set-nav", OptionList)
        idx = [k for k, _ in SECTIONS].index(key)
        if nav.highlighted != idx:
            nav.highlighted = idx
        if key == "accounts":
            self.load_accounts()
        if key == "updates":
            self.check_updates()

    @on(OptionList.OptionHighlighted, "#set-nav")
    def _nav(self, ev: OptionList.OptionHighlighted) -> None:
        self.show_section(ev.option.id or "logon")

    # ---- logon restore ----------------------------------------------------------------------
    def _logon(self) -> ComposeResult:
        r = settings.load().restore
        yield Static(Text.assemble(("Reopen your sessions when you log on", ""), ("   ·   changes save on their own", "dim not bold")), classes="section-title")
        yield Static("", id="logon-status")
        yield Tick("Reopen the ticked sessions every time I log on", autostart.installed(), id="lg-auto")
        yield Static("Tick or untick sessions in the Sessions tab (3). The ones you did not choose yourself are "
                     "ticked automatically: the newest few per lane (the main checkout and each worktree).",
                     classes="hint")
        with Horizontal(classes="form-row"):
            yield Static("Auto-tick newest", classes="lbl")
            yield Field(str(r.get("per_lane", 3)), id="lg-per-lane", classes="num")
            yield Static("per lane · last", classes="unit")
            yield Field(str(r.get("window_days", 3)), id="lg-window", classes="num")
            yield Static("days", classes="unit")
        with Horizontal(classes="form-row"):
            yield Static("Open at most", classes="lbl")
            yield Field(str(r.get("max_sessions", 30)), id="lg-max", classes="num")
            yield Static("sessions,", classes="unit")
            yield Field(str(r.get("gap_seconds", 1.5)), id="lg-gap", classes="num")
            yield Static("seconds apart", classes="unit")
        with Horizontal(classes="form-row"):
            yield Static("Start", classes="lbl")
            yield Field(str(r.get("logon_delay_seconds", 20)), id="lg-delay", classes="num")
            yield Static("seconds after logon", classes="unit")
        yield Tick("Claude: restore each session under its own name (-n)",
                   bool(r.get("claude_name", True)), id="lg-name")
        yield Tick("Claude: Remote Control on (use sessions from your phone)",
                   bool(r.get("claude_remote_control", False)), id="lg-rc")
        with Horizontal(classes="bar"):
            yield Btn("▶ Open ticked now", id="lg-open", tooltip="Open every ticked session that is not running yet")
            yield Btn("📄 Restore log", id="lg-log", tooltip="What the last restore did")

    def show_logon_status(self) -> None:
        on_ = autostart.installed()
        t = Text.assemble(("● on" if on_ else "○ off", "bold green" if on_ else "dim"),
                          ("  ·  " + ("a logon task reopens the ticked sessions in herdr" if on_
                                      else "nothing reopens at logon"), "dim"))
        self.query_one("#logon-status", Static).update(t)

    @on(Checkbox.Changed, "#lg-auto")
    def _autostart(self, ev: Checkbox.Changed) -> None:
        if ev.value == autostart.installed():
            return
        msg = autostart.install() if ev.value else autostart.uninstall()
        self.app.notify(msg, severity="error" if msg.startswith("✗") else "information")
        self.show_logon_status()
        self.app.query_one("StartupPane").sync_autostart()

    def _saved(self, what: str) -> None:
        self.app.notify(f"✓ {what} saved", timeout=1.5)

    def save_logon(self) -> None:
        r = settings.load().restore
        v = lambda i: self.query_one(i, Input).value.strip()
        new = {
            "per_lane": _int(v("#lg-per-lane"), int(r["per_lane"])),
            "window_days": _int(v("#lg-window"), int(r["window_days"])),
            "max_sessions": _int(v("#lg-max"), int(r["max_sessions"])),
            "gap_seconds": _num(v("#lg-gap"), float(r["gap_seconds"])),
            "logon_delay_seconds": _int(v("#lg-delay"), int(r["logon_delay_seconds"])),
            "claude_name": self.query_one("#lg-name", Checkbox).value,
            "claude_remote_control": self.query_one("#lg-rc", Checkbox).value,
        }
        if all(r.get(k) == val for k, val in new.items()):
            return
        settings.save_values("restore", new)
        self._saved("Logon restore")
        self.app.load_world()

    # ---- accounts -----------------------------------------------------------------------------
    def _accounts(self) -> ComposeResult:
        yield Static("Accounts per CLI", classes="section-title")
        yield Static("To add another account: 💾 save the current login as a profile first (so you can come "
                     "back to it), then 🔑 sign in with the other account. The sign-in opens in a new tab; when it "
                     "is done the Navigator offers to restart that CLI's sessions on the new login. ⇄ Switch moves "
                     "between saved profiles without signing in again.", classes="hint", id="acct-note")
        for cli in accounts.clis():
            with Vertical(classes="acct"):
                with Horizontal(classes="acct-row"):
                    yield Static(cli, classes="acct-name")
                    yield Static("…", id=f"acct-who-{cli}", classes="acct-who")
                if profiles.supported(cli):
                    with Horizontal(classes="acct-row"):
                        yield Choice([], prompt="saved profiles", id=f"acct-prof-{cli}")
                        yield Btn("⇄ Switch to it", id=f"acct-switch-{cli}",
                                  tooltip="Switch to the chosen profile, then relaunch")
                        yield Btn("💾 Save current login", id=f"acct-save-{cli}",
                                  tooltip="Save the current login as a profile you can switch back to")
                with Horizontal(classes="acct-row last"):
                    yield Btn("🔑 Sign in with another account", id=f"acct-in-{cli}")
                    yield Btn("Sign out", id=f"acct-out-{cli}")

    @work(thread=True, group="accounts")
    def load_accounts(self) -> None:
        for cli in accounts.clis():
            who = accounts.status(cli)
            self.app.call_from_thread(self.show_account, cli, who)

    def show_account(self, cli: str, who: str) -> None:
        self.who = {**getattr(self, "who", {}), cli: who}
        act = profiles.active(cli)
        out_ = who.startswith("not ") or who in ("?", "")
        self.query_one(f"#acct-who-{cli}", Static).update(
            Text.assemble(("○ " if out_ else "● ", "bold red" if out_ else "bold green"), who,
                          (f"  ·  profile {act}" if act else "", "cyan")))
        sign_in = self.query_one(f"#acct-in-{cli}", Button)
        sign_in.label = "🔑 Sign in" if out_ else "🔑 Sign in with another account"
        sign_in.variant = "primary" if out_ else "default"
        self.query_one(f"#acct-out-{cli}", Button).disabled = out_
        if profiles.supported(cli):
            sel = self.query_one(f"#acct-prof-{cli}", Select)
            sel.set_options([(f"{n}  {profiles.label(cli, n)}"[:40], n) for n in profiles.names(cli)])
            if act in profiles.names(cli):
                sel.value = act

    def save_profile(self, cli: str) -> None:
        from .startup_ui import Prompt

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
            self.app.open_relaunch(cli)

    # ---- alerts -------------------------------------------------------------------------------
    def _alerts(self) -> ComposeResult:
        c = settings.load().alerts
        g = lambda k: str(c.get(k, "") or "")
        yield Static(Text.assemble(("Alerts to your phone", ""), ("   ·   changes save on their own", "dim not bold")), classes="section-title")
        yield Static("", id="al-status")
        yield Static("When an agent has waited on you too long, and after the logon restore. Messages carry only "
                     "a session's name, its project and how long it waited. Use any number of channels.",
                     classes="hint")
        yield Static(Text.assemble(("Telegram", "bold"), ("   1. In Telegram open @BotFather, send /newbot, copy the "
                                                         "token.  2. Paste it below: the Navigator checks it and "
                                                         "opens your bot.  3. Press Start there. A confirmation "
                                                         "arrives in the chat.", "dim")), classes="hint")
        with Horizontal(classes="form-row"):
            yield Static("Bot token", classes="lbl")
            yield Field(g("telegram_bot_token"), password=True, placeholder="paste the token from @BotFather",
                        id="al-tg")
            yield Btn("Connect", id="al-tg-connect", variant="primary",
                      tooltip="Check the token, open your bot, wait for Start, send a confirmation")
        yield Static("", id="al-tg-status")
        yield Static(Text.assemble(("ntfy", "bold"), ("   Free app (ntfy.sh), no account: subscribe to the same "
                                                     "topic in the app.", "dim")), classes="hint")
        with Horizontal(classes="form-row"):
            yield Static("ntfy topic", classes="lbl")
            yield Field(g("ntfy_topic"), placeholder="a name only you know", id="al-ntfy")
            yield Btn("Random", id="al-ntfy-random")
        yield Static(Text.assemble(("Webhook", "bold"), ("   Discord, Slack, Teams, Mattermost, Google Chat: an "
                                                        "incoming-webhook URL.", "dim")), classes="hint")
        with Horizontal(classes="form-row"):
            yield Static("Webhook URL", classes="lbl")
            yield Field(g("webhook_url"), password=True, placeholder="https://…", id="al-hook")
        yield Static(Text.assemble(("WhatsApp", "bold"), ("   Through CallMeBot (free, unofficial): get your API "
                                                         "key at callmebot.com.", "dim")), classes="hint")
        with Horizontal(classes="form-row"):
            yield Static("WhatsApp", classes="lbl")
            yield Field(g("whatsapp_phone"), placeholder="phone, +49…", id="al-wa-phone", classes="short")
            yield Field(g("whatsapp_apikey"), password=True, placeholder="CallMeBot API key", id="al-wa-key")
        with Horizontal(classes="form-row"):
            yield Static("Alert after waiting", classes="lbl")
            yield Field(str(c.get("blocked_minutes", 10)), id="al-min", classes="num")
            yield Static("minutes (0 = never)", classes="unit")
        yield Tick("Also send the logon restore's result", bool(c.get("on_restore", True)), id="al-restore")
        yield Tick("Toast in herdr when an agent opens a question",
                   bool(c.get("toast_reply", True)), id="al-toast")
        with Horizontal(classes="bar"):
            yield Btn("Send test", id="al-test", variant="primary", tooltip="A test message to every channel set up")

    def show_alert_status(self, extra: str = "") -> None:
        from . import alerts
        t = Text()
        for ch, ok in alerts.channels().items():
            t.append(f"{'☑' if ok else '☐'} {ch}   ", style="bold green" if ok else "dim")
        if extra:
            t.append("\n" + extra, style="bold")
        self.query_one("#al-status", Static).update(t)

    def save_alerts(self) -> None:
        from . import alerts
        cur = settings.load().alerts
        new = self._alert_values()
        if new["telegram_bot_token"] != str(cur.get("telegram_bot_token", "") or ""):
            new["telegram_chat_id"] = ""  # another bot: its chat has to be found again
        if all(cur.get(k) == val for k, val in new.items()):
            return
        alerts.save(new)
        self.show_alert_status()
        self._saved("Alerts")

    def _alert_values(self) -> dict:
        v = lambda i: self.query_one(i, Input).value.strip()
        return {"telegram_bot_token": v("#al-tg"), "ntfy_topic": v("#al-ntfy"), "webhook_url": v("#al-hook"),
                "whatsapp_phone": v("#al-wa-phone"), "whatsapp_apikey": v("#al-wa-key"),
                "blocked_minutes": _int(v("#al-min"), 10),
                "on_restore": self.query_one("#al-restore", Checkbox).value,
                "toast_reply": self.query_one("#al-toast", Checkbox).value}

    @work(thread=True, group="alerts")
    def run_test_send(self) -> None:
        from . import alerts
        res = alerts.send("herdr navigator", "Test alert: alerts reach you here.", "white_check_mark")
        msg = "  ".join(f"{k}: {'delivered' if ok else 'FAILED'}" for k, ok in res.items()) or "no channel set up yet"
        self.app.call_from_thread(self.show_alert_status, msg)

    def tg_status(self, text: str, style: str = "") -> None:
        self.query_one("#al-tg-status", Static).update(Text(text, style=style))

    @work(thread=True, exclusive=True, group="telegram")
    def connect_telegram(self, token: str) -> None:
        """Check the token, open the bot in Telegram, wait until you pressed Start, then confirm
        in the chat. Telegram lets a bot write to you only after you wrote to it first."""
        import time
        import webbrowser
        from textual.worker import get_current_worker
        from . import alerts
        say = lambda text, style="": self.app.call_from_thread(self.tg_status, text, style)
        token = token.strip()
        if not token:
            say("Paste the bot token first.", "yellow")
            return
        say("Checking the token with Telegram…", "dim")
        bot, err = alerts.telegram_bot_info(token)
        if err:
            say("✗ " + err, "bold red")
            self.app.call_from_thread(self.app.notify, err, title="Telegram", severity="error")
            return
        chat, who = alerts.telegram_find_chat(token)
        if not chat:
            link = f"https://t.me/{bot}?start=herdr" if bot else ""
            say(f"✓ Token works (@{bot}). Telegram is opening your bot: press Start there. Waiting…\n"
                f"   Nothing opened? Open {link or 'your bot'} yourself.", "bold yellow")
            if link:
                try:
                    webbrowser.open(link)
                except Exception:
                    pass
            worker = get_current_worker()
            deadline = time.time() + 300
            while not chat and time.time() < deadline:
                if worker.is_cancelled:
                    return
                time.sleep(2.5)
                chat, who = alerts.telegram_find_chat(token)
            if not chat:
                say(f"No Start from you within 5 minutes. Press Start in @{bot}, then Connect.", "yellow")
                return
        values = {**self._alert_values(), "telegram_bot_token": token, "telegram_chat_id": chat}
        alerts.save(values)
        ok = alerts._send_telegram("✅ herdr navigator connected",
                                   "Alerts arrive here: an agent waiting on you, and the logon restore's result.")
        if ok:
            say(f"● Connected to {who or chat}: a confirmation is in the chat.", "bold green")
            self.app.call_from_thread(self.app.notify, f"Telegram connected ({who or chat})", title="Alerts")
        else:
            say("Found your chat, but the confirmation did not go through. Try Send test.", "bold red")
        self.app.call_from_thread(self.show_alert_status)

    @on(Input.Changed, "#al-tg")
    def _tg_changed(self, ev: Input.Changed) -> None:
        """A pasted token connects by itself."""
        from . import alerts
        if not ev.input.has_focus:  # the saved value filling the field at start-up is not a paste
            return
        tok = ev.value.strip()
        cur = settings.load().alerts
        if (alerts.looks_like_telegram_token(tok) and tok != getattr(self, "_tg_tried", "")
                and not (tok == cur.get("telegram_bot_token") and cur.get("telegram_chat_id"))):
            self._tg_tried = tok
            self.connect_telegram(tok)

    # ---- prompts ------------------------------------------------------------------------------
    def _prompts(self) -> ComposeResult:
        yield Static("One-click prompts", classes="section-title")
        yield Static("Agents › ☰ Prompts sends one to the selected agent, or to every ☑ ticked agent.",
                     classes="hint")
        yield DataTable(id="prompt-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="form-row"):
            yield Static("New prompt", classes="lbl")
            yield Field(placeholder="name", id="pr-name", classes="short")
            yield Field(placeholder="the text to send", id="pr-text")
            yield Btn("＋ Add", id="pr-add", variant="primary")
        with Horizontal(classes="bar"):
            yield Btn("🗑 Delete selected", id="pr-del", tooltip="Prompts you added here; the file's own stay")
        yield Static("Compact instructions", classes="section-title")
        yield Static("⇣ Compact and prefix+shift+c in herdr send /compact plus this to Claude Code, so the summary "
                     "keeps what matters. Other CLIs get a plain /compact. Empty = plain everywhere.", classes="hint")
        with Horizontal(classes="form-row"):
            yield Static("Keep", classes="lbl")
            yield Field(str(settings.load().compact.get("instructions", "")), id="pr-compact")
        yield Static("Hand-off text", classes="section-title")
        yield Static("What ⇢ Hand off sends to the other agent. {name} {cli} {project} {answer}", classes="hint")
        with Horizontal(classes="form-row"):
            yield Static("Template", classes="lbl")
            yield Field(settings.load().handoff.replace("\n", "\\n"), id="pr-handoff")

    def fill_prompts(self) -> None:
        t = self.query_one("#prompt-table", DataTable)
        if not t.columns:
            t.add_columns("Name", "Sends", "From")
        t.clear()
        mine = prompts.saved()
        for name, text in prompts.all_().items():
            t.add_row(Text(name, style="bold"), text.replace("\n", " ")[:48],
                      Text("added here" if name in mine else "settings file", style="dim"), key=name)
        self.query_one("#prompt-table", DataTable).styles.height = min(14, t.row_count + 1)

    # ---- general ------------------------------------------------------------------------------
    def _general(self) -> ComposeResult:
        s = settings.load()
        yield Static(Text.assemble(("General", ""), ("   ·   changes save on their own", "dim not bold")), classes="section-title")
        yield Tick("Sidebar: open worktrees that have a running agent", s.open_active_worktrees, id="gn-wt")
        yield Tick("Sidebar: mirror agents that run outside herdr (read-only)", s.mirror_outside, id="gn-mirror")
        yield Tick("Name new workspaces after their project automatically", s.auto_name, id="gn-autoname")
        yield Tick("Hide sub-agent sessions in the lists", s.hide_subagents, id="gn-hidesub")
        yield Tick("Sidebar: sort Spaces by running, need, recent", s.sort_spaces,
                   id="gn-sortspaces")
        from . import termfont
        size = termfont.get()
        with Horizontal(classes="form-row"):
            yield Static("Font size", classes="lbl")
            yield Btn("A−", id="gn-font-minus", disabled=size is None, tooltip="Smaller text in every tab")
            yield Static(str(size) if size else "—", id="gn-font-val", classes="unit")
            yield Btn("A+", id="gn-font-plus", disabled=size is None, tooltip="Larger text in every tab")
            yield Static("Windows Terminal, all tabs" if size else "use your terminal's zoom: Ctrl + / Ctrl −",
                         classes="unit")
        with Horizontal(classes="form-row"):
            yield Static("List sessions", classes="lbl")
            yield Static("of the last", classes="unit")
            yield Field(str(s.max_age_days), id="gn-age", classes="num")
            yield Static("days", classes="unit")
        with Horizontal(classes="form-row"):
            yield Static("herdr sidebar width", classes="lbl")
            yield Btn("◂ narrower", id="sbw-minus")
            yield Btn("wider ▸", id="sbw-plus")
        with Horizontal(classes="bar"):
            yield Btn("📂 Open settings file", id="gn-file",
                      tooltip="navigator.toml: launch commands, projects, hidden sessions, context windows…")
        yield Static(Text(f"Settings: {settings.settings_path()}\nState:    {settings.state_dir()}", style="dim"))

    def save_general(self) -> None:
        val = lambda i: self.query_one(i, Checkbox).value
        s = settings.load()
        age = _int(self.query_one("#gn-age", Input).value, s.max_age_days)
        new = (val("#gn-wt"), val("#gn-mirror"), val("#gn-autoname"), val("#gn-hidesub"), age, val("#gn-sortspaces"))
        if new == (s.open_active_worktrees, s.mirror_outside, s.auto_name, s.hide_subagents, s.max_age_days,
                   s.sort_spaces):
            return
        settings.save_values("sidebar", {"open_active_worktrees": new[0], "mirror_outside": new[1],
                                         "sort_spaces": new[5]})
        settings.save_values("workspaces", {"auto_name": new[2]})
        settings.save_values("sessions", {"hide_subagents": new[3], "max_age_days": age})
        if new[5] != s.sort_spaces:
            from . import sync
            sync.spawn_background()  # herdr's Spaces follow (or keep their order from now on)
        self._saved("Settings")
        self.app.load_world()

    def save_compact(self) -> None:
        text = " ".join(self.query_one("#pr-compact", Input).value.split())
        if text != str(settings.load().compact.get("instructions", "")):
            settings.save_values("compact", {"instructions": text})
            self._saved("Compact instructions")

    def save_handoff(self) -> None:
        text = self.query_one("#pr-handoff", Input).value.replace("\\n", "\n")
        if text.strip() and text != settings.load().handoff:
            settings.save_values("handoff", {"template": text})
            self._saved("Hand-off text")

    # ---- saving as you go -----------------------------------------------------------------------
    def _autosave(self, wid: str) -> None:
        if wid.startswith("lg-") and wid != "lg-auto":
            self.save_logon()
        elif wid.startswith("al-"):
            self.save_alerts()
        elif wid.startswith("gn-"):
            self.save_general()
        elif wid == "pr-handoff":
            self.save_handoff()
        elif wid == "pr-compact":
            self.save_compact()

    def on_descendant_focus(self, ev) -> None:
        if isinstance(ev.widget, Input):
            ev.widget._nav_orig = ev.widget.value  # what Esc puts back

    @on(Input.Blurred)
    def _blurred(self, ev: Input.Blurred) -> None:
        self._autosave(ev.input.id or "")

    @on(Input.Submitted)
    def _submitted(self, ev: Input.Submitted) -> None:
        if ev.input.id not in ("al-tg", "pr-text", "pr-name"):
            self._autosave(ev.input.id or "")

    @on(Checkbox.Changed)
    def _checked(self, ev: Checkbox.Changed) -> None:
        self._autosave(ev.checkbox.id or "")

    # ---- updates ------------------------------------------------------------------------------
    def _updates(self) -> ComposeResult:
        from . import updater
        yield Static("Updates", classes="section-title")
        yield Static(Text.assemble(("Installed ", "dim"), (updater.installed_version(), "bold"),
                                   ("   ·   from ", "dim"), (str(updater.ROOT), "dim")), id="up-version")
        yield Static("Checking GitHub…", id="up-status")
        with Horizontal(classes="bar"):
            yield Btn("⬇ Update now", id="up-go", variant="primary", disabled=True,
                      tooltip="Fetch the latest version and switch to it (never over local changes)")
            yield Btn("⟳ Check again", id="up-check")
        yield Static("", id="up-new")
        yield Static("After an update, close the Navigator (Esc) and open it again to use the new version. "
                     "Updates only fast-forward: local changes or local commits stop them, nothing is overwritten.",
                     classes="hint")

    @work(thread=True, exclusive=True, group="updates")
    def check_updates(self) -> None:
        from . import updater
        self.app.call_from_thread(self.query_one("#up-status", Static).update, Text("Checking GitHub…", style="dim"))
        st = updater.check()
        self.app.call_from_thread(self.show_updates, st)

    def show_updates(self, st) -> None:
        status = self.query_one("#up-status", Static)
        go = self.query_one("#up-go", Button)
        new = self.query_one("#up-new", Static)
        go.disabled = not (st.ok and st.behind and not st.dirty and not st.ahead)
        if not st.ok:
            status.update(Text("✗ " + st.why, style="bold red"))
            new.update("")
            return
        if st.behind:
            msg = Text.assemble((f"⬆ {st.latest} is available", "bold green"),
                                (f"  ({st.behind} change{'s' * (st.behind != 1)})", "dim"))
            if st.dirty:
                msg.append(f"\nLocal changes block the update: {', '.join(st.dirty[:4])}", style="yellow")
            if st.ahead:
                msg.append(f"\nThis checkout has {st.ahead} commit(s) of its own: merge by hand", style="yellow")
            status.update(msg)
            new.update(Text("\n".join("  • " + c for c in st.new), style=""))
        else:
            extra = f"  ·  {st.ahead} local commit(s) not on GitHub yet" if st.ahead else ""
            status.update(Text.assemble(("● Up to date", "bold green"), (f"  ({st.version}){extra}", "dim")))
            new.update("")

    @work(thread=True, exclusive=True, group="updates")
    def run_update(self) -> None:
        from . import updater
        self.app.call_from_thread(self.query_one("#up-status", Static).update, Text("Updating…", style="bold"))
        msg = updater.update()
        bad = msg.startswith("✗")
        self.app.call_from_thread(self.app.notify, msg + ("" if bad else "  Close (Esc) and reopen the Navigator."),
                                  title="Update", severity="error" if bad else "information", timeout=12)
        self.app.call_from_thread(self.query_one("#up-version", Static).update,
                                  Text.assemble(("Installed ", "dim"), (updater.installed_version(), "bold")))
        self.check_updates()

    # ---- buttons ------------------------------------------------------------------------------
    def on_button_pressed(self, ev) -> None:
        bid = ev.button.id or ""
        ev.stop()
        if bid == "lg-open":
            self.app.query_one("StartupPane").open_ticked()
        elif bid == "lg-log":
            log = settings.state_dir() / "restore.log"
            if log.exists():
                open_file(log)
            else:
                self.app.notify("No restore has run yet.")
        elif bid.startswith("acct-switch-"):
            self.switch_profile(bid[12:])
        elif bid.startswith("acct-save-"):
            self.save_profile(bid[10:])
        elif bid.startswith("acct-in-"):
            cli = bid[8:]
            self.app.finish(lambda: accounts.sign_in(cli))
        elif bid.startswith("acct-out-"):
            cli = bid[9:]
            self.app.finish(lambda: accounts.sign_out(cli))
        elif bid == "al-ntfy-random":
            from . import alerts
            self.query_one("#al-ntfy", Input).value = alerts.random_topic()
            self.save_alerts()
        elif bid == "al-test":
            self.save_alerts()
            self.run_test_send()
        elif bid == "al-tg-connect":
            self.connect_telegram(self.query_one("#al-tg", Input).value.strip())
        elif bid == "pr-add":
            name = self.query_one("#pr-name", Input).value.strip()
            text = self.query_one("#pr-text", Input).value.strip()
            if not (name and text):
                self.app.notify("A prompt needs a name and a text.", severity="warning")
                return
            prompts.save(name, text)
            self.query_one("#pr-name", Input).value = ""
            self.query_one("#pr-text", Input).value = ""
            self.fill_prompts()
        elif bid == "pr-del":
            t = self.query_one("#prompt-table", DataTable)
            if not t.row_count:
                return
            name = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
            if prompts.delete(name):
                self.fill_prompts()
            else:
                self.app.notify(f"'{name}' lives in the settings file ([prompts]): remove it there.",
                                severity="warning")
        elif bid == "up-check":
            self.check_updates()
        elif bid == "up-go":
            self.run_update()
        elif bid == "gn-file":
            open_file(settings.settings_path())
        elif bid in ("sbw-minus", "sbw-plus"):
            self.app.action_sidebar_width("-6" if bid == "sbw-minus" else "+6")
        elif bid in ("gn-font-minus", "gn-font-plus"):
            from . import termfont
            msg = termfont.set_size((termfont.get() or termfont.DEFAULT) + (-1 if bid == "gn-font-minus" else 1))
            self.query_one("#gn-font-val", Static).update(str(termfont.get() or "—"))
            self.app.notify(msg, severity="error" if msg.startswith("✗") else "information", timeout=4)

    @on(Input.Submitted, "#al-tg")
    def _tg_enter(self, ev: Input.Submitted) -> None:
        self.connect_telegram(ev.value.strip())

    @on(Input.Submitted, "#pr-text")
    def _pr_enter(self) -> None:
        self.query_one("#pr-add").press()
