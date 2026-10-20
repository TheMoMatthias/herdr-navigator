"""The Settings tab's text and layout (declutter 2026-10-12), driven headless with Textual's pilot.

SettingsPane is mounted in a small host app with the CLIs, accounts, updater and font mocked, so
nothing here touches a real login, GitHub or the registry.
"""
import asyncio
import threading

import pytest
from textual.app import App
from textual.widgets import Button, Checkbox, Input, OptionList, Select, Static
from textual.worker import WorkerCancelled

from navigator import accounts, alerts, autostart, herdrctl, profiles, projects, settings, termfont, updater
from navigator.settings_ui import SECTIONS, SettingsPane
from navigator.ui import CSS


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_PLUGIN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "navigator.toml").write_text("[hide]\npatterns = []\n", encoding="utf-8")
    settings.load.cache_clear()
    projects.clear_cache()
    monkeypatch.setattr(autostart, "installed", lambda fresh=False: False)
    monkeypatch.setattr(accounts, "clis", lambda: {"claude": {}, "codex": {}})
    monkeypatch.setattr(accounts, "status", lambda cli: "me@x" if cli == "claude" else "not signed in")
    monkeypatch.setattr(profiles, "supported", lambda cli: cli == "claude")
    monkeypatch.setattr(profiles, "names", lambda cli: ["me", "work"])
    monkeypatch.setattr(profiles, "active", lambda cli: "me")
    monkeypatch.setattr(profiles, "label", lambda cli, n: f"{n} · detail")
    monkeypatch.setattr(termfont, "get", lambda: None)
    monkeypatch.setattr(updater, "check", lambda fetch=True: updater.State(True, version="1.0", latest="1.0"))
    monkeypatch.setattr(herdrctl, "status", lambda: herdrctl.Status(True, "", "0.9.5", "0.9.5", True, False))
    monkeypatch.setattr(herdrctl, "latest", lambda timeout=8: "0.9.5")
    monkeypatch.setattr(herdrctl, "agents", lambda: (3, 1))
    yield
    settings.load.cache_clear()
    projects.clear_cache()


class Host(App):
    CSS = CSS

    def __init__(self, section="logon"):
        super().__init__()
        self.section = section
        self.finished = []
        self.finish_kw = []
        self.toasts = []

    def compose(self):
        yield SettingsPane(self.section, id="sp")

    def render_keys(self):
        pass

    def load_world(self):
        pass

    def finish(self, fn, jump=False, busy="", after=None):
        self.finished.append(fn)
        self.finish_kw.append({"busy": busy, "after": after})

    def notify(self, message, *a, **kw):
        self.toasts.append(str(message))
        super().notify(message, *a, **kw)

    def action_sidebar_width(self, delta):
        pass


def text_of(w) -> str:
    return str(w.render())


def all_text(pane) -> str:
    """Every Static's text, button label and input placeholder in the pane, one blob."""
    out = [text_of(s) for s in pane.query(Static)]
    out += [str(b.label) for b in pane.query(Button)]
    out += [i.placeholder for i in pane.query(Input)]
    return "\n".join(out)


def run(coro_fn, section="logon", size=(150, 50)):
    async def go():
        app = Host(section)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await coro_fn(app, pilot, app.query_one(SettingsPane))

    asyncio.run(go())


async def settle(app, pilot):
    """Let the background workers (accounts, updates) finish and their UI updates land."""
    for _ in range(4):
        try:
            await app.workers.wait_for_complete()
        except WorkerCancelled:  # an exclusive worker replaced by its successor
            pass
        await pilot.pause()


def test_every_section_mounts_and_shows_without_error():
    async def body(app, pilot, pane):
        for key, _ in SECTIONS:
            pane.show_section(key)
            await settle(app, pilot)
            assert pane.section == key
        assert text_of(pane.query_one("#up-status")).startswith("●")  # the mocked check ran

    run(body)


