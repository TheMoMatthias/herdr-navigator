"""Make herdr's left sidebar wider or narrower, live.

herdr sizes the sidebar from `[ui] sidebar_width` (clamped to sidebar_min/max_width) and applies
`herdr server reload-config` immediately. This edits those keys with tomlkit, so comments and
the rest of config.toml stay intact.

    run sidebar +6 | -6 | 40 | reset
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import tomlkit

from .setup import herdr_config_path

DEFAULT, LOW, HIGH = 26, 16, 120


def current() -> int:
    try:
        doc = tomlkit.parse(herdr_config_path().read_text(encoding="utf-8"))
        return int(doc.get("ui", {}).get("sidebar_width", DEFAULT))
    except (OSError, ValueError, TypeError):
        return DEFAULT


def set_width(arg: str) -> int:
    path = herdr_config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text)
    ui = doc.setdefault("ui", tomlkit.table())
    now = int(ui.get("sidebar_width", DEFAULT))
    if arg == "reset":
        width = DEFAULT
    elif arg[:1] in "+-":
        width = now + int(arg)
    else:
        width = int(arg)
    width = max(LOW, min(HIGH, width))
    ui["sidebar_width"] = width
    if int(ui.get("sidebar_max_width", 36)) < width:
        ui["sidebar_max_width"] = width
    if int(ui.get("sidebar_min_width", 18)) > width:
        ui["sidebar_min_width"] = width
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    herdr = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    res = subprocess.run([herdr, "server", "reload-config"], capture_output=True, text=True, creationflags=flags)
    if '"applied"' not in res.stdout:
        path.write_text(text, encoding="utf-8")  # herdr refused it: put the old file back
        raise RuntimeError((res.stdout + res.stderr)[:300])
    subprocess.run([herdr, "notification", "show", f"Sidebar width {width}", "--body",
                    "Ctrl+Alt+Shift+→ wider · Ctrl+Alt+Shift+← narrower"],
                   capture_output=True, creationflags=flags)
    return width


if __name__ == "__main__":
    print(set_width(sys.argv[1] if len(sys.argv) > 1 else "+6"))
