"""User settings: <HERDR_PLUGIN_CONFIG_DIR>/navigator.toml (a commented default is written on first run)."""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DEFAULT_TOML = """\
# herdr-navigator settings. Edit freely; changes apply the next time the Navigator opens.

# Pin projects (they appear even with no recent sessions) and optionally rename them.
# Keys are directories, values are display names ("" keeps the folder name).
[projects]
# '~/code/my-repo' = "My Repo"

[sessions]
# How far back the Resume list and project discovery look.
max_age_days = 45
# Skip Codex sub-agent threads and headless Claude runs in the Resume list (they still show
# as sub-agents under their parent in the Agents tab).
hide_subagents = true

[hide]
# Regexes (case-insensitive) on a session/pane cwd, matched with "/" as the separator on
# every OS. Matching directories never become projects.
patterns = [
  '/appdata/local/temp/',
  '/appdata/roaming/claude/scratch',
  '^/tmp/',
  '^/private/var/folders/',
  '/scratchpad',
]

[workspaces]
# Rename auto-named herdr workspaces to their project name (never touches names you set).
auto_name = true

[sidebar]
# Projects shown in herdr's sidebar are the ones you tick in the Navigator (Space / "Sidebar"
# button). Active worktrees of a ticked project open as indented children of its workspace.
open_active_worktrees = true
# Sessions running in other terminal windows get a "mirror" tab in their project's workspace:
# it shows what they do and lists them in herdr's Agents panel under their real name.
mirror_outside = true
# Keep herdr's Spaces sorted like the Agents panel: who needs you first, then what runs, then
# the most recently active. A repo moves together with its worktree Spaces. Off = your own order.
sort_spaces = true
# An idle session with nothing new in its transcript for this long shows as ◌ inactive (sorted
# below idle), so a pause of a few minutes and a session nobody touched for hours look different.
# 0 = off.
inactive_after_minutes = 60

[launch]
# Commands used by "new agent" and "resume". {id} is the session id.
claude_new = "claude"
claude_resume = "claude --resume {id}"
codex_new = "codex"
codex_resume = "codex resume {id}"
pi_new = "pi"
pi_resume = "pi --session {id}"
opencode_new = "opencode"
opencode_resume = "opencode --session {id}"
kilo_new = "kilo"
kilo_resume = "kilo --session {id}"
gemini_new = "gemini"
gemini_resume = "gemini --resume {id}"
qwen_new = "qwen"
qwen_resume = "qwen --resume {id}"
copilot_new = "copilot"
copilot_resume = "copilot --resume {id}"
hermes_new = "hermes"
hermes_resume = "hermes --resume {id}"
droid_new = "droid"
droid_resume = "droid --resume {id}"
amp_new = "amp"
amp_resume = "amp threads continue {id}"
cline_new = "cline"
cline_resume = "cline --id {id}"
cursor_new = "cursor-agent"
cursor_resume = "cursor-agent --resume {id}"

[restore]
# Startup restore: which sessions reopen when you log on (Navigator › Sessions, ⚙ Settings › Logon).
# A session you tick or untick yourself keeps that choice. Everything else is ticked
# automatically: the newest `per_lane` sessions of each project lane (the main checkout and
# each worktree) that you worked in during the last `window_days` days.
per_lane = 3
window_days = 3
# Projects worked in during this many days are switched on for restore by default.
recency_days = 14
include_worktrees = true
# Never open more than this many sessions in one go.
max_sessions = 30
# Seconds between two launches, and the wait after logon before restoring.
gap_seconds = 1.5
logon_delay_seconds = 20
# Claude only: name the restored session after its title (claude -n) and attach Remote Control.
claude_name = true
claude_remote_control = false
# Command that opens herdr in a terminal at logon ("" = Windows Terminal / the system terminal).
terminal = ""

[context]
# Context window (tokens) by model name, for the Ctx gauge. The longest key contained in the
# model id wins; a session seen using more than its window counts as a 1M-token window.
default = 200000
# The Ctx bar turns yellow from warn_at tokens in use and red from full_at (or at 70% / 85% of a
# smaller window); the herdr sidebar shows the same coloured bar.
warn_at = 200000
full_at = 700000
"claude-opus-5" = 1000000
"claude-sonnet-5" = 1000000

[prompts]
# One-click prompts (Agents › ☰ Prompts), sent to the selected agent or every ticked one.
"Status" = "Give me a short status: what is done, what is next, and is anything blocked?"
"Compact" = "/compact"
"Wrap up" = "Wrap up for today: summarise the state and write down how to resume tomorrow."
"Commit & push" = "Commit your finished work with a clear message and push it."
"Review" = "Review what you just did: anything wrong, risky or untested? Fix what you find."

[compact]
# What ⇣ Compact (and prefix+shift+c in herdr) adds after /compact for Claude Code: what the
# summary must keep. Other CLIs get a plain /compact. Empty = a plain /compact everywhere.
instructions = "Save the current working state: all progress, findings and insights gained, what we are working on and still need to evaluate, the tasks and objectives done and still to do next, and the rulings and workflows established."

[handoff]
# What "⇢ Hand off" sends to the other agent. {name} {cli} {project} {answer}
template = "Hand-off from {name} ({cli}, {project}). Their latest answer:\\n\\n{answer}\\n\\nReview it and tell me what you think: what is right, what is wrong or missing."

[alerts]
# Alerts to your phone. Easiest: Navigator › ⚙ Settings › Alerts sets these up for you.
# Telegram: create a bot with @BotFather, paste its token, press Start in the bot chat.
telegram_bot_token = ""
telegram_chat_id = ""
# ntfy (https://ntfy.sh, free app, no account): a topic name only you know.
ntfy_topic = ""
ntfy_server = "https://ntfy.sh"
# Discord, Slack, Mattermost, Teams, Google Chat ...: an incoming-webhook URL.
webhook_url = ""
# WhatsApp through CallMeBot (free, unofficial): see callmebot.com for your API key.
whatsapp_phone = ""
whatsapp_apikey = ""
# Alert when an agent has waited on you this many minutes (0 = never).
blocked_minutes = 10
# Alert with the logon restore's result.
on_restore = true

# Sign in / sign out per CLI (Navigator › ⚙ Settings › Accounts). `watch` is the file the CLI
# rewrites when the sign-in succeeds; the Navigator then offers to relaunch that CLI's sessions.
# `files` (and `json_keys`: keys inside JSON files) hold the login: account profiles save and
# swap exactly these, so you can switch accounts without signing out.
[login.claude]
login = "claude auth login"
logout = "claude auth logout"
status = "claude auth status"
watch = "~/.claude/.credentials.json"
files = ["~/.claude/.credentials.json"]
json_keys = { "~/.claude.json" = ["oauthAccount"] }

[login.codex]
login = "codex login"
logout = "codex logout"
status = "codex login status"
watch = "~/.codex/auth.json"
files = ["~/.codex/auth.json"]

[login.opencode]
login = "opencode auth login"
logout = "opencode auth logout"
status = "opencode auth list"
watch = "~/.local/share/opencode/auth.json"
files = ["~/.local/share/opencode/auth.json"]

[login.gemini]
login = "gemini"
logout = ""
status = ""
watch = "~/.gemini/oauth_creds.json"
files = ["~/.gemini/oauth_creds.json", "~/.gemini/google_accounts.json"]
"""