def test_nav_labels_are_words_only():
    async def body(app, pilot, pane):
        nav = pane.query_one("#set-nav", OptionList)
        for i in range(nav.option_count):
            label = str(nav.get_option_at_index(i).prompt)
            assert label.replace(" ", "").isalpha(), label

    run(body)


def test_no_section_says_changes_save_on_their_own():
    async def body(app, pilot, pane):
        assert "save on their own" not in all_text(pane)
        assert "Logins are saved" not in all_text(pane)

    for section in ("logon", "alerts", "general", "accounts"):
        run(body, section)


def test_logon_has_no_status_line_and_no_open_ticked_button():
    async def body(app, pilot, pane):
        assert not pane.query("#logon-status") and not pane.query("#lg-open")
        blob = all_text(pane)
        assert "Open ticked" not in blob and "nothing reopens" not in blob
        assert "Tick in Sessions (3). The rest are auto-ticked:" in blob
        assert "Restore log" in blob and "📄" not in blob  # the log stays, without the emoji

    run(body, "logon")


def test_accounts_are_two_rows_per_cli_with_the_short_note():
    async def body(app, pilot, pane):
        pane.show_section("accounts")
        await settle(app, pilot)
        assert "Pick an account to switch; its sessions relaunch on it." in text_of(pane.query_one("#acct-note"))
        assert len(text_of(pane.query_one("#acct-note")).split()) <= 11
        for box in pane.query(".acct"):
            assert len(box.query(".acct-row")) == 2
        who = text_of(pane.query_one("#acct-who-claude"))
        assert who.startswith("● me@x") and "profile" not in who
        assert text_of(pane.query_one("#acct-who-codex")).startswith("○ ")
        assert str(pane.query_one("#acct-in-claude", Button).label) == "🔑 Add another account"
        assert str(pane.query_one("#acct-in-codex", Button).label) == "🔑 Sign in"
        assert str(pane.query_one("#acct-save-claude", Button).label) == "Save as…"
        assert pane.query_one("#acct-out-codex", Button).disabled
        assert not pane.query_one("#acct-out-claude", Button).disabled

    run(body, "accounts")


def test_picking_a_profile_switches_but_showing_the_active_one_does_not(monkeypatch):
    calls = []
    monkeypatch.setattr(SettingsPane, "switch_profile", lambda self, cli, name: calls.append((cli, name)))

    async def body(app, pilot, pane):
        pane.show_section("accounts")
        await settle(app, pilot)
        sel = pane.query_one("#acct-prof-claude", Select)
        assert sel.value == "me" and calls == []  # filled programmatically: not a pick
        sel.value = "work"
        await pilot.pause()
        assert calls == [("claude", "work")]
        pane.load_accounts()  # a refresh shows the active profile again
        await settle(app, pilot)
        assert calls == [("claude", "work")]

    run(body, "accounts")


def test_sign_in_and_out_buttons_still_reach_accounts(monkeypatch):
    seen = []
    monkeypatch.setattr(accounts, "sign_in", lambda cli: seen.append(("in", cli)))
    monkeypatch.setattr(accounts, "sign_out", lambda cli: seen.append(("out", cli)))

    async def body(app, pilot, pane):
        pane.show_section("accounts")
        await settle(app, pilot)
        pane.query_one("#acct-in-claude", Button).press()
        pane.query_one("#acct-out-claude", Button).press()
        await pilot.pause()
        app.screen.dismiss("out")  # confirmed in the menu
        await pilot.pause()
        for fn in app.finished:
            fn()
        assert seen == [("in", "claude"), ("out", "claude")]

    run(body, "accounts")


