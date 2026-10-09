"""What is running right now, inside herdr or not, and what it is doing.

* Claude Code keeps a registry of running interactive sessions in ~/.claude/sessions/<pid>.json
  (cwd, name, busy/idle). A session there whose pid is alive is running, even in another terminal.
* Codex has no registry: a non-subagent rollout written in the last few minutes counts as active.
* Sub-agents: Claude writes <transcript dir>/<session>/subagents/agent-*.jsonl (+ .meta.json);
  Codex writes child threads with thread_source "subagent". Recently written = running, and a
  background sub-agent counts until its task-notification arrives, however quiet it is.
* Background jobs (Claude): a Bash result carrying `backgroundTaskId` starts one, its
  <task-notification> with a final status ends it; jobs from before the process started died with it.
  Dynamic workflows the same way (an `async_launched` result with taskType local_workflow); their
  agents write <session>/subagents/workflows/<run id>/agent-*.jsonl and belong to that background task (⟳), not to the session's ↳ count.
* Codex: a sub-agent is a child rollout (session_meta.source.subagent.thread_spawn: parent, nickname,
  role) whose newest turn is still open; a background job is an exec_command whose output carries a
  `session_id` (unified exec keeps the process) until a write_stdin on it returns an exit code.
* Activity: the last tool call (or message) in the tail of the transcript.
"""
from __future__ import annotations

import functools
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import insight, projects
from .sessions import Session, _claude_root, _loads
from .filememo import by_file

ACTIVE_SECONDS = 150        # a transcript written this recently is "running"
CODEX_BUSY_SECONDS = 900    # a Codex turn that started and never ended counts as running this long after its last write
CODEX_IDLE_SECONDS = 900    # a Codex session outside herdr stays listed (idle) this long after its last write
TAIL = 64 * 1024
CODEX_JOB_SECONDS = 6 * 3600  # a Codex job older than this is assumed dead with its process: the rollout never says so
BG_FIRST = 4_000_000        # the first look at a transcript reads this much of its end for background jobs


@dataclass
class SubAgent:
    name: str
    description: str
    kind: str
    model: str
    mtime: float
    activity: str = ""
    workflow: str = ""  # run id when the agent works for a dynamic workflow (a ⟳ task), not for the session (↳)


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
    jobs: int = 0               # background jobs (shell commands) still running
    workflows: int = 0          # dynamic workflows still running
    started_at: float = 0.0     # when the process started (epoch seconds), 0 if unknown

    @property
    def project(self) -> projects.Project:
        return projects.resolve(self.cwd)


@functools.lru_cache(maxsize=1)
def _kernel32():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    return k32


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = _kernel32()
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


@by_file
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


_TASK_END = re.compile(r"<task-id>([^<]+)</task-id>.*?<status>([a-z_]+)</status>", re.S)
_BG: dict[str, list] = {}  # transcript -> [read up to, {task id: (kind, started)}, {ended ids}]