@dataclass
class Settings:
    projects: dict[str, str] = field(default_factory=dict)
    max_age_days: int = 45
    hide_subagents: bool = True
    hidden_patterns: list[str] = field(default_factory=list)
    auto_name: bool = True
    open_active_worktrees: bool = True
    mirror_outside: bool = True
    sort_spaces: bool = True
    inactive_after_minutes: int = 60
    launch: dict[str, str] = field(default_factory=dict)
    restore: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)
    alerts: dict = field(default_factory=dict)
    handoff: str = ""
    compact: dict = field(default_factory=dict)
    login: dict[str, dict] = field(default_factory=dict)


PLUGIN_ID = "momatthias.navigator"


def _dirs_file() -> Path:
    return Path(__file__).resolve().parent.parent / ".herdr-dirs.json"


def _remembered(kind: str) -> str | None:
    """Keybinding popups don't get HERDR_PLUGIN_* env, so every run under herdr's plugin env
    records the dirs herdr assigned, and later runs without that env reuse them."""
    env = os.environ.get(f"HERDR_PLUGIN_{kind}_DIR")
    if env and os.environ.get("HERDR_PLUGIN_ID") != PLUGIN_ID:
        return env  # an override (tests, dev), not herdr's assignment: use it, don't remember it
    f = _dirs_file()
    try:
        known = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    if env:
        if known.get(kind) != env:
            known[kind] = env
            try:
                f.write_text(json.dumps(known), encoding="utf-8")
            except OSError:
                pass
        return env
    return known.get(kind)


