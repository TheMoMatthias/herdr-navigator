"""The logon entry that runs the startup restore (`restore boot`), per OS. No admin rights needed.

Windows: a shortcut in the user's Startup folder that runs the plugin's pythonw (no console).
macOS: a LaunchAgent with RunAtLoad. Linux: an XDG autostart .desktop file.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "herdr-navigator-restore"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _python(windowless: bool = True) -> Path:
    if os.name == "nt":
        exe = "pythonw.exe" if windowless else "python.exe"
        return ROOT / ".venv" / "Scripts" / exe
    return ROOT / ".venv" / "bin" / "python"


def entry_path() -> Path:
    if os.name == "nt":
        start = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        return start / "herdr Navigator restore.lnk"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"dev.herdr.{NAME}.plist"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / f"{NAME}.desktop"


def installed() -> bool:
    return entry_path().exists()


def _env() -> dict[str, str]:
    """The plugin dirs herdr assigned, so the restore finds its settings without herdr's env."""
    from . import settings
    return {"HERDR_PLUGIN_CONFIG_DIR": str(settings.config_dir()),
            "HERDR_PLUGIN_STATE_DIR": str(settings.state_dir()),
            "HERDR_BIN_PATH": os.environ.get("HERDR_BIN_PATH", "")}


def install() -> str:
    p = entry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    py = _python()
    if not py.exists():
        return f"✗ {py} is missing: rebuild the plugin first"
    if os.name == "nt":
        ps = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:NAV_LNK);"
            "$s.TargetPath=$env:NAV_PY;$s.Arguments='-m navigator.restore boot';"
            "$s.WorkingDirectory=$env:NAV_ROOT;$s.WindowStyle=7;"
            "$s.Description='herdr Navigator: reopen ticked sessions at logon';$s.Save()"
        )
        env = {**os.environ, "NAV_LNK": str(p), "NAV_PY": str(py), "NAV_ROOT": str(ROOT)}
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                           env=env, capture_output=True, text=True, creationflags=_NO_WINDOW)
        if r.returncode != 0 or not p.exists():
            return f"✗ could not create the Startup shortcut: {(r.stderr or r.stdout).strip()[:200]}"
    elif sys.platform == "darwin":
        env_xml = "".join(f"<key>{k}</key><string>{v}</string>" for k, v in _env().items() if v)
        p.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>dev.herdr.{NAME}</string>
<key>ProgramArguments</key><array><string>{py}</string><string>-m</string><string>navigator.restore</string><string>boot</string></array>
<key>WorkingDirectory</key><string>{ROOT}</string>
<key>EnvironmentVariables</key><dict>{env_xml}</dict>
<key>RunAtLoad</key><true/>
</dict></plist>
""", encoding="utf-8")
    else:
        envs = " ".join(f"{k}='{v}'" for k, v in _env().items() if v)
        p.write_text(f"""[Desktop Entry]
Type=Application
Name=herdr Navigator restore
Comment=Reopen ticked agent sessions in herdr
Exec=sh -c "cd '{ROOT}' && env {envs} '{py}' -m navigator.restore boot"
X-GNOME-Autostart-enabled=true
NoDisplay=true
""", encoding="utf-8")
    return f"⏻ startup restore on ({p.name})"


def uninstall() -> str:
    p = entry_path()
    if p.exists():
        p.unlink()
        return "⏻ startup restore off"
    return "startup restore was already off"


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "status"
    if arg == "on":
        print(install())
    elif arg == "off":
        print(uninstall())
    else:
        print(("on: " if installed() else "off: ") + str(entry_path()))
