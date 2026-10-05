"""Index resumable sessions of every agent CLI on this machine.

Providers read only the head and tail of each transcript (they can be hundreds of MB) and
cache the result by (mtime, size), so a refresh after the first run touches changed files only.
"""
from __future__ import annotations

import functools
import json
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import projects, settings

HEAD_BYTES = 96 * 1024
TAIL_BYTES = 256 * 1024
CACHE_VERSION = 9  # bumped: sessions without a cwd record


@dataclass
class Session:
    cli: str            # provider key: "claude", "codex", "pi", "opencode", "kilo", ...
    id: str
    cwd: str
    title: str
    last_prompt: str
    branch: str
    mtime: float
    path: str
    subagent: bool = False
    origin: str = ""    # e.g. "cli", "Codex Desktop"
    named: bool = False  # title is a name the user/provider gave it, not a first prompt

    @property
    def project(self) -> projects.Project:
        return projects.resolve(self.cwd)

    def resume_command(self) -> str:
        tpl = settings.load().launch.get(f"{self.cli}_resume", "")
        from .startup import _q
        return tpl.replace("{id}", self.id).replace("{name}", _q(self.title[:40] if self.named else self.id[:8]))


def _read_head_tail(path: Path) -> tuple[list[str], list[str]]:
    size = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(HEAD_BYTES)
        tail = b""
        if size > HEAD_BYTES:
            fh.seek(max(HEAD_BYTES, size - TAIL_BYTES))
            tail = fh.read()
    head_lines = head.decode("utf-8", "replace").splitlines()
    if len(head) == HEAD_BYTES and head_lines:
        head_lines = head_lines[:-1]  # drop the cut-off line
    tail_lines = tail.decode("utf-8", "replace").splitlines()[1:] if tail else []
    return head_lines, tail_lines