def test_sign_out_asks_first_and_both_actions_refresh_the_rows(monkeypatch):
    from navigator.startup_ui import ContextMenu
    seen, refreshed = [], []
    monkeypatch.setattr(accounts, "sign_in", lambda cli: "in")
    monkeypatch.setattr(accounts, "sign_out", lambda cli: seen.append(cli) or "claude: signed out.")
    monkeypatch.setattr(SettingsPane, "load_accounts", lambda self: refreshed.append(1))

    async def body(app, pilot, pane):
        out = pane.query_one("#acct-out-claude", Button)
        out.press()
        await pilot.pause()
        assert isinstance(app.screen, ContextMenu)
        assert "Sign out of claude?" in str(app.screen.items) and app.finished == []  # nothing ran yet
        await pilot.press("escape")  # cancelled: nothing runs
        await pilot.pause()
        assert app.finished == [] and seen == []
        out.press()
        await pilot.pause()
        app.screen.dismiss("out")
        await pilot.pause()
        assert len(app.finished) == 1 and app.finish_kw[0]["after"] == pane.load_accounts
        assert "new tab" in app.finish_kw[0]["busy"]
        assert "new tab" in app.finished[0]()  # the toast says where it went
        assert seen == ["claude"]
        pane.query_one("#acct-in-claude", Button).press()
        await pilot.pause()
        assert len(app.finished) == 2 and app.finish_kw[1]["after"] == pane.load_accounts

    run(body, "accounts")


def test_sign_out_failure_message_is_passed_through(monkeypatch):
    monkeypatch.setattr(accounts, "sign_out", lambda cli: "✗ no logout command for claude")

    async def body(app, pilot, pane):
        pane._sign_out("claude")
        assert app.finished[0]() == "✗ no logout command for claude"

    run(body, "accounts")


def test_alerts_have_no_checkbox_list_and_hints_live_in_placeholders():
    async def body(app, pilot, pane):
        blob = all_text(pane)
        assert "☑" not in blob and "☐" not in blob
        for gone in ("Free app (ntfy.sh)", "Discord, Slack, Teams, Mattermost", "Through CallMeBot"):
            assert not any(gone in text_of(s) for s in pane.query(Static)), gone
        assert "BotFather" in pane.query_one("#al-tg", Input).placeholder
        assert "ntfy" in pane.query_one("#al-ntfy", Input).placeholder
        assert "webhook" in pane.query_one("#al-hook", Input).placeholder.lower()
        assert "callmebot" in pane.query_one("#al-wa-key", Input).placeholder.lower()

    run(body, "alerts")


def test_telegram_steps_show_only_while_not_connected():
    async def body(app, pilot, pane):
        st = pane.query_one("#al-tg-status")
        assert "/newbot" in text_of(st) and st.display

    run(body, "alerts")


def test_telegram_steps_hidden_once_connected_and_the_label_carries_the_dot():
    settings.save_values("alerts", {"telegram_bot_token": "1:abc", "telegram_chat_id": "42"})
    settings.load.cache_clear()

    async def body(app, pilot, pane):
        st = pane.query_one("#al-tg-status")
        assert "/newbot" not in text_of(st) and not st.display
        assert "●" in text_of(pane.query_one("#al-lbl-telegram"))
        assert "●" not in text_of(pane.query_one("#al-lbl-ntfy"))

    run(body, "alerts")


def test_telegram_token_without_chat_says_so():
    settings.save_values("alerts", {"telegram_bot_token": "1:abc"})
    settings.load.cache_clear()

    async def body(app, pilot, pane):
        assert "chat not connected" in text_of(pane.query_one("#al-tg-status"))
        assert "●" not in text_of(pane.query_one("#al-lbl-telegram"))

    run(body, "alerts")


def test_a_set_up_channel_gets_a_dot_on_its_label_after_saving():
    async def body(app, pilot, pane):
        assert "●" not in text_of(pane.query_one("#al-lbl-ntfy"))
        pane.query_one("#al-ntfy", Input).value = "my-topic"
        pane.save_alerts()
        await pilot.pause()
        assert alerts.channels()["ntfy"]
        assert "●" in text_of(pane.query_one("#al-lbl-ntfy"))

    run(body, "alerts")


