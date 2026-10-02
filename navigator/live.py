"""What is running right now, inside herdr or not, and what it is doing.

* Claude Code keeps a registry of running interactive sessions in ~/.claude/sessions/<pid>.json
  (cwd, name, busy/idle). A session there whose pid is alive is running, even in another terminal.
* Codex has no registry: a non-subagent rollout written in the last few minutes counts as active.
* Sub-agents: Claude writes <transcript dir>/<session>/subagents/agent-*.jsonl (+ .meta.json);
  Codex writes child threads with thread_source "subagent". Recently written = running.
* Activity: the last tool call (or message) in the tail of the transcript.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import projects
from .sessions import Session, _claude_root, _loads

ACTIVE_SECONDS = 150        # a transcript written this recently is "running"
TAIL = 64 * 1024


@dataclass
class SubAgent:
    name: str
    description: str
    kind: str
    model: str
    mtime: float
    activity: str = ""


@dataclass
class Running:
    cli: str
    session_id: str
    name: str
    cwd: str
    status: str                 # busy | idle | active (codex, inferred)
    pid: int = 0
    user_named: bool = False    # the user named it (claude -n / /rename), vs. a derived name
    transcript: str = ""
    activity: str = ""
    subagents: list[SubAgent] = field(default_factory=list)

    @property
    def project(self) -> projects.Project:
        return projects.resolve(self.cwd)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = wintypes.DWORD()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _tail_records(path: str, nbytes: int = TAIL) -> list[dict]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - nbytes))
            data = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    if size > nbytes:
        data = data[1:]
    return [d for d in (_loads(x) for x in data) if d]


def _short(v, n: int = 70) -> str:
    s = " ".join(str(v).split())
    return s if len(s) <= n else s[: n - 1] + "…"


_ARG_KEYS = ("description", "command", "file_path", "path", "pattern", "url", "query", "prompt", "skill")


def _describe_tool(name: str, args) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return f"{name}: {_short(args, 60)}"
    if isinstance(args, dict):
        for k in _ARG_KEYS:
            if args.get(k):
                v = args[k]
                if k in ("file_path", "path"):
                    v = os.path.basename(str(v)) or v
                return f"{name}: {_short(v, 60)}"
    return name


def activity(path: str) -> str:
    """The most recent thing the agent did, from the transcript tail."""
    for d in reversed(_tail_records(path)):
        # Claude
        if d.get("type") == "assistant":
            content = (d.get("message") or {}).get("content")
            if isinstance(content, list):
                for part in reversed(content):
                    if part.get("type") == "tool_use":
                        return "▸ " + _describe_tool(part.get("name", "tool"), part.get("input"))
                    if part.get("type") == "text" and part.get("text", "").strip():
                        return "💬 " + _short(part["text"])
        # Codex
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        if d.get("type") == "response_item":
            if p.get("type") in ("function_call", "custom_tool_call"):
                return "▸ " + _describe_tool(p.get("name", "tool"), p.get("arguments") or p.get("input"))
            if p.get("type") in ("message", "agent_message") and p.get("role") == "assistant":
                for part in p.get("content") or []:
                    if isinstance(part, dict) and part.get("text"):
                        return "💬 " + _short(part["text"])
    return ""


def claude_subagents(transcript: str, now: float) -> list[SubAgent]:
    d = Path(transcript).with_suffix("") / "subagents"
    out = []
    try:
        files = list(d.glob("agent-*.jsonl"))
    except OSError:
        return out
    for f in files:
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        if now - m > ACTIVE_SECONDS:
            continue
        meta = {}
        try:
            meta = json.loads(f.with_suffix(".meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        out.append(SubAgent(
            name=meta.get("name") or meta.get("agentType") or f.stem.removeprefix("agent-")[:12],
            description=meta.get("description", ""),
            kind=meta.get("customAgentType") or meta.get("agentType") or "",
            model=meta.get("model", ""),
            mtime=m,
            activity=activity(str(f)),
        ))
    return sorted(out, key=lambda s: -s.mtime)


def _claude_registry(now: float) -> list[Running]:
    reg = _claude_root().parent / "sessions"
    out = []
    try:
        files = list(reg.glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("kind") not in (None, "interactive") or not pid_alive(int(d.get("pid", 0))):
            continue
        out.append(Running(
            cli="claude", session_id=d.get("sessionId", ""), name=d.get("name", ""),
            cwd=d.get("cwd", ""), status=d.get("status", "idle"), pid=int(d.get("pid", 0)),
            user_named=d.get("nameSource") == "user",
        ))
    return out


def running(sessions: list[Session]) -> list[Running]:
    """All running top-level agents with activity and live sub-agents."""
    now = time.time()
    by_id = {(s.cli, s.id): s for s in sessions}
    out = _claude_registry(now)
    for r in out:
        s = by_id.get(("claude", r.session_id))
        if s:
            r.transcript = s.path
        else:  # not in the index (e.g. hidden cwd): look the transcript up directly
            hits = list(_claude_root().glob(f"*/{r.session_id}.jsonl"))
            r.transcript = str(hits[0]) if hits else ""
    # Codex: recently written top-level threads; children attach to their parent
    children: dict[str, list[SubAgent]] = {}
    for s in sessions:
        if s.cli != "codex" or now - s.mtime > ACTIVE_SECONDS:
            continue
        if s.subagent:
            parent = _codex_parent(s.path)
            children.setdefault(parent, []).append(SubAgent(
                name=s.title[:30], description=s.title, kind="codex", model="", mtime=s.mtime,
                activity=activity(s.path)))
        else:
            out.append(Running(cli="codex", session_id=s.id, name=s.title, cwd=s.cwd,
                               status="active", transcript=s.path, user_named=s.named))
    for r in out:
        if r.transcript:
            r.activity = activity(r.transcript)
            if r.cli == "claude":
                r.subagents = claude_subagents(r.transcript, now)
        r.subagents += children.get(r.session_id, [])
    return out


def _codex_parent(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            d = _loads(fh.readline()) or {}
    except OSError:
        return ""
    return (d.get("payload") or {}).get("parent_thread_id", "")
