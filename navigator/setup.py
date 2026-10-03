"""Wire the Navigator into herdr's config.toml, or take it out again.

    python -m navigator.setup install     # idempotent; backs up config.toml once
    python -m navigator.setup uninstall   # removes exactly what install added
    python -m navigator.setup print       # show the block without writing

Your own settings always win: a key you already set is left alone (a direct chord is only
*added* next to your binding), and everything added is recorded so uninstall can remove it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import tomlkit
from tomlkit.items import AoT, Array, InlineTable, Table

from . import settings

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = os.name == "nt"
MARK = "navigator"   # every [[keys.command]] we own has a description starting "Navigator:"

# Direct chords. Ctrl+Alt is AltGr on many European layouts, so chords that type characters
# there (Q E M 2 3 7 8 9 0 + < sz) are avoided.
CHORDS = {
    "focus_pane_left": ["prefix+h", "ctrl+alt+h"],
    "focus_pane_down": ["prefix+j", "ctrl+alt+j"],
    "focus_pane_up": ["prefix+k", "ctrl+alt+k"],
    "focus_pane_right": ["prefix+l", "ctrl+alt+l"],
    "next_tab": ["prefix+n", "ctrl+alt+n"],
    "previous_tab": ["prefix+p", "ctrl+alt+p"],
    "new_tab": ["prefix+c", "ctrl+alt+c"],
    "split_vertical": ["prefix+v", "ctrl+alt+v"],
    "split_horizontal": ["prefix+minus", "ctrl+alt+s"],
    "zoom": ["prefix+z", "ctrl+alt+z"],
    "toggle_sidebar": ["prefix+b", "ctrl+alt+b"],
    "goto": ["prefix+g", "ctrl+alt+g"],
    "workspace_picker": ["prefix+w", "ctrl+alt+w"],
    "open_notification_target": ["prefix+o", "ctrl+alt+o"],
    "previous_workspace": ["prefix+shift+u", "ctrl+alt+u"],
    "next_workspace": ["prefix+shift+j", "ctrl+alt+d"],
    "switch_workspace": ["alt+1..9"],
    "next_agent": ["prefix+a", "ctrl+alt+a"],
    "last_pane": ["prefix+semicolon", "ctrl+alt+r"],
    "previous_agent": ["prefix+shift+a", "ctrl+alt+shift+a"],
}

POPUPS = [
    (["prefix+space", "f1"], "projects", "Navigator: projects, worktrees, agents"),
    (["prefix+u", "f2"], "resume", "Navigator: sessions (search and resume, logon ticks, accounts)"),
    (["prefix+i", "f3"], "agents", "Navigator: every agent and sub-agent"),
    (["prefix+slash", "f4"], "keys", "Navigator: key cheat sheet"),
    (["prefix+m", "f6"], "panes", "Navigator: layout (arrange, split, move panes)"),
    (["prefix+shift+o", "f7"], "recent", "Navigator: agents, where you were last first"),
    (["prefix+comma", "f9"], "settings", "Navigator: settings (logon, accounts, alerts, updates)"),
]

# Small popups: (keys, launcher module, width, height, description)
MENUS = [
    (["prefix+period", "f5"], "panemenu", 48, 23, "Navigator: pane menu (compact, split, move, arrange)"),
]

# Detached commands (no window): (keys, launcher args, description)
SHELLS = [
    (["ctrl+alt+i", "f8"], ("attention", "next"), "Navigator: next agent that needs you"),
    (["prefix+shift+right", "ctrl+alt+shift+right"], ("uiwidth", "+6"), "Navigator: sidebar wider"),
    (["prefix+shift+left", "ctrl+alt+shift+left"], ("uiwidth", "-6"), "Navigator: sidebar narrower"),
]

# Plugin actions (they get the focused pane as context): (keys, action id, description)
ACTIONS = [
    (["prefix+f", "f10"], "fold", "Navigator: fold / unfold this project in the sidebar"),
    (["prefix+shift+f", "ctrl+alt+shift+f"], "fold-all", "Navigator: fold / unfold every project in the sidebar"),
    (["prefix+shift+c", "ctrl+alt+shift+c"], "compact", "Navigator: /compact the focused agent with your instructions"),
]

UI = {
    "agent_panel_sort": "priority",
    "status_indicators": "symbols",
    "show_agent_labels_on_pane_borders": True,
    "sidebar_max_width": 120,
    "sidebar_min_width": 16,
    "window_title": "herdr - {workspace} - {tab}",
    "tab_bar_right_separator": "  ",
}

# One tone for the whole tree: every branch (├─ └─ │), name and worktree is the same muted
# colour and weight, so the connectors line up evenly. State shows only in the glyph after the
# branch (! ? ● ◐ ○ from sync.SIDE_ICON) and in herdr's own state text; the Space heading is bold.
MUTED = "#a89984"
TREE = {"fg": MUTED}

# one row per session inside the Space ($s1..$s8 from sync.py); empty rows disappear
SPACE_ROWS = [
    ["state_icon", "workspace", {"token": "$agents", "dim": True}],
    ["branch", "git_status"],
    *[[{"token": f"$s{i}", **TREE}] for i in range(1, 9)],
]
# herdr's Agents panel as a tree by project (tokens and order from sync.agent_tree): a heading
# row on the first agent of each project, then one "├─ ◐ NAME · state" line per session. No
# worktree row: its name (lead-3, data-2) read as a second copy of the session name; the
# worktree shows in the Spaces panel and in the Navigator.
# The state word carries the colour, one clearly different hue per state: working bright and
# bold, idle a calm grey that recedes, done green, anything waiting on you orange.
STATE_TEXT = {"token": "state_text", "fg": MUTED, "rules": [
    {"contains": "reply", "ignore_case": True, "fg": "#fe8019", "bold": True},
    {"contains": "blocked", "ignore_case": True, "fg": "#fe8019", "bold": True},
    {"contains": "working", "ignore_case": True, "fg": "#fabd2f", "bold": True},
    {"contains": "done", "ignore_case": True, "fg": "#b8bb26"},
    {"contains": "idle", "ignore_case": True, "fg": "#7c6f64"},
]}
AGENT_ROWS = [
    [{"token": "$grp", "bold": True}],
    [{"token": "$line", **TREE}, STATE_TEXT, {"token": "$subagents", "dim": True}],
]


def herdr_config_path() -> Path:
    if WINDOWS:
        return Path(os.environ.get("APPDATA", Path.home())) / "herdr" / "config.toml"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "herdr" / "config.toml"


def _short_path(p: str) -> str:
    """cmd.exe mangles quoted paths inside /c strings; an 8.3 path needs no quotes."""
    if not WINDOWS or " " not in p:
        return p
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(p, buf, 1024)
    return buf.value if 0 < n < 1024 else p


def launcher(*args: str) -> str:
    if WINDOWS:
        return " ".join([_short_path(str(ROOT / "run.cmd")), *args])
    return " ".join(["sh", f"'{ROOT / 'run.sh'}'", *args])  # sh: works without the exec bit


def status_command() -> str:
    """The tab bar prints the daemon's status file; without a daemon the script falls back."""
    out = str(settings.state_dir() / "status.txt")
    if WINDOWS:
        return f"{_short_path(str(ROOT / 'status.cmd'))} {_short_path(str(settings.state_dir()))}\\status.txt"
    return f"sh '{ROOT / 'status.sh'}' '{out}'"