def config_dir() -> Path:
    d = _remembered("CONFIG")
    return Path(d) if d else Path.home() / ".config" / "herdr-navigator"


def state_dir() -> Path:
    d = _remembered("STATE")
    p = Path(d) if d else Path.home() / ".cache" / "herdr-navigator"
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_path() -> Path:
    return config_dir() / "navigator.toml"


@lru_cache(maxsize=1)
def load() -> Settings:
    path = settings_path()
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_TOML, encoding="utf-8")
        except OSError:
            pass
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raw = tomllib.loads(DEFAULT_TOML)
    defaults = tomllib.loads(DEFAULT_TOML)
    sess = {**defaults["sessions"], **raw.get("sessions", {})}
    launch = {**defaults["launch"], **raw.get("launch", {})}
    return Settings(
        projects=dict(raw.get("projects", {})),
        max_age_days=int(sess["max_age_days"]),
        hide_subagents=bool(sess["hide_subagents"]),
        hidden_patterns=list(raw.get("hide", defaults["hide"]).get("patterns", [])),
        auto_name=bool(raw.get("workspaces", {}).get("auto_name", True)),
        open_active_worktrees=bool(raw.get("sidebar", {}).get("open_active_worktrees", True)),
        mirror_outside=bool(raw.get("sidebar", {}).get("mirror_outside", True)),
        sort_spaces=bool(raw.get("sidebar", {}).get("sort_spaces", True)),
        inactive_after_minutes=int(raw.get("sidebar", {}).get("inactive_after_minutes", 60) or 0),
        launch=launch,
        restore={**defaults["restore"], **raw.get("restore", {})},
        context={**defaults["context"], **raw.get("context", {})},
        prompts=dict(raw.get("prompts", defaults["prompts"])),
        alerts={**defaults["alerts"], **raw.get("alerts", {})},
        handoff=str(raw.get("handoff", {}).get("template", defaults["handoff"]["template"])),
        compact={**defaults["compact"], **raw.get("compact", {})},
        login={k: {**defaults["login"].get(k, {}), **v} for k, v in
               {**defaults["login"], **raw.get("login", {})}.items()},
    )


def save_values(section: str, values: dict) -> None:
    """Write keys into `[section]` (dotted for nested tables) of navigator.toml, keeping
    everything else, comments included, as it is."""
    import tomlkit
    path = settings_path()
    load()  # makes sure the file exists
    doc = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()
    tab = doc
    for part in section.split("."):
        nxt = tab.get(part)
        if nxt is None:
            nxt = tomlkit.table()
            tab[part] = nxt
        tab = nxt
    for k, v in values.items():
        tab[k] = v
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    load.cache_clear()