def test_test_send_result_shows_beside_the_button():
    async def body(app, pilot, pane):
        pane.show_alert_status("ntfy: delivered")
        assert "ntfy: delivered" in text_of(pane.query_one("#al-status"))

    run(body, "alerts")


def test_prompts_texts():
    async def body(app, pilot, pane):
        blob = all_text(pane)
        assert "Added to /compact for Claude Code. Empty = plain /compact." in blob
        assert "🗑" not in blob and "☑" not in blob
        assert "prefix+shift+c" in (pane.query_one("#pr-compact", Input).tooltip or "")  # the key stays documented

    run(body, "prompts")


def test_general_groups_the_sidebar_ticks_under_one_subtitle():
    async def body(app, pilot, pane):
        blob = all_text(pane)
        assert "Sidebar:" not in blob
        titles = [text_of(s) for s in pane.query(".section-title")]
        assert "Sidebar" in titles
        assert "📂" not in blob
        for i in ("#gn-wt", "#gn-mirror", "#gn-sortspaces", "#gn-autoname", "#gn-hidesub", "#sbw-minus"):
            assert pane.query(i), i

    run(body, "general")


def test_general_ticks_still_save():
    async def body(app, pilot, pane):
        before = settings.load().hide_subagents
        pane.query_one("#gn-hidesub").value = not before
        await pilot.pause()
        settings.load.cache_clear()
        assert settings.load().hide_subagents == (not before)

    run(body, "general")


def test_updates_hint():
    async def body(app, pilot, pane):
        pane.show_section("updates")
        await settle(app, pilot)
        hints = [text_of(s) for s in pane.query_one("#updates").query(".hint")]
        assert hints[-1].startswith("Fast-forward only, from the plugin's own GitHub repo")
        assert hints[0].startswith("Not checked yet")          # the automatic updater has not run here

    run(body, "updates")


def test_automatic_updates_are_on_by_default_and_the_tick_saves():
    async def body(app, pilot, pane):
        pane.show_section("updates")
        await settle(app, pilot)
        box = pane.query_one("#up-auto", Checkbox)
        assert box.value is True and settings.load().auto_update is True
        box.value = False
        await pilot.pause()
        settings.load.cache_clear()
        assert settings.load().auto_update is False
        assert text_of(pane.query_one("#up-autostate")).startswith("Automatic updates are off")

    run(body, "updates")


def test_updates_shows_what_the_automatic_updater_did_last():
    async def body(app, pilot, pane):
        updater._remember(checked=1792489164.0, result="Updated 0.16.3 → 0.16.4 (2 changes).")
        pane.show_section("updates")
        await settle(app, pilot)
        shown = text_of(pane.query_one("#up-autostate"))
        assert "Last automatic check" in shown and "Updated 0.16.3 → 0.16.4" in shown

    run(body, "updates")


def test_logon_fields_still_save():
    async def body(app, pilot, pane):
        pane.query_one("#lg-per-lane", Input).value = "5"
        pane.save_logon()
        settings.load.cache_clear()
        assert settings.load().restore["per_lane"] == 5

    run(body, "logon")


def test_opening_at_another_section_does_not_ping_pong(monkeypatch):
    """The list's own first highlight (Logon) used to answer the requested section's highlight and
    back, forever: every round re-ran show_section (and with it a thread worker per round)."""
    calls = []
    orig = SettingsPane.show_section
    monkeypatch.setattr(SettingsPane, "show_section", lambda self, key: (calls.append(key), orig(self, key))[1])

    async def body(app, pilot, pane):
        await settle(app, pilot)
        assert pane.section == "accounts"
        assert len(calls) <= 3, calls

    run(body, "accounts")


def test_accounts_rows_fit_a_120_column_popup():
    async def body(app, pilot, pane):
        pane.show_section("accounts")
        await settle(app, pilot)
        right = pane.query_one("#set-body").region.right
        for b in pane.query(".acct-row Button"):
            assert b.region.right <= right, (b.id, b.region, right)
            assert b.region.height == 1

    run(body, "accounts", size=(120, 40))