def _added_file() -> Path:
    return settings.state_dir() / "setup-added.json"


def _inline(d: dict) -> InlineTable:
    t = tomlkit.inline_table()
    t.update(d)
    return t


def _rows(rows) -> Array:
    arr = tomlkit.array()
    for r in rows:
        inner = tomlkit.array()
        for x in r:
            inner.append(_inline(x) if isinstance(x, dict) else x)
        arr.append(inner)
    return arr.multiline(True)


def _is_ours_status(entry) -> bool:
    """Our tab-bar entry: this checkout's launcher, or any older navigator/cockpit launcher."""
    cmd = str(entry.get("command", "")) if hasattr(entry, "get") else ""
    if cmd in (launcher("status"), status_command()):
        return True
    low = cmd.lower()
    if ("status.cmd" in low or "status.sh" in low) and "status.txt" in low:
        return True
    return ("run.cmd" in low or "run.sh" in low) and low.endswith(" status") and (
        "navigator" in low or "cockpit" in low)


def _is_ours_command(c) -> bool:
    d = str(c.get("description", ""))
    return d.startswith("Navigator:") or d.startswith("Cockpit:")


def install(doc, added: dict) -> list[str]:
    notes = []
    ui = doc.setdefault("ui", tomlkit.table())
    for k, v in UI.items():
        if k not in ui:
            ui[k] = v
            added.setdefault("ui", []).append(k)
    # herdr clamps a mouse-dragged sidebar to sidebar_max_width: leave room to drag it wide
    if int(ui.get("sidebar_max_width", 36)) < UI["sidebar_max_width"]:
        ui["sidebar_max_width"] = UI["sidebar_max_width"]
    # tab bar: replace our old entry, keep everything else
    bar = ui.get("tab_bar_right")
    if bar is None:
        bar = tomlkit.array()
        bar.append(_inline({"type": "zoom"}))
        ui["tab_bar_right"] = bar
    keep = [e for e in bar if not _is_ours_status(e)]
    bar.clear()
    for e in keep:
        bar.append(e)
    bar.append(_inline({"type": "command", "command": status_command(),
                        "interval_seconds": 3, "timeout_seconds": 4}))
    bar.multiline(True)
    added["status"] = True

    sidebar = ui.setdefault("sidebar", tomlkit.table(is_super_table=True))
    for name, rows in (("spaces", SPACE_ROWS), ("agents", AGENT_ROWS)):
        sec = sidebar.get(name)
        rows_text = str(sec.get("rows", "")) if sec is not None else ""
        ours = any(t in rows_text for t in ("$project", "$agents", "$line", "$s1"))
        if sec is None or ours:
            if sec is None:
                sec = tomlkit.table()
                sidebar[name] = sec
            sec["rows"] = _rows(rows)
            added.setdefault("sidebar", []).append(name)
        else:
            notes.append(f"kept your own [ui.sidebar.{name}] rows (add $agents/$s1..$s8/$project tokens yourself)")

    # the plugin resumes agent panes itself after a restart, named and with launch options
    sess = doc.setdefault("session", tomlkit.table())
    if "resume_agents_on_restore" not in sess:
        sess["resume_agents_on_restore"] = False
        added["session_resume"] = True

    keys = doc.setdefault("keys", tomlkit.table())
    for action, chords in CHORDS.items():
        cur = keys.get(action)
        if cur is None:
            keys[action] = chords if len(chords) > 1 else chords[0]
            added.setdefault("keys", {})[action] = "set"
        else:
            have = [cur] if isinstance(cur, str) else list(cur)
            direct = [c for c in chords if not c.startswith("prefix+") and c not in have]
            if direct:
                keys[action] = have + direct
                added.setdefault("keys", {})[action] = direct
    cmds = keys.get("command")
    if cmds is None:
        cmds = tomlkit.aot()
        keys["command"] = cmds
    for c in [c for c in cmds if _is_ours_command(c)]:
        cmds.remove(c)
    for key, tab, desc in POPUPS:
        t = tomlkit.table()
        t.update({"key": key, "type": "popup", "command": launcher("app", tab),
                  "width": "94%", "height": "90%", "description": desc})
        cmds.append(t)
    for key, mod, width, height, desc in MENUS:
        t = tomlkit.table()
        t.update({"key": key, "type": "popup", "command": launcher(mod), "width": width, "height": height,
                  "description": desc})
        cmds.append(t)
    for key, args, desc in SHELLS:
        t = tomlkit.table()
        t.update({"key": key, "type": "shell", "command": launcher(*args), "description": desc})
        cmds.append(t)
    for key, action, desc in ACTIONS:
        t = tomlkit.table()
        t.update({"key": key, "type": "plugin_action", "description": desc,
                  "command": f"{settings.PLUGIN_ID}.{action}{'' if WINDOWS else '-unix'}"})
        cmds.append(t)
    added["commands"] = True
    return notes


