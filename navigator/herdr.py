"""Thin wrapper over the herdr CLI. The CLI is the plugin API (herdr docs: Plugins)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class HerdrError(RuntimeError):
    pass


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"


def run(*args: str, timeout: float = 10.0, check: bool = True) -> dict:
    """Run `herdr <args>` and return the parsed JSON `result` object."""
    proc = subprocess.run(
        [herdr_bin(), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        if check:
            raise HerdrError((proc.stderr or proc.stdout).strip()[:500])
        return {}
    out = proc.stdout.strip()
    if not out:
        return {}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"raw": out}
    return data.get("result", data)


def snapshot() -> dict:
    return run("api", "snapshot").get("snapshot", {})


# --- navigation ---------------------------------------------------------------

def focus_workspace(ws_id: str) -> None:
    run("workspace", "focus", ws_id)


def focus_tab(tab_id: str) -> None:
    run("tab", "focus", tab_id)


def focus_agent(pane_id: str) -> None:
    run("agent", "focus", pane_id)


def create_workspace(cwd: str, label: str | None = None, focus: bool = True) -> dict:
    args = ["workspace", "create", "--cwd", cwd, "--focus" if focus else "--no-focus"]
    if label:
        args += ["--label", label]
    return run(*args)


def create_tab(ws_id: str, cwd: str, label: str | None = None, focus: bool = True) -> dict:
    args = ["tab", "create", "--workspace", ws_id, "--cwd", cwd, "--focus" if focus else "--no-focus"]
    if label:
        args += ["--label", label]
    return run(*args)


def pane_run(pane_id: str, command: str) -> None:
    run("pane", "run", pane_id, command)


def rename_workspace(ws_id: str, label: str) -> None:
    run("workspace", "rename", ws_id, label)


def workspace_tokens(ws_id: str, source: str, tokens: dict[str, str | None]) -> None:
    args = ["workspace", "report-metadata", ws_id, "--source", source]
    for k, v in tokens.items():
        args += ["--clear-token", k] if v is None else ["--token", f"{k}={v}"]
    run(*args, check=False)


def pane_tokens(pane_id: str, source: str, tokens: dict[str, str | None]) -> None:
    args = ["pane", "report-metadata", pane_id, "--source", source]
    for k, v in tokens.items():
        args += ["--clear-token", k] if v is None else ["--token", f"{k}={v}"]
    run(*args, check=False)