def _loads(line: str) -> dict | None:
    try:
        d = json.loads(line)
        return d if isinstance(d, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


_NOISE = re.compile(r"^\s*(<|\[Request interrupted|Caveat:)")


def _text_of(content) -> str:
    if isinstance(content, str):
        return "" if _NOISE.match(content) else content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("text", "input_text", None):  # Gemini/Qwen parts are untyped
                t = part.get("text", "")
                if t and not _NOISE.match(t):
                    return t
    return ""


def _clean(s: str) -> str:
    return "" if not s or _NOISE.match(s) else s


def _clip(s: str, n: int = 140) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


# --- Claude Code ---------------------------------------------------------------

def _claude_root() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"


@functools.lru_cache(maxsize=1024)
def _decode_project_dir(name: str) -> str:
    """Claude names a project folder after its path, every character that is not a letter or
    digit replaced by '-'. That is lossy, so find the real folder on disk ('' if it is gone)."""
    enc = lambda x: re.sub(r"[^A-Za-z0-9]", "-", x)
    if os.name == "nt":
        m = re.match(r"^([A-Za-z])--(.*)$", name)
        if not m:
            return ""
        base, rest = m.group(1) + ":\\", m.group(2)
    else:
        if not name.startswith("-"):
            return ""
        base, rest = "/", name[1:]

    def walk(d: str, rest: str, depth: int = 0) -> str:
        if not rest:
            return d
        if depth > 24:
            return ""
        try:
            kids = sorted(os.listdir(d), key=len, reverse=True)
        except OSError:
            return ""
        for k in kids:
            e = enc(k)
            if rest != e and not rest.startswith(e + "-"):
                continue
            sub = os.path.join(d, k)
            if not os.path.isdir(sub):
                continue
            found = walk(sub, rest[len(e) + 1:], depth + 1) if rest != e else sub
            if found:
                return found
        return ""
    return walk(base, rest)


def _parse_claude(path: Path) -> Session | None:
    head, tail = _read_head_tail(path)
    sid = path.stem
    cwd = branch = first_prompt = title = last_prompt = entry = ""
    for line in head:
        d = _loads(line)
        if not d:
            continue
        cwd = cwd or d.get("cwd", "")
        branch = branch or d.get("gitBranch", "") or ""
        entry = entry or d.get("entrypoint", "")
        if not first_prompt and d.get("type") == "user" and not d.get("isMeta") and not d.get("isSidechain"):
            first_prompt = _text_of((d.get("message") or {}).get("content"))
        if d.get("type") == "custom-title":
            title = d.get("customTitle", "") or title
        if d.get("type") == "summary" and not title:
            title = d.get("summary", "")
        if d.get("type") == "last-prompt":
            last_prompt = d.get("lastPrompt", "") or last_prompt
    for line in tail:
        d = _loads(line)
        if not d:
            continue
        t = d.get("type")
        if t == "custom-title":
            title = d.get("customTitle", "") or title
        elif t == "last-prompt":
            last_prompt = d.get("lastPrompt", "") or last_prompt
        elif d.get("gitBranch"):
            branch = d["gitBranch"]
        # Keep the launch cwd: `claude --resume` looks sessions up by it, and later records
        # follow in-session `cd`s.
        cwd = cwd or d.get("cwd", "")
    if not cwd:  # named but never ran a turn: only the project folder says where it belongs
        cwd = _decode_project_dir(path.parent.name)
    if not cwd:
        return None
    st = path.stat()
    return Session(
        cli="claude",
        id=sid,
        cwd=cwd,
        named=bool(title),
        title=_clip(title or first_prompt or _clean(last_prompt) or "(untitled)"),
        last_prompt=_clip(_clean(last_prompt) or first_prompt, 200),
        branch=branch,
        mtime=st.st_mtime,
        path=str(path),
        subagent=entry.startswith("sdk"),
        origin=entry or "cli",
    )


# stat results the listing already has: on Windows a directory listing carries size and times, so
# reusing them saves one system call per transcript (~1,600 a scan) in load_sessions
_LISTED: dict[str, os.stat_result] = {}


def _claude_files(cutoff: float) -> list[Path]:
    root = _claude_root()
    out = []
    try:
        projs = [e for e in os.scandir(root) if e.is_dir()]
    except OSError:
        return out
    for proj in projs:
        try:
            with os.scandir(proj.path) as it:
                for e in it:
                    if not e.name.endswith(".jsonl") or not e.is_file():
                        continue
                    st = e.stat()
                    if st.st_mtime >= cutoff:
                        _LISTED[e.path] = st
                        out.append(Path(e.path))
        except OSError:
            continue
    return out


# --- Codex ---------------------------------------------------------------------

def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def _codex_names() -> dict[str, str]:
    names: dict[str, str] = {}
    idx = _codex_home() / "session_index.jsonl"
    try:
        with open(idx, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                d = _loads(line)
                if d and d.get("id") and d.get("thread_name"):
                    names[d["id"]] = d["thread_name"]
    except OSError:
        pass
    return names


def _parse_codex(path: Path) -> Session | None:
    head, tail = _read_head_tail(path)
    meta = None
    first_prompt = last_prompt = branch = ""
    for line in head:
        d = _loads(line)
        if not d:
            continue
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        if d.get("type") == "session_meta" and meta is None:
            meta = p
            branch = ((p.get("git") or {}).get("branch") or "") if isinstance(p.get("git"), dict) else ""
        elif d.get("type") == "event_msg" and p.get("type") == "user_message" and not first_prompt:
            first_prompt = p.get("message", "")
        elif d.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "user" and not first_prompt:
            first_prompt = _text_of(p.get("content"))
    if not meta or not meta.get("id"):
        return None
    for line in reversed(tail):
        d = _loads(line)
        if not d:
            continue
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        if d.get("type") == "event_msg" and p.get("type") == "user_message":
            last_prompt = p.get("message", "")
            break
    st = path.stat()
    return Session(
        cli="codex",
        id=meta["id"],
        cwd=meta.get("cwd", ""),
        title=_clip(first_prompt or "(untitled)"),
        last_prompt=_clip(last_prompt or first_prompt, 200),
        branch=branch,
        mtime=st.st_mtime,
        path=str(path),
        subagent=meta.get("thread_source") == "subagent",
        origin=meta.get("originator", ""),
    )


def _codex_files(cutoff: float) -> list[Path]:
    root = _codex_home() / "sessions"
    if not root.is_dir():
        return []
    out = []
    for f in root.rglob("rollout-*.jsonl"):
        try:
            if f.stat().st_mtime >= cutoff:
                out.append(f)
        except OSError:
            continue
    return out


# --- pi ------------------------------------------------------------------------

def _pi_root() -> Path:
    return Path(os.environ.get("PI_CODING_AGENT_DIR", Path.home() / ".pi" / "agent")) / "sessions"


_PI_SUBAGENT_NAME = re.compile(r"#[0-9a-f]{8}$")


def _parse_pi(path: Path) -> Session | None:
    head, tail = _read_head_tail(path)
    meta = None
    name = first_prompt = last_prompt = ""
    for line in head:
        d = _loads(line)
        if not d:
            continue
        t = d.get("type")
        if t == "session" and meta is None:
            meta = d
        elif t == "session_info" and d.get("name"):
            name = d["name"]
        elif t == "message" and not first_prompt:
            m = d.get("message") or {}
            if m.get("role") == "user":
                first_prompt = _text_of(m.get("content"))
    if not meta or not meta.get("id"):
        return None
    for line in tail:
        d = _loads(line)
        if d and d.get("type") == "session_info" and d.get("name"):
            name = d["name"]
    for line in reversed(tail or head):
        d = _loads(line)
        m = (d or {}).get("message") or {}
        if d and d.get("type") == "message" and m.get("role") == "user":
            last_prompt = _text_of(m.get("content"))
            if last_prompt:
                break
    st = path.stat()
    return Session(
        cli="pi",
        id=meta["id"],
        cwd=meta.get("cwd", ""),
        named=bool(name),
        title=_clip(name or first_prompt or "(untitled)"),
        last_prompt=_clip(last_prompt or first_prompt, 200),
        branch="",
        mtime=st.st_mtime,
        path=str(path),
        # pi names spawned helper sessions "<agent>#<8 hex>"
        subagent=bool(_PI_SUBAGENT_NAME.search(name)),
        origin="cli",
    )


def _pi_files(cutoff: float) -> list[Path]:
    root = _pi_root()
    out = []
    if not root.is_dir():
        return out
    for proj in root.iterdir():
        if not proj.is_dir():
            continue
        try:
            for f in proj.glob("*.jsonl"):
                if f.stat().st_mtime >= cutoff:
                    out.append(f)
        except OSError:
            continue
    return out


def _recent(root: Path, pattern: str, cutoff: float) -> list[Path]:
    if not root.is_dir():
        return []
    out = []
    for f in root.glob(pattern):
        try:
            if f.is_file() and f.stat().st_mtime >= cutoff:
                out.append(f)
        except OSError:
            continue
    return out


# --- Qwen Code (Claude-style JSONL) ------------------------------------------------

def _qwen_root() -> Path:
    return Path(os.environ.get("QWEN_RUNTIME_DIR") or os.environ.get("QWEN_HOME") or Path.home() / ".qwen")


def _parse_qwen(path: Path) -> Session | None:
    head, tail = _read_head_tail(path)
    sid = cwd = branch = title = first_prompt = last_prompt = ""
    side = False
    for line in head + tail:
        d = _loads(line)
        if not d:
            continue
        sid = sid or d.get("sessionId", "")
        cwd = cwd or d.get("cwd", "")
        branch = d.get("gitBranch") or branch
        if d.get("type") == "system" and d.get("subtype") == "custom_title":
            title = (d.get("systemPayload") or {}).get("customTitle", "") or title
        elif d.get("type") == "user":
            side = side or bool(d.get("isSidechain"))
            msg = d.get("message") or {}
            t = _text_of(msg.get("content") or msg.get("parts"))
            if t:
                first_prompt = first_prompt or t
                last_prompt = t
    if not cwd:
        return None
    return Session(
        cli="qwen", id=sid or path.stem, cwd=cwd, named=bool(title),
        title=_clip(title or first_prompt or "(untitled)"), last_prompt=_clip(last_prompt or first_prompt, 200),
        branch=branch, mtime=path.stat().st_mtime, path=str(path), subagent=side, origin="cli")


def _qwen_files(cutoff: float) -> list[Path]:
    return _recent(_qwen_root() / "projects", "*/chats/*.jsonl", cutoff)


# --- Gemini CLI ----------------------------------------------------------------------

def _gemini_root() -> Path:
    return Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini"


def _parse_gemini(path: Path) -> Session | None:
    # Project folders carry a .project_root marker; old sha256-named folders don't, so their
    # cwd is unknown and they are skipped.
    chats = next((p for p in path.parents if p.name == "chats"), None)
    try:
        cwd = (chats.parent / ".project_root").read_text(encoding="utf-8").strip() if chats else ""
    except OSError:
        cwd = ""
    if not cwd:
        return None
    head, tail = _read_head_tail(path)
    recs = [r for r in (_loads(line) for line in head + tail) if r]
    if not recs:  # single pretty-printed JSON document
        recs = [_loads(path.read_text(encoding="utf-8", errors="replace")) or {}]
    if len(recs) == 1 and isinstance(recs[0].get("messages"), list):
        recs = [recs[0], *[m for m in recs[0]["messages"] if isinstance(m, dict)]]
    sid = title = first_prompt = last_prompt = ""
    sub = path.parent.name != "chats"  # chats/<parentId>/<id>.jsonl
    for d in recs:
        sid = sid or d.get("sessionId", "")
        title = d.get("summary") or title
        sub = sub or d.get("kind") == "subagent"
        if d.get("type") == "user":
            t = _text_of(d.get("content"))
            if t:
                first_prompt = first_prompt or t
                last_prompt = t
    return Session(
        cli="gemini", id=sid or path.stem, cwd=cwd, named=bool(title),
        title=_clip(title or first_prompt or "(untitled)"), last_prompt=_clip(last_prompt or first_prompt, 200),
        branch="", mtime=path.stat().st_mtime, path=str(path), subagent=sub, origin="cli")


def _gemini_files(cutoff: float) -> list[Path]:
    root = _gemini_root() / "tmp"
    return _recent(root, "*/chats/*.json*", cutoff) + _recent(root, "*/chats/*/*.jsonl", cutoff)


# --- GitHub Copilot CLI ----------------------------------------------------------------

def _copilot_root() -> Path:
    return Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot") / "session-state"


def _parse_copilot(path: Path) -> Session | None:
    """`path` is the session's events.jsonl (or workspace.yaml); metadata is the flat yaml."""
    meta = {}
    for line in (path.parent / "workspace.yaml").read_text(encoding="utf-8", errors="replace").splitlines():
        k, sep, v = line.partition(":")
        if sep and line[:1] not in (" ", "\t", "#", "-"):
            meta[k.strip()] = v.strip().strip("'\"")
    if not meta.get("cwd"):
        return None
    name = meta.get("name", "")
    return Session(
        cli="copilot", id=meta.get("id") or path.parent.name, cwd=meta["cwd"], named=bool(name),
        title=_clip(name or "(untitled)"), last_prompt="", branch=meta.get("branch", ""),
        mtime=path.stat().st_mtime, path=str(path), origin="cli")


def _copilot_files(cutoff: float) -> list[Path]:
    out = []
    for meta in _recent(_copilot_root(), "*/workspace.yaml", 0):
        ev = meta.parent / "events.jsonl"
        f = ev if ev.is_file() else meta  # events.jsonl changes on every turn, so it dates the session
        try:
            if f.stat().st_mtime >= cutoff:
                out.append(f)
        except OSError:
            continue
    return out


# --- OpenCode family (SQLite: opencode, kilo) -----------------------------------------

def _xdg_data() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


# cli -> database file. Kilo is an OpenCode fork with the same schema.
SQLITE_STORES = {
    "opencode": lambda: _xdg_data() / "opencode" / "opencode.db",
    "kilo": lambda: _xdg_data() / "kilo" / "kilo.db",
}

_LAST_USER_SQL = """
SELECT p.data FROM message m JOIN part p ON p.message_id = m.id
WHERE m.session_id = ? AND json_extract(m.data, '$.role') = 'user'
  AND json_extract(p.data, '$.type') = 'text'
ORDER BY m.time_created {order}, p.time_created LIMIT 1
"""


def _sqlite_sessions(cli: str, db: Path, cutoff: float) -> list[Session]:
    """One cheap query per store; a locked or foreign-schema database yields nothing."""
    if not db.is_file():
        return []
    out = []
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            rows = con.execute(
                "SELECT id, directory, title, parent_id, time_updated FROM session"
                " WHERE time_archived IS NULL AND time_updated >= ?", (int(cutoff * 1000),)).fetchall()
            for sid, cwd, title, parent, updated in rows:
                prompts = []
                for order in ("ASC", "DESC"):
                    r = con.execute(_LAST_USER_SQL.format(order=order), (sid,)).fetchone()
                    prompts.append(_text_of([_loads(r[0]) or {}]) if r else "")
                first, last = prompts
                out.append(Session(
                    cli=cli, id=sid, cwd=os.path.normpath(cwd) if cwd else "",
                    named=bool(title), title=_clip(title or first or "(untitled)"),
                    last_prompt=_clip(last or first, 200), branch="", mtime=updated / 1000,
                    path=str(db), subagent=bool(parent), origin="cli"))
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return out


# --- Hermes Agent (SQLite) -------------------------------------------------------------

def _hermes_sessions(cutoff: float) -> list[Session]:
    db = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "state.db"
    if not db.is_file():
        return []
    out = []
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            rows = con.execute("SELECT id, cwd, title, started_at, ended_at FROM sessions"
                               " WHERE source = 'cli'").fetchall()
            for sid, cwd, title, started, ended in rows:
                upd = con.execute("SELECT max(timestamp) FROM messages WHERE session_id = ?", (sid,)).fetchone()[0]
                mtime = _epoch(upd) or _epoch(ended) or _epoch(started)
                if not cwd or mtime < cutoff:
                    continue
                first, last = (
                    (r[0] if r and r[0] else "") for r in (
                        con.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user'"
                                    f" ORDER BY timestamp {order} LIMIT 1", (sid,)).fetchone()
                        for order in ("ASC", "DESC")))
                first, last = _text_of(str(first)), _text_of(str(last))
                out.append(Session(
                    cli="hermes", id=str(sid), cwd=cwd, named=bool(title),
                    title=_clip(title or first or "(untitled)"), last_prompt=_clip(last or first, 200),
                    branch="", mtime=mtime, path=str(db), origin="cli"))
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return out


def _epoch(v) -> float:
    """Unix seconds from seconds, milliseconds or an ISO string; 0 when unknown."""
    if v in (None, ""):
        return 0.0
    try:
        f = float(v)
        return f / 1000 if f > 1e11 else f
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# --- index ---------------------------------------------------------------------

PROVIDERS = {
    "claude": (_claude_files, _parse_claude),
    "codex": (_codex_files, _parse_codex),
    "pi": (_pi_files, _parse_pi),
    "qwen": (_qwen_files, _parse_qwen),
    "gemini": (_gemini_files, _parse_gemini),
    "copilot": (_copilot_files, _parse_copilot),
}


def _cache_path() -> Path:
    return settings.state_dir() / "sessions-cache.json"


def is_listed(s: Session) -> bool:
    """Shown in Resume/Projects: not a spawned helper, not in a hidden directory."""
    cfg = settings.load()
    return not (cfg.hide_subagents and s.subagent) and not projects.is_hidden(s.cwd)


def load_sessions(include_hidden: bool = False) -> list[Session]:
    cfg = settings.load()
    cutoff = time.time() - cfg.max_age_days * 86400
    try:
        cache = json.loads(_cache_path().read_text(encoding="utf-8"))
        if cache.get("v") != CACHE_VERSION:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    entries: dict = cache.get("files", {})
    fresh: dict = {}
    out: list[Session] = []
    codex_names = _codex_names()
    for cli, (files, parse) in PROVIDERS.items():
        for f in files(cutoff):
            k = str(f)
            try:
                st = _LISTED.pop(k, None) or f.stat()
            except OSError:
                continue
            ent = entries.get(k)
            if ent and ent["m"] == st.st_mtime and ent["s"] == st.st_size:
                rec = ent["r"]
            else:
                try:
                    s = parse(f)
                except (OSError, ValueError, TypeError, AttributeError):  # one odd file never sinks the scan
                    s = None
                rec = asdict(s) if s else None
            fresh[k] = {"m": st.st_mtime, "s": st.st_size, "r": rec}
            if not rec:
                continue
            s = Session(**rec)
            if cli == "codex" and s.id in codex_names:
                s.title = _clip(codex_names[s.id])
                s.named = True
            if include_hidden or is_listed(s):
                out.append(s)
    db_sessions = [s for cli, db in SQLITE_STORES.items() for s in _sqlite_sessions(cli, db(), cutoff)]
    for s in db_sessions + _hermes_sessions(cutoff):
        if include_hidden or is_listed(s):
            out.append(s)
    if fresh != entries:  # unchanged (the usual case): no rewrite of the whole cache
        try:
            _cache_path().write_text(json.dumps({"v": CACHE_VERSION, "files": fresh}), encoding="utf-8")
        except OSError:
            pass
    # one row per (cli, id): Codex can write several rollout files for one thread
    best: dict[tuple[str, str], Session] = {}
    for s in out:
        k = (s.cli, s.id)
        if k not in best or s.mtime > best[k].mtime:
            best[k] = s
    return sorted(best.values(), key=lambda s: -s.mtime)