def _epoch(ts: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _bg_scan(state: list, data: bytes) -> None:
    started, ended = state[1], state[2]
    for raw in data.split(b"\n"):
        if b"backgroundTaskId" not in raw and b"async_launched" not in raw and b"<task-notification>" not in raw:
            continue
        rec = _loads(raw.decode("utf-8", "replace"))
        if not isinstance(rec, dict) or rec.get("isSidechain"):
            continue
        res = rec.get("toolUseResult")
        if isinstance(res, dict):
            at = _epoch(rec.get("timestamp", ""))
            if res.get("backgroundTaskId"):
                started[res["backgroundTaskId"]] = ("job", at, "")
            elif res.get("status") == "async_launched":
                if res.get("agentId"):  # a background sub-agent: its notice carries the agent id
                    started[res["agentId"]] = ("agent", at, "")
                elif res.get("taskType") == "local_workflow" and res.get("taskId"):
                    started[res["taskId"]] = ("workflow", at, str(res.get("runId") or ""))
                elif res.get("taskId"):  # any other background task (a monitor, ...)
                    started[res["taskId"]] = ("job", at, "")
        # the notification itself, as queued for the model (not a tool result quoting one)
        if rec.get("type") == "queue-operation" and rec.get("operation") == "enqueue":
            text = rec.get("content")
        elif rec.get("type") == "attachment":
            text = (rec.get("attachment") or {}).get("prompt")
        elif rec.get("type") == "user":
            text = (rec.get("message") or {}).get("content")
        else:
            continue
        if isinstance(text, str) and text.lstrip().startswith("<task-notification>"):
            for tid, status in _TASK_END.findall(text):
                if status != "running":
                    ended.add(tid)


@dataclass
class Background:
    jobs: int = 0                                  # background shell jobs and other tasks
    agents: set[str] = field(default_factory=set)  # background sub-agents' ids
    runs: set[str] = field(default_factory=set)    # dynamic workflows' run ids


def background(transcript: str, since: float = 0.0) -> Background:
    """What a Claude session still has running in the background. Reads only what was appended
    since the last call."""
    try:
        size = os.path.getsize(transcript)
    except OSError:
        return Background()
    state = _BG.get(transcript)
    if state is None or size < state[0]:  # new, or rewritten
        state = _BG[transcript] = [max(0, size - BG_FIRST), {}, set()]
    if size > state[0]:
        try:
            with open(transcript, "rb") as f:
                f.seek(state[0])
                data = f.read(size - state[0])
        except OSError:
            data = b""
        cut = data.rfind(b"\n") + 1  # a half-written last line waits for the next call
        _bg_scan(state, data[:cut])
        state[0] += cut
    live = [(tid, kind, run) for tid, (kind, at, run) in state[1].items() if tid not in state[2] and at >= since]
    return Background(jobs=sum(1 for _, k, _ in live if k == "job"),
                      agents={tid for tid, k, _ in live if k == "agent"},
                      runs={run or tid for tid, k, run in live if k == "workflow"})


def _agent_files(d: Path) -> list[tuple[Path, float]]:
    try:  # scandir: on Windows the listing carries the times, no stat call per sub-agent file
        with os.scandir(d) as it:
            return [(Path(e.path), e.stat().st_mtime) for e in it
                    if e.name.startswith("agent-") and e.name.endswith(".jsonl")]
    except OSError:
        return []


def _subagent_files(transcript: str, now: float, keep: set[str] = frozenset(),
                    runs: set[str] = frozenset()) -> list[tuple[Path, float]]:
    """The running sub-agents' transcripts: written recently, or a background one not yet done;
    plus the recently written agents of the session's running workflows."""
    d = Path(transcript).with_suffix("") / "subagents"
    out = [(f, m) for f, m in _agent_files(d)
           if now - m <= ACTIVE_SECONDS or f.stem.removeprefix("agent-") in keep]
    for run in runs:
        out += [(f, m) for f, m in _agent_files(d / "workflows" / run) if now - m <= ACTIVE_SECONDS]
    return out


def work_counts(now: float) -> list[tuple[str, int, int, int]]:
    """(session id, sub-agents, workflows, background jobs) per running Claude session: cheap enough to
    ask every few seconds (only appended transcript lines are read, sub-agents are a listing)."""
    out = []
    for r in _claude_registry(now):
        tr = _transcript_of(r.session_id)
        if tr:
            bg = background(tr, r.started_at)
            own = [f for f, _ in _subagent_files(tr, now, bg.agents, bg.runs) if not _workflow_of(f)]
            out.append((r.session_id, len(own), len(bg.runs), bg.jobs))
    return out + _codex_counts(now)


_TRANSCRIPTS: dict[str, str] = {}


def _transcript_of(session_id: str) -> str:
    tr = _TRANSCRIPTS.get(session_id)
    if tr and os.path.exists(tr):
        return tr
    hits = list(_claude_root().glob(f"*/{session_id}.jsonl"))
    _TRANSCRIPTS[session_id] = tr = str(hits[0]) if hits else ""
    return tr


def _workflow_of(f: Path) -> str:
    """The run id of a workflow agent's transcript (subagents/workflows/<run>/agent-*.jsonl), else ""."""
    return f.parent.name if f.parent.parent.name == "workflows" else ""


def claude_subagents(transcript: str, now: float, keep: set[str] = frozenset(),
                     runs: set[str] = frozenset()) -> list[SubAgent]:
    out = []
    for f, m in _subagent_files(transcript, now, keep, runs):
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
            workflow=_workflow_of(f),
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
            user_named=d.get("nameSource") == "user", started_at=float(d.get("startedAt") or 0) / 1000,
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
    children = codex_children(sessions, now)
    for s in sessions:
        age = now - s.mtime
        if s.cli != "codex" or s.subagent or age > max(CODEX_BUSY_SECONDS, CODEX_IDLE_SECONDS):
            continue
        # no process to check: the rollout says whether a turn is open (a long tool call writes
        # nothing for minutes, so "recently written" alone would call a busy agent gone)
        turn = _turn(s.path)
        if (turn == "working" and age <= CODEX_BUSY_SECONDS) or (not turn and age <= ACTIVE_SECONDS):
            status = "busy"
        elif age <= CODEX_IDLE_SECONDS:
            status = "idle"
        else:
            continue
        out.append(Running(cli="codex", session_id=s.id, name=s.title, cwd=s.cwd,
                           status=status, transcript=s.path, user_named=s.named))
    for r in out:
        if r.transcript:
            r.activity = activity(r.transcript)
            if r.cli == "claude":
                bg = background(r.transcript, r.started_at)
                r.jobs, r.workflows = bg.jobs, len(bg.runs)
                r.subagents = claude_subagents(r.transcript, now, bg.agents, bg.runs)
            elif r.cli == "codex":
                r.jobs = codex_jobs(r.transcript, now)
                _SEEN[r.transcript] = r.session_id
        r.subagents += children.get(r.session_id, [])
    return out


_turn = by_file(insight.codex_turn)  # every refresh asks; an unchanged rollout is not re-read
_CODEX_META: dict[str, dict] = {}
_CHILDREN: dict[str, list[str]] = {}  # parent id -> child rollouts of the last listing (the daemon re-asks their turns)
_SEEN: dict[str, str] = {}            # Codex rollouts the panes/listing asked about: the daemon watches them


def _codex_meta(path: str) -> dict:
    """A rollout's session_meta payload (the first line never changes: remembered by path)."""
    m = _CODEX_META.get(path)
    if m is None:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                d = _loads(fh.readline()) or {}
        except OSError:
            return {}
        m = _CODEX_META[path] = d.get("payload") if isinstance(d.get("payload"), dict) else {}
    return m


def _spawn(path: str) -> dict:
    src = _codex_meta(path).get("source")
    spawn = ((src.get("subagent") or {}).get("thread_spawn") if isinstance(src, dict) else None) or {}
    return spawn if isinstance(spawn, dict) else {}


def _codex_parent(path: str) -> str:
    return _codex_meta(path).get("parent_thread_id") or _spawn(path).get("parent_thread_id", "")


def _child_running(path: str, now: float) -> bool:
    """A child thread works while its newest turn is open (idle children stay around for follow-ups).
    No turn marker at all: "written just now"."""
    try:
        age = now - os.path.getmtime(path)
    except OSError:
        return False
    turn = _turn(path)
    return (turn == "working" and age <= CODEX_BUSY_SECONDS) or (not turn and age <= ACTIVE_SECONDS)


def codex_children(sessions: list[Session], now: float) -> dict[str, list[SubAgent]]:
    """parent session id -> its running sub-agents (child rollouts, named by nickname)."""
    kids: dict[str, list[SubAgent]] = {}
    seen: dict[str, list[str]] = {}
    for s in sessions:
        if s.cli != "codex" or not s.subagent or now - s.mtime > CODEX_BUSY_SECONDS:
            continue
        parent = _codex_parent(s.path)
        seen.setdefault(parent, []).append(s.path)
        if _child_running(s.path, now):
            m, sp = _codex_meta(s.path), _spawn(s.path)
            role = m.get("agent_role") or sp.get("agent_role") or ""
            nick = m.get("agent_nickname") or sp.get("agent_nickname") or ""
            kids.setdefault(parent, []).append(SubAgent(
                name=nick or role or s.title[:30], description=s.title, kind=role or "codex", model="",
                mtime=s.mtime, activity=activity(s.path)))
    _CHILDREN.clear()
    _CHILDREN.update(seen)
    return kids


_JOB_ID = re.compile(r'"session_id"\s*:\s*(\d+)|running with session ID (\d+)')
_EXIT = re.compile(r'"exit_code"\s*:')
_STDIN_ID = re.compile(r'"?session_id"?\s*:\s*(\d+)')
_CALLS = re.compile(r'tools\.(exec_command|write_stdin)\((?:\{\s*session_id\s*:\s*(\d+))?')
_CJ: dict[str, list] = {}  # rollout -> [read up to, {unified-exec session id: started}, {call id: [polled id or ""]}]


def _output_text(p: dict) -> str:
    o = p.get("output")
    if isinstance(o, list):
        return "".join(x.get("text", "") for x in o if isinstance(x, dict))
    return o if isinstance(o, str) else ""


def _cj_scan(state: list, data: bytes) -> None:
    jobs, pending = state[1], state[2]
    for raw in data.split(b"\n"):
        # only what can matter is parsed: a rollout is mostly reasoning and file dumps
        if b"session_id" not in raw and b"session ID" not in raw and b"Process exited" not in raw and not any(c in raw for c in pending):
            continue
        rec = _loads(raw.decode("utf-8", "replace"))
        p = rec.get("payload") if isinstance(rec, dict) and rec.get("type") == "response_item" else None
        if not isinstance(p, dict) or not isinstance(p.get("call_id"), str):
            continue
        kind, cid = p.get("type"), p["call_id"].encode()
        if kind in ("function_call", "custom_tool_call"):
            text = str(p.get("arguments") or p.get("input") or "")
            # what each exec_command/write_stdin in the call will answer: "" = a new command, else the polled id
            polls = [i or "" for _, i in _CALLS.findall(text)] if "tools." in text else (
                [(_STDIN_ID.findall(text) or [""])[0]] if p.get("name") == "write_stdin" else [""])
            if any(polls):
                pending[cid] = polls
        elif kind in ("function_call_output", "custom_tool_call_output"):
            text = _output_text(p)
            polled = pending.pop(cid, [])
            # a script's results come in call order, one "chunk_id" each (plain mode: the whole text)
            parts = text.split('"chunk_id"')[1:] or [text]
            at = _epoch(rec.get("timestamp", ""))
            for n, part in enumerate(parts):
                ids = [x or y for x, y in _JOB_ID.findall(part)]
                if ids:  # the process is still there (first sight, or a poll that found it running)
                    for i in ids:
                        jobs.setdefault(i, at)
                elif len(parts) == len(polled) and polled[n] and (_EXIT.search(part) or "Process exited" in part):
                    jobs.pop(polled[n], None)  # the polled process ended (unpaired results are left alone)


def codex_jobs(rollout: str, now: float) -> int:
    """Background terminals a Codex session still has: unified-exec processes that returned a
    session id and were not seen to exit. Reads only what was appended since the last call."""
    try:
        size = os.path.getsize(rollout)
    except OSError:
        return 0
    state = _CJ.get(rollout)
    if state is None or size < state[0]:  # new, or rewritten
        state = _CJ[rollout] = [max(0, size - BG_FIRST), {}, {}]
    if size > state[0]:
        try:
            with open(rollout, "rb") as f:
                f.seek(state[0])
                data = f.read(size - state[0])
        except OSError:
            data = b""
        cut = data.rfind(b"\n") + 1  # a half-written last line waits for the next call
        _cj_scan(state, data[:cut])
        state[0] += cut
    return sum(1 for at in state[1].values() if now - at <= CODEX_JOB_SECONDS)


def codex_work(s: Session, kids: dict[str, list[SubAgent]], now: float) -> tuple[list[SubAgent], int]:
    """(sub-agents, background jobs) of one Codex session, for a herdr pane that running() no
    longer lists (idle a long while, yet its background terminals may still run)."""
    _SEEN[s.path] = s.id
    return kids.get(s.id, []), codex_jobs(s.path, now)


def _codex_counts(now: float) -> list[tuple[str, int, int, int]]:
    home = str(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions")
    out = []
    for path, sid in list(_SEEN.items()):
        try:
            fresh = now - os.path.getmtime(path) <= CODEX_JOB_SECONDS
        except OSError:
            fresh = False
        if not fresh:
            del _SEEN[path]
        elif path.startswith(home):
            out.append((sid, sum(_child_running(c, now) for c in _CHILDREN.get(sid, [])), 0, codex_jobs(path, now)))
    return out
