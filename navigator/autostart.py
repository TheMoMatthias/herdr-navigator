"""The logon entry that runs the startup restore (`restore boot`), per OS. No admin rights needed.

Windows: a per-user scheduled task at logon that runs the plugin's pythonw (no console). A
Startup-folder shortcut was tried first: antivirus (Bitdefender) silently removed it and then
blocked the path, since a script shortcut in Startup is a classic malware pattern.
macOS: a LaunchAgent with RunAtLoad. Linux: an XDG autostart .desktop file.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "herdr-navigator-restore"
TASK = "herdr Navigator restore"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _python(windowless: bool = True) -> Path:
    if os.name == "nt":
        exe = "pythonw.exe" if windowless else "python.exe"
        return ROOT / ".venv" / "Scripts" / exe
    return ROOT / ".venv" / "bin" / "python"


def entry_path() -> Path:
    if os.name == "nt":
        return Path("Task Scheduler") / TASK
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"dev.herdr.{NAME}.plist"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / f"{NAME}.desktop"


def _ps(script: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                          env={**os.environ, **env}, capture_output=True, text=True, creationflags=_NO_WINDOW)


def installed() -> bool:
    if os.name == "nt":
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True, creationflags=_NO_WINDOW)
        return r.returncode == 0
    return entry_path().exists()


def _env() -> dict[str, str]:
    """The plugin dirs herdr assigned, so the restore finds its settings without herdr's env."""
    from . import settings
    return {"HERDR_PLUGIN_CONFIG_DIR": str(settings.config_dir()),
            "HERDR_PLUGIN_STATE_DIR": str(settings.state_dir()),
            "HERDR_BIN_PATH": os.environ.get("HERDR_BIN_PATH", "")}


def install() -> str:
    p = entry_path()
    if os.name != "nt":
        p.parent.mkdir(parents=True, exist_ok=True)
    py = _python()
    if not py.exists():
        return f"✗ {py} is missing: rebuild the plugin first"
    if os.name == "nt":
        ps = (
            "$a=New-ScheduledTaskAction -Execute $env:NAV_PY -Argument '-m navigator.restore boot' "
            "-WorkingDirectory $env:NAV_ROOT;"
            '$u="$env:USERDOMAIN\\$env:USERNAME";'
            "$t=New-ScheduledTaskTrigger -AtLogOn -User $u;"
            "$p=New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited;"
            "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
            "-ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew;"
            "Register-ScheduledTask -TaskName $env:NAV_TASK -Action $a -Trigger $t -Principal $p -Settings $s "
            "-Description 'herdr Navigator: reopen ticked sessions at logon' -Force -ErrorAction Stop | Out-Null"
        )
        r = _ps(ps, NAV_PY=str(py), NAV_ROOT=str(ROOT), NAV_TASK=TASK)
        if r.returncode != 0 or not installed():
            return f"✗ could not register the logon task: {(r.stderr or r.stdout).strip()[:200]}"
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
    if os.name == "nt":
        if not installed():
            return "startup restore was already off"
        _ps("Unregister-ScheduledTask -TaskName $env:NAV_TASK -Confirm:$false", NAV_TASK=TASK)
        return "⏻ startup restore off" if not installed() else "✗ could not remove the logon task"
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