def uninstall(doc, added: dict) -> None:
    if added.get("session_resume") and "session" in doc:
        doc["session"].pop("resume_agents_on_restore", None)
        if not doc["session"]:
            doc.pop("session")
    ui = doc.get("ui", {})
    for k in added.get("ui", []):
        ui.pop(k, None)
    bar = ui.get("tab_bar_right")
    if bar is not None:
        keep = [e for e in bar if not _is_ours_status(e)]
        if not keep or all(dict(e) == {"type": "zoom"} for e in keep) and added.get("status"):
            ui.pop("tab_bar_right", None)
        else:
            bar.clear()
            for e in keep:
                bar.append(e)
    sb = ui.get("sidebar", {})
    for name in added.get("sidebar", []):
        sb.pop(name, None)
    if "sidebar" in ui and not ui["sidebar"]:
        ui.pop("sidebar")
    keys = doc.get("keys", {})
    for action, what in added.get("keys", {}).items():
        if what == "set":
            keys.pop(action, None)
        elif action in keys:
            rest = [c for c in (keys[action] if not isinstance(keys[action], str) else [keys[action]])
                    if c not in what]
            keys[action] = rest[0] if len(rest) == 1 else rest
    cmds = keys.get("command")
    if cmds is not None:
        for c in [c for c in cmds if _is_ours_command(c)]:
            cmds.remove(c)
        if not len(cmds):
            keys.pop("command")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    mode = argv[0] if argv else "install"
    path = herdr_config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text)
    try:
        added = json.loads(_added_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        added = {}
    if mode in ("install", "print"):
        notes = install(doc, added)
    elif mode == "uninstall":
        uninstall(doc, added)
        added, notes = {}, []
    else:
        print(__doc__)
        return 2
    out = tomlkit.dumps(doc)
    if mode == "print":
        print(out)
        return 0
    backup = path.with_name("config.toml.before-navigator.bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out, encoding="utf-8")
    _added_file().write_text(json.dumps(added), encoding="utf-8")
    herdr = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"
    check = subprocess.run([herdr, "config", "check"], capture_output=True, text=True, encoding="utf-8")
    ok = "ok" in (check.stdout + check.stderr).lower() and "issue" not in (check.stdout + check.stderr).lower()
    if not ok:
        path.write_text(text, encoding="utf-8")
        print("herdr rejected the new config, restored the previous file:\n" + check.stdout + check.stderr)
        return 1
    subprocess.run([herdr, "server", "reload-config"], capture_output=True, text=True)
    print(f"{mode}ed: {path}" + (f"\nbackup: {backup}" if backup.exists() else ""))
    for n in notes:
        print("note:", n)
    if mode == "install":
        print("Press F1 in herdr to open the Navigator.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