# ---- telegram: one connect per token, fields read on the UI thread ------------------------------
TOKEN = "123456789:AAH-abcdefghijklmnopqrstuvwxyz012345"


def test_telegram_paste_then_enter_connects_once(monkeypatch):
    calls = []
    monkeypatch.setattr(SettingsPane, "_connect_worker", lambda self, tok, fields: calls.append((tok, fields)))

    async def body(app, pilot, pane):
        assert alerts.looks_like_telegram_token(TOKEN)
        tg = pane.query_one("#al-tg", Input)
        tg.focus()
        await pilot.pause()
        tg.value = TOKEN  # the paste
        await pilot.pause()
        assert len(calls) == 1 and calls[0][0] == TOKEN
        assert isinstance(calls[0][1], dict) and "ntfy_topic" in calls[0][1]  # read on the UI thread
        await pilot.press("enter")
        await pilot.pause()
        assert len(calls) == 1  # Enter did not connect a second time
        pane.query_one("#al-tg-connect", Button).press()  # the button still retries on purpose
        await pilot.pause()
        assert len(calls) == 2

    run(body, "alerts")


def test_telegram_enter_on_the_connected_token_does_nothing(monkeypatch):
    settings.save_values("alerts", {"telegram_bot_token": TOKEN, "telegram_chat_id": "42"})
    settings.load.cache_clear()
    calls = []
    monkeypatch.setattr(SettingsPane, "_connect_worker", lambda self, tok, fields: calls.append(tok))

    async def body(app, pilot, pane):
        pane.query_one("#al-tg", Input).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert calls == []

    run(body, "alerts")


def test_telegram_enter_with_a_new_token_connects(monkeypatch):
    calls = []
    monkeypatch.setattr(SettingsPane, "_connect_worker", lambda self, tok, fields: calls.append(tok))

    async def body(app, pilot, pane):
        tg = pane.query_one("#al-tg", Input)
        tg.value = "not-a-token-yet"  # not focused: no paste detection
        tg.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert calls == ["not-a-token-yet"]

    run(body, "alerts")


def test_connect_telegram_reads_the_fields_on_the_ui_thread_not_in_the_worker(monkeypatch):
    seen, saved = [], []
    orig = SettingsPane._alert_values
    monkeypatch.setattr(SettingsPane, "_alert_values",
                        lambda self: (seen.append(threading.current_thread()), orig(self))[1])
    monkeypatch.setattr(alerts, "telegram_bot_info", lambda tok: ("bot", ""))
    monkeypatch.setattr(alerts, "telegram_find_chat", lambda tok: ("42", "Me"))
    monkeypatch.setattr(alerts, "save", lambda values: saved.append(values))
    monkeypatch.setattr(alerts, "_send_telegram", lambda *a: True)

    async def body(app, pilot, pane):
        pane.query_one("#al-ntfy", Input).value = "my-topic"
        pane.connect_telegram(TOKEN)
        await settle(app, pilot)
        assert seen and all(t is threading.main_thread() for t in seen)
        assert saved[0]["ntfy_topic"] == "my-topic" and saved[0]["telegram_chat_id"] == "42"
        assert saved[0]["telegram_bot_token"] == TOKEN

    run(body, "alerts")


# ---- number fields, template, compact -----------------------------------------------------------
def test_logon_numbers_are_written_back_and_non_numbers_rejected():
    async def body(app, pilot, pane):
        per = pane.query_one("#lg-per-lane", Input)
        per.value = "5.7"
        pane.query_one("#lg-gap", Input).value = "2.5"
        pane.save_logon()
        settings.load.cache_clear()
        r = settings.load().restore
        assert r["per_lane"] == 5 and r["gap_seconds"] == 2.5
        assert per.value == "5" and pane.query_one("#lg-gap", Input).value == "2.5"  # shows what was used
        per.value = "abc"
        pane.query_one("#lg-window", Input).value = ""
        pane.save_logon()
        settings.load.cache_clear()
        assert settings.load().restore["per_lane"] == 5 and per.value == "5"  # the saved value is back
        assert pane.query_one("#lg-window", Input).value == "3"
        assert any("'abc' is not a number" in t for t in app.toasts)

    run(body, "logon")


