"""The terminal's font size, for Settings › General. A terminal app cannot size its own text: the
terminal draws it. On Windows Terminal the size lives in its settings.json (profiles.defaults
font.size), which it reloads on its own, so changing it there resizes every tab at once.
Elsewhere the terminal's own zoom keys (usually Ctrl + / Ctrl -) do the same."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import jsonfile

DEFAULT = 12  # Windows Terminal's own default
LOW, HIGH = 6, 36


def settings_file() -> Path | None:
    if os.name != "nt":
        return None
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for p in (local / "Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json",
              local / "Packages/Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe/LocalState/settings.json",
              local / "Microsoft/Windows Terminal/settings.json"):
        if p.is_file():
            return p
    return None


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def get() -> int | None:
    """The current size, or None when there is no Windows Terminal to change."""
    p = settings_file()
    if not p:
        return None
    try:
        d = _load(p)
    except (OSError, ValueError):
        return None
    prof = d.get("profiles") if isinstance(d.get("profiles"), dict) else {}
    font = (prof.get("defaults") or {}).get("font") or {}
    return int(font.get("size") or DEFAULT)


def set_size(size: int) -> str:
    p = settings_file()
    if not p:
        return "✗ no Windows Terminal settings found: use your terminal's zoom (Ctrl + / Ctrl −)"
    size = max(LOW, min(HIGH, int(size)))
    try:
        d = _load(p)
    except (OSError, ValueError) as e:
        return f"✗ Windows Terminal's settings.json could not be read ({e}); nothing changed"
    backup = p.with_name(p.name + ".navigator-backup")
    if not backup.exists():
        shutil.copy2(p, backup)  # the state before the Navigator first touched it
    prof = d.setdefault("profiles", {})
    if isinstance(prof, list):  # very old format: a bare list of profiles
        prof = d["profiles"] = {"list": prof}
    prof.setdefault("defaults", {}).setdefault("font", {})["size"] = size
    for pr in prof.get("list", []):  # a profile with its own size would ignore the default
        if isinstance(pr, dict) and isinstance(pr.get("font"), dict) and "size" in pr["font"]:
            pr["font"]["size"] = size
    jsonfile.write(p, d, indent=4)
    return f"Font size {size}: Windows Terminal applies it to every tab right away"
