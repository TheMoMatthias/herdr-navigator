"""Index resumable sessions of every agent CLI on this machine.

Providers read only the head and tail of each transcript (they can be hundreds of MB) and
cache the result by (mtime, size), so a refresh after the first run touches changed files only.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import projects, settings

HEAD_BYTES = 96 * 1024
TAIL_BYTES = 256 * 1024
CACHE_VERSION = 4


@dataclass
class Session:
    cli: str            # "claude" | "codex"
    id: str
    cwd: str
    title: str
    last_prompt: str
    branch: str
    mtime: float
    path: str
    subagent: bool = False
    origin: str = ""    # e.g. "cli", "Codex Desktop"

    @property
    def project(self) -> projects.Project:
        return projects.resolve(self.cwd)

    def resume_command(self) -> str:
        tpl = settings.load().launch.get(f"{self.cli}_resume", "")
        return tpl.format(id=self.id)


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
            if isinstance(part, dict) and part.get("type") in ("text", "input_text"):
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
    if not cwd:
        return None
    st = path.stat()
    return Session(
        cli="claude",
        id=sid,
        cwd=cwd,
        title=_clip(title or first_prompt or _clean(last_prompt) or "(untitled)"),
        last_prompt=_clip(_clean(last_prompt) or first_prompt, 200),
        branch=branch,
        mtime=st.st_mtime,
        path=str(path),
        subagent=entry.startswith("sdk"),
        origin=entry or "cli",
    )


def _claude_files(cutoff: float) -> list[Path]:
    root = _claude_root()
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


# --- index ---------------------------------------------------------------------

PROVIDERS = {
    "claude": (_claude_files, _parse_claude),
    "codex": (_codex_files, _parse_codex),
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
            try:
                st = f.stat()
            except OSError:
                continue
            k = str(f)
            ent = entries.get(k)
            if ent and ent["m"] == st.st_mtime and ent["s"] == st.st_size:
                rec = ent["r"]
            else:
                try:
                    s = parse(f)
                except OSError:
                    s = None
                rec = asdict(s) if s else None
            fresh[k] = {"m": st.st_mtime, "s": st.st_size, "r": rec}
            if not rec:
                continue
            s = Session(**rec)
            if cli == "codex" and s.id in codex_names:
                s.title = _clip(codex_names[s.id])
            if include_hidden or is_listed(s):
                out.append(s)
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