def test_max_age_days_is_clamped_to_at_least_one_and_non_numbers_rejected():
    async def body(app, pilot, pane):
        age = pane.query_one("#gn-age", Input)
        age.value = "0"
        pane.save_general()
        settings.load.cache_clear()
        assert settings.load().max_age_days == 1 and age.value == "1"
        age.value = "-7"
        pane.save_general()
        settings.load.cache_clear()
        assert settings.load().max_age_days == 1 and age.value == "1"
        age.value = "30"
        pane.save_general()
        settings.load.cache_clear()
        assert settings.load().max_age_days == 30 and age.value == "30"
        age.value = "many"
        pane.save_general()
        settings.load.cache_clear()
        assert settings.load().max_age_days == 30 and age.value == "30"
        assert any("'many' is not a number" in t for t in app.toasts)

    run(body, "general")


def test_alert_minutes_non_number_keeps_the_saved_value():
    async def body(app, pilot, pane):
        mins = pane.query_one("#al-min", Input)
        mins.value = "7"
        pane.save_alerts()
        settings.load.cache_clear()
        assert settings.load().alerts["blocked_minutes"] == 7
        mins.value = "soon"
        pane.save_alerts()
        settings.load.cache_clear()
        assert settings.load().alerts["blocked_minutes"] == 7 and mins.value == "7"

    run(body, "alerts")


def test_clearing_the_handoff_template_resets_it_and_shows_the_default():
    from navigator.settings_ui import _default_handoff
    settings.save_values("handoff", {"template": "custom {answer}"})
    settings.load.cache_clear()

    async def body(app, pilot, pane):
        field = pane.query_one("#pr-handoff", Input)
        assert field.value == "custom {answer}"
        field.value = "   "
        pane.save_handoff()
        settings.load.cache_clear()
        default = _default_handoff()
        assert "\n" in default and settings.load().handoff == default
        assert field.value == default.replace("\n", "\\n") and "\n" not in field.value
        assert "Reset to the default" in app.toasts
        field.value = "one\\ntwo"  # the escape the field expects
        pane.save_handoff()
        settings.load.cache_clear()
        assert settings.load().handoff == "one\ntwo"

    run(body, "prompts")


def test_compact_text_is_written_back_normalised():
    async def body(app, pilot, pane):
        field = pane.query_one("#pr-compact", Input)
        field.value = "  keep   the   plan  "
        pane.save_compact()
        settings.load.cache_clear()
        assert settings.load().compact["instructions"] == "keep the plan"
        assert field.value == "keep the plan"

    run(body, "prompts")


# ---- smaller fixes ------------------------------------------------------------------------------
def test_enter_in_the_new_prompt_name_moves_to_the_text_field():
    async def body(app, pilot, pane):
        pane.query_one("#pr-name", Input).focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused is pane.query_one("#pr-text", Input)

    run(body, "prompts")


def test_update_now_rechecks_on_the_ui_thread_and_both_version_lines_match(monkeypatch):
    where = []
    monkeypatch.setattr(updater, "update", lambda: "updated")
    orig = SettingsPane.check_updates
    monkeypatch.setattr(SettingsPane, "check_updates",
                        lambda self: (where.append(threading.current_thread()), orig(self))[1])

    async def body(app, pilot, pane):
        await settle(app, pilot)
        before = text_of(pane.query_one("#up-version"))
        where.clear()
        pane.run_update()
        await settle(app, pilot)
        assert where and all(t is threading.main_thread() for t in where)
        assert text_of(pane.query_one("#up-version")) == before and "from" in before

    run(body, "updates")
