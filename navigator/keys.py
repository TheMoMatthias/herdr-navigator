"""The effective herdr keymap: built-in defaults (from `herdr --default-config`) + config.toml."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import herdr

GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Find your way", [
        ("goto", "Goto picker: every agent/terminal"),
        ("workspace_picker", "Workspace (project) picker"),
        ("open_notification_target", "Jump to the agent that notified"),
        ("next_agent", "Next agent"), ("previous_agent", "Previous agent"),
        ("focus_agent", "Focus agent row N"),
        ("toggle_sidebar", "Show / hide sidebar"),
        ("help", "All key bindings"),
    ]),
    ("Projects (workspaces)", [
        ("next_workspace", "Next project"), ("previous_workspace", "Previous project"),
        ("switch_workspace", "Project N"),
        ("new_workspace", "New workspace"), ("rename_workspace", "Rename workspace"),
        ("close_workspace", "Close workspace"),
        ("new_worktree", "New git worktree"),
    ]),
    ("Tabs", [
        ("new_tab", "New tab"), ("next_tab", "Next tab"), ("previous_tab", "Previous tab"),
        ("switch_tab", "Tab N"), ("rename_tab", "Rename tab"), ("close_tab", "Close tab"),
    ]),
    ("Panes", [
        ("focus_pane_left", "Focus left"), ("focus_pane_down", "Focus down"),
        ("focus_pane_up", "Focus up"), ("focus_pane_right", "Focus right"),
        ("last_pane", "Last pane"), ("cycle_pane_next", "Cycle panes"),
        ("split_vertical", "Split right"), ("split_horizontal", "Split down"),
        ("zoom", "Zoom pane"), ("resize_mode", "Resize mode"),
        ("rename_pane", "Rename pane"), ("close_pane", "Close pane"),
        ("edit_scrollback", "Scrollback in editor"),
    ]),
    ("Session", [
        ("settings", "Settings"), ("reload_config", "Reload config"), ("detach", "Detach (keeps running)"),
    ]),
]

_LINE = re.compile(r'^#?\s*([a-z_]+)\s*=\s*("([^"]*)"|\[[^\]]*\])')


@dataclass
class Binding:
    action: str
    label: str
    keys: list[str]
    custom: bool = False


def config_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "herdr" / "config.toml"


def _defaults() -> dict[str, list[str]]:
    """herdr's built-in keys. Asking herdr costs a process (0.2 s idle, seconds when the machine
    is busy) on every Navigator open, so the answer is kept until the herdr binary changes."""
    from . import jsonfile, settings
    exe = shutil.which(herdr.herdr_bin()) or herdr.herdr_bin()
    try:
        st = os.stat(exe)
        stamp = f"{exe}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        stamp = ""
    cache = settings.state_dir() / "herdr-default-keys.json"
    try:
        hit = jsonfile.read(cache, {}) or {}
    except (OSError, ValueError):
        hit = {}
    if stamp and hit.get("stamp") == stamp and hit.get("keys"):
        return hit["keys"]
    out = _ask_defaults()
    if stamp and out:
        try:
            jsonfile.write(cache, {"stamp": stamp, "keys": out})
        except OSError:
            pass
    return out


def _ask_defaults() -> dict[str, list[str]]:
    try:
        text = subprocess.run([herdr.herdr_bin(), "--default-config"], capture_output=True, text=True,
                              encoding="utf-8", timeout=5,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    out: dict[str, list[str]] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^#?\s*\[([a-z_.]+)\]", line)
        if m:
            section = m.group(1)
            continue
        if section != "keys":
            continue
        m = _LINE.match(line)
        if m:
            val = m.group(3) if m.group(3) is not None else m.group(2)
            out[m.group(1)] = _as_list(val)
    return out


def _as_list(v) -> list[str]:
    if isinstance(v, list):
        return [x for x in v if x]
    if isinstance(v, str) and v.startswith("["):
        return re.findall(r'"([^"]+)"', v)
    return [v] if v else []


def user_config() -> dict:
    try:
        return tomllib.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def effective() -> tuple[str, list[tuple[str, list[Binding]]], list[Binding]]:
    """Returns (prefix, grouped built-in bindings, custom command bindings)."""
    keys = {**_defaults(), **{k: _as_list(v) for k, v in user_config().get("keys", {}).items()
                              if isinstance(v, (str, list))}}
    prefix = (keys.get("prefix") or ["ctrl+b"])[0]
    groups = []
    for title, items in GROUPS:
        rows = [Binding(a, label, keys.get(a, [])) for a, label in items]
        groups.append((title, rows))
    custom = []
    for c in user_config().get("keys", {}).get("command", []) or []:
        custom.append(Binding(c.get("command", ""), c.get("description") or c.get("command", ""),
                              _as_list(c.get("key", "")), True))
    return prefix, groups, custom


_NAMES = {"minus": "-", "plus": "+", "comma": ",", "period": ".", "space": "Space", "tab": "Tab",
          "enter": "Enter", "esc": "Esc", "left": "←", "right": "→", "up": "↑", "down": "↓",
          "slash": "/", "backtick": "`", "ampersand": "&"}


def pretty(key: str, prefix: str) -> str:
    """'prefix+shift+n' -> 'Ctrl+B › Shift+N'; 'ctrl+alt+h' -> 'Ctrl+Alt+H'."""
    def chord(k: str) -> str:
        parts = k.split("+")
        out = []
        for p in parts:
            if p in ("ctrl", "alt", "shift", "cmd", "super"):
                out.append(p.capitalize())
            elif ".." in p:
                out.append(p)
            else:
                out.append(_NAMES.get(p, p.upper() if len(p) == 1 else p.capitalize()))
        return "+".join(out)
    if key.startswith("prefix+"):
        return f"{chord(prefix)} › {chord(key[len('prefix+'):])}"
    return chord(key)
