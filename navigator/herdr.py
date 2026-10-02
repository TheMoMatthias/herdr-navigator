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


def request(method: str, params: dict | None = None, timeout: float = 10.0) -> dict:
    """One raw socket-API request: in-process, so far cheaper than spawning the CLI.
    Windows: a named pipe named after HERDR_SOCKET_PATH. Unix: a Unix socket at that path.
    Never hangs: a server that does not answer within `timeout` raises HerdrError."""
    path = os.environ.get("HERDR_SOCKET_PATH") or _default_socket()
    payload = (json.dumps({"id": f"nav:{method}", "method": method, "params": params or {}}) + "\n").encode()
    buf = b""
    if os.name == "nt":
        import threading
        import time
        box: dict = {}

        def talk() -> None:
            try:
                for attempt in range(20):  # every pipe instance busy (ERROR_PIPE_BUSY): retry briefly
                    try:
                        f = open(r"\\.\pipe" + "\\" + path, "r+b", buffering=0)
                        break
                    except OSError as e:
                        if getattr(e, "winerror", 0) != 231 or attempt == 19:
                            raise
                        time.sleep(0.05)
                with f:
                    f.write(payload)
                    out = b""
                    while not out.endswith(b"\n"):
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        out += chunk
                box["out"] = out
            except Exception as e:  # raised in the caller below
                box["err"] = e
        th = threading.Thread(target=talk, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive():
            raise HerdrError(f"herdr did not answer {method} within {timeout:.0f}s")
        if "err" in box:
            raise HerdrError(f"{method}: {box['err']}")
        buf = box.get("out", b"")
    else:
        import socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path)
            s.sendall(payload)
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
    if not buf.strip():
        raise HerdrError(f"herdr closed the connection on {method}")
    data = json.loads(buf.decode("utf-8", "replace").splitlines()[0])
    if "error" in data:
        raise HerdrError(json.dumps(data["error"])[:500])
    return data.get("result", {})


def _default_socket() -> str:
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", ""), "herdr", "herdr.sock")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "herdr", "herdr.sock")


def snapshot() -> dict:
    try:
        res = request("session.snapshot", {}, timeout=6)
        return res.get("snapshot", res)
    except (HerdrError, OSError, ValueError):
        return run("api", "snapshot").get("snapshot", {})


def report_metadata(kind: str, target: str, source: str, tokens: dict[str, str | None], seq: int) -> None:
    """Set (None: clear) several sidebar tokens of one pane or workspace in one socket call."""
    request(f"{kind}.report_metadata", {f"{kind}_id": target, "source": source, "tokens": tokens, "seq": seq},
            timeout=6)


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
