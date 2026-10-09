"""What a session's transcript says right now: how full its context is, and its last answer.

Claude: the newest main-chain assistant message's usage (input + cache read + cache write) is
the context in use; the window comes from `[context]` in the settings (by model name), and
grows to 1M when a session is seen using more than the configured window.
Codex: `token_count` events carry the last turn's usage and the model's context window.
OpenCode/Kilo (SQLite), pi/omp, Qwen, Gemini: the newest assistant turn's prompt-side tokens
(input + cache read/write), the window by model name (`[context]`, then a built-in table; an
unknown model shows tokens only, window 0).
The bar's full mark is where the session compacts: the CLI's auto-compact setting when set
(Claude `autoCompactWindow`, per model or global; Codex `model_auto_compact_token_limit`;
pi `compaction.reserveTokens`; Qwen's 85% ladder; Gemini `model.compressionThreshold`).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import settings
from .filememo import by_file

TAIL = 600_000


@dataclass
class Context:
    used: int
    window: int

    @property
    def pct(self) -> int:
        return min(100, round(100 * self.used / self.window)) if self.window else 0

    @property
    def level(self) -> str:
        """"ok", "warn" (orange) or "full" (red): by tokens in use (`warn_at` / `full_at` in
        `[context]`, 200K / 700K by default), and by fill for a small window (70% / 85%)."""
        cfg = settings.load().context
        if self.used >= int(cfg.get("full_at", 700_000)) or self.pct >= 85:
            return "full"
        if self.used >= int(cfg.get("warn_at", 200_000)) or self.pct >= 70:
            return "warn"
        return "ok"

    def bar(self, cells: int = 4) -> str:
        """A horizontal bar of the window, ▰▰▱▱ (one cell at least while anything is in use)."""
        n = min(cells, max(1, round(cells * self.used / self.window))) if self.window and self.used else 0
        return "▰" * n + "▱" * (cells - n)

    @property
    def short(self) -> str:
        """312K, 1.2M."""
        return f"{self.used / 1e6:.1f}M" if self.used >= 1_000_000 else f"{round(self.used / 1000)}K"


def _tail(path: str, size: int = TAIL) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            f.seek(max(0, end - size))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if end > size else lines


def _window_for(model: str) -> int:
    windows = settings.load().context
    best, best_len = int(windows.get("default", 200_000)), -1
    for key, val in windows.items():
        if key not in ("default", "warn_at", "full_at") and key.lower() in model.lower() and len(key) > best_len:
            best, best_len = int(val), len(key)
    if "[1m]" in model.lower():
        best = max(best, 1_000_000)
    return best


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


@by_file
def _json(path: str) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


@by_file
def _toml(path: str) -> dict:
    try:
        return tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def claude_compact_at(model: str, cwd: str, model_window: int) -> int:
    """Where Claude Code auto-compacts this session (tokens), 0 when it does not.

    The settings a session runs with: user, then the project's .claude/settings.json and
    settings.local.json. `modelSettings.<model>.autoCompactWindow` beats `autoCompactWindow`;
    "auto" or unset means the model's window, or CLAUDE_AUTOCOMPACT_PCT_OVERRIDE percent of it."""
    files = [_claude_home() / "settings.json"]
    if cwd:
        files += [Path(cwd) / ".claude" / "settings.json", Path(cwd) / ".claude" / "settings.local.json"]
    enabled, window, pct, per_model = True, None, None, {}
    for f in files:
        s = _json(str(f))
        if "autoCompactEnabled" in s:
            enabled = bool(s["autoCompactEnabled"])
        if "autoCompactWindow" in s:
            window = s["autoCompactWindow"]
        pct = (s.get("env") or {}).get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", pct)
        for key, val in (s.get("modelSettings") or {}).items():
            if isinstance(val, dict) and "autoCompactWindow" in val:
                per_model[key] = val["autoCompactWindow"]
    if not enabled:
        return 0
    best = max((k for k in per_model if k.lower() in model.lower()), key=len, default=None)
    if best is not None:
        window = per_model[best]
    try:
        return int(window)
    except (TypeError, ValueError):
        pass
    try:
        return round(model_window * float(pct or os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE")) / 100)
    except (TypeError, ValueError):
        return 0


def codex_compact_at() -> int:
    """Codex's `model_auto_compact_token_limit` (config.toml), 0 when unset."""
    try:
        return int(_toml(str(_codex_home() / "config.toml")).get("model_auto_compact_token_limit") or 0)
    except (TypeError, ValueError):
        return 0


def context(cli: str, path: str, sid: str = "") -> Context | None:
    """Tokens in use against the point where this session compacts: the CLI's auto-compact
    setting when there is one (never above the model's window), else the model's window.
    `sid` picks the session out of a shared store (OpenCode, Kilo: one database for all)."""
    r = _sqlite_reading(cli, path, sid) if cli in SQLITE_CLIS else _reading(cli, path)
    if not r:
        return None
    used, model_window, model, cwd = r
    if cli == "claude":
        at = claude_compact_at(model, cwd, model_window)
    elif cli == "codex":
        at = codex_compact_at()
    else:
        at = _COMPACT_AT.get(cli, lambda w: 0)(model_window) if model_window else 0
    return Context(used, min(at, model_window) if at else model_window)


@by_file
def _reading(cli: str, path: str) -> tuple | None:
    """(tokens in use, model window, model, cwd) from the transcript's tail."""
    if not path:
        return None
    lines = _tail(path)
    if cli == "claude":
        compacted, cwd = 0, ""  # a /compact (or auto-compact) after the last answer: its postTokens is the context now
        for ln in reversed(lines):
            boundary = '"compact_boundary"' in ln
            if not boundary and ('"usage"' not in ln or '"assistant"' not in ln):
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            if rec.get("isSidechain"):
                continue
            cwd = cwd or str(rec.get("cwd") or "")
            if boundary and rec.get("subtype") == "compact_boundary":
                if not compacted:
                    compacted = int((rec.get("compactMetadata") or {}).get("postTokens") or 1)
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            u = msg.get("usage") or {}
            used = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0))                 + int(u.get("cache_creation_input_tokens", 0))
            if not used:
                continue
            model = str(msg.get("model", ""))
            window = _window_for(model)
            if used > window:  # seen above its window: a 1M session
                window = max(window, 1_000_000)
            return compacted or used, window, model, cwd  # the model (window) from the last answer
        return (compacted, _window_for(""), "", cwd) if compacted else None
    if cli == "codex":
        for ln in reversed(lines):
            if '"token_count"' not in ln:
                continue
            try:
                info = (json.loads(ln).get("payload") or {}).get("info") or {}
            except ValueError:
                continue
            last = info.get("last_token_usage") or {}
            window = int(info.get("model_context_window") or 0)
            used = int(last.get("input_tokens", 0)) + int(last.get("output_tokens", 0))
            if used and window:
                return used, window, "", ""
        return None
    if cli in ("pi", "omp"):
        return _pi_reading(lines)
    if cli == "qwen":
        return _qwen_reading(lines)
    if cli == "gemini":
        return _gemini_reading(path, lines)
    return None


# --- the other CLIs --------------------------------------------------------------------

# Context windows of models these CLIs commonly run, under `[context]` (which wins). Longest
# key contained in the model id wins, as there.
WINDOWS = {
    "gemini": 1_048_576, "qwen3-coder-plus": 1_000_000, "qwen3-coder": 262_144, "qwen3-max": 262_144,
    "claude": 200_000, "gpt-5": 400_000, "gpt-4.1": 1_047_576, "o3": 200_000, "o4-mini": 200_000,
    "kimi-k2": 262_144, "glm-4.5": 131_072, "glm-4.6": 200_000, "deepseek": 128_000,
    "grok-code": 256_000, "grok-4": 256_000,
}


def _known_window(model: str) -> int:
    """The window by model name, 0 when unknown (the bar then shows tokens only: a guessed window
    would draw a fill that means nothing)."""
    if not model:
        return 0
    m = model.lower()
    user = {k: v for k, v in settings.load().context.items() if k not in ("default", "warn_at", "full_at")}
    for table in (user, WINDOWS):
        key = max((k for k in table if k.lower() in m), key=len, default=None)
        if key is not None:
            return max(int(table[key]), 1_000_000 if "[1m]" in m else 0)
    return 1_000_000 if "[1m]" in m else 0


def _records(lines: list[str], needle: str):
    """The JSON records among `lines` that contain `needle`, newest first."""
    for ln in reversed(lines):
        if needle in ln:
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            if isinstance(rec, dict):
                yield rec


def _pi_reading(lines: list[str]) -> tuple | None:
    """pi / oh-my-pi: `{"type":"message","message":{"role":"assistant","model",...,"usage":
    {input, output, cacheRead, cacheWrite, totalTokens}}}`; a later `compaction` entry replaces
    the context (omp records its `tokensAfter`, pi does not: 1 until the next answer)."""
    compacted = 0
    for rec in _records(lines, '"type"'):
        if rec.get("type") == "compaction":
            compacted = compacted or int(rec.get("tokensAfter") or 1)
            continue
        msg = rec.get("message") or {}
        if rec.get("type") != "message" or msg.get("role") != "assistant":
            continue
        u = msg.get("usage") or {}
        used = int(u.get("input") or 0) + int(u.get("cacheRead") or 0) + int(u.get("cacheWrite") or 0)
        if used:  # an aborted or failed turn reports nothing
            model = str(msg.get("model") or "")
            return compacted or used, _known_window(model), model, ""
    return (compacted, 0, "", "") if compacted else None


def _qwen_reading(lines: list[str]) -> tuple | None:
    """Qwen Code: assistant records carry Gemini-style `usageMetadata` (promptTokenCount already
    counts the cached part), `model` and `contextWindowSize`; a later `chat_compression` system
    record's `info.newTokenCount` is the context after compressing."""
    compacted = 0
    for rec in _records(lines, '"type"'):
        if rec.get("isSidechain"):
            continue
        if rec.get("type") == "system" and rec.get("subtype") == "chat_compression":
            info = (rec.get("systemPayload") or {}).get("info") or {}
            compacted = compacted or int(info.get("newTokenCount") or 1)
            continue
        used = int((rec.get("usageMetadata") or {}).get("promptTokenCount") or 0)
        if rec.get("type") == "assistant" and used:
            model = str(rec.get("model") or "")
            return compacted or used, int(rec.get("contextWindowSize") or 0) or _known_window(model), model, ""
    return (compacted, 0, "", "") if compacted else None


def _gemini_reading(path: str, lines: list[str]) -> tuple | None:
    """Gemini CLI: `type: "gemini"` message records with `tokens: {input, output, cached, ...}`
    (input is the prompt, cached included) and `model`. JSONL appends a message again when its
    tokens arrive; a legacy .json is one document with a `messages` list."""
    if path.endswith(".json"):
        recs = list(reversed([m for m in _json(path).get("messages") or [] if isinstance(m, dict)]))
    else:
        recs = []
        for rec in _records(lines, '"gemini"'):
            if isinstance((rec.get("$set") or {}).get("messages"), list):  # a rewrite carries them all
                recs += reversed([m for m in rec["$set"]["messages"] if isinstance(m, dict)])
            else:
                recs.append(rec)
    for rec in recs:
        used = int((rec.get("tokens") or {}).get("input") or 0)
        if rec.get("type") == "gemini" and used:
            model = str(rec.get("model") or "")
            return used, _known_window(model), model, ""
    return None


def _pi_settings() -> dict:
    home = os.environ.get("PI_CODING_AGENT_DIR") or str(Path.home() / ".pi" / "agent")
    return _json(str(Path(home) / "settings.json"))


def _pi_compact_at(window: int) -> int:
    """pi compacts once the context passes the window less `compaction.reserveTokens` (16384)."""
    c = _pi_settings().get("compaction") or {}
    if c.get("enabled") is False:
        return 0
    try:
        return max(0, window - int(c.get("reserveTokens", 16_384)))
    except (TypeError, ValueError):
        return 0


def _qwen_compact_at(window: int) -> int:
    """Qwen's auto-compaction ladder: 85% of the window, but never past window - 33K (the summary's
    output reserve plus a buffer)."""
    ceiling = window - 20_000 - 13_000
    return round(min(0.85 * window, ceiling) if ceiling > 0 else 0.85 * window)


def _gemini_compact_at(window: int) -> int:
    """Gemini compresses at `model.compressionThreshold` (0.5 by default) of the window."""
    home = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini"
    try:
        frac = float((_json(str(home / "settings.json")).get("model") or {}).get("compressionThreshold", 0.5))
    except (TypeError, ValueError):
        frac = 0.5
    return round(window * frac) if 0 < frac <= 1 else 0


_COMPACT_AT = {"pi": _pi_compact_at, "qwen": _qwen_compact_at, "gemini": _gemini_compact_at}


# OpenCode and Kilo (a fork, same schema) keep every session in one SQLite database, so the
# file's stamp alone cannot key the reading: (database, session) plus the stamps of the
# database and its WAL (writes land in -wal until a checkpoint, leaving the .db untouched).
SQLITE_CLIS = ("opencode", "kilo")
_SQLITE_MEMO: dict[tuple, tuple] = {}

# Newest assistant messages first: a turn in flight is stored with zero tokens until it ends.
_ASSISTANT_SQL = """
SELECT data FROM message WHERE session_id = ? AND json_extract(data, '$.role') = 'assistant'
ORDER BY time_created DESC, id DESC LIMIT 8
"""


def _stamp(path: str) -> tuple:
    out = []
    for p in (path, path + "-wal"):
        try:
            st = os.stat(p)
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _sqlite_reading(cli: str, path: str, sid: str) -> tuple | None:
    """(tokens in use, model window, model, "") of one session: the newest finished assistant
    message's `tokens` {input, output, reasoning, cache {read, write}} and `modelID`. After a
    compaction the newest is the summary (`summary: true`): its output is the new context."""
    if not path or not sid or not os.path.isfile(path):
        return None
    key, stamp = (path, sid), _stamp(path)
    hit = _SQLITE_MEMO.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    out = None
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            rows = con.execute(_ASSISTANT_SQL, (sid,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:  # locked, or not (yet) this schema
        rows = []
    for (data,) in rows:
        try:
            d = json.loads(data)
        except (TypeError, ValueError):
            continue
        t = d.get("tokens") or {}
        cache = t.get("cache") or {}
        used = int(t.get("output") or 0) if d.get("summary") else \
            int(t.get("input") or 0) + int(cache.get("read") or 0) + int(cache.get("write") or 0)
        if used:
            model = str(d.get("modelID") or "")
            out = used, _known_window(model), model, ""
            break
    _SQLITE_MEMO[key] = (stamp, out)
    return out


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") in ("text", "output_text"))
    return ""


@by_file
def last_answer(cli: str, path: str, limit: int = 8000) -> str:
    """The newest thing the agent said to you (text only, no tool calls)."""
    for ln in reversed(_tail(path)):
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if cli == "claude":
            if rec.get("type") != "assistant" or rec.get("isSidechain"):
                continue
            t = _text((rec.get("message") or {}).get("content")).strip()
        elif cli == "codex":
            p = rec.get("payload") or {}
            if p.get("type") == "task_complete":
                t = str(p.get("last_agent_message") or "").strip()  # the turn's final answer, as the CLI itself recorded it
            elif p.get("type") == "agent_message":
                t = str(p.get("message", "")).strip()  # inter-agent lines carry `content`, not `message`
            elif p.get("type") == "message" and p.get("role") == "assistant":
                t = _text(p.get("content")).strip()
            else:
                continue
        else:
            return ""
        if t:
            return t[-limit:]
    return ""


def _epoch_of(rec: dict) -> float:
    from datetime import datetime
    ts = rec.get("timestamp", "")
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() if ts else 0.0
    except ValueError:
        return 0.0


@by_file
def last_answer_at(cli: str, path: str) -> float:
    """When the agent last said something (epoch seconds), 0 if unknown or the CLI has no parser."""
    if cli not in ("claude", "codex"):
        return 0.0  # any timestamped record would be a wrong answer
    for ln in reversed(_tail(path)):
        if cli == "claude" and '"assistant"' not in ln:
            continue
        if cli == "codex" and "task_complete" not in ln and "agent_message" not in ln:
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        p = rec.get("payload") or {}
        # Codex: the turn ending (sub-agents' inter-agent agent_message lines have no `message`)
        if cli == "codex" and p.get("type") != "task_complete" and not (p.get("type") == "agent_message" and p.get("message")):
            continue
        t = _epoch_of(rec)
        if t:
            return t
    return 0.0


_TURN = re.compile(rb'"type"\s*:\s*"(task_started|task_complete|turn_aborted)"')
TURN_TAIL_MAX = 8_000_000


@by_file
def codex_turn(path: str) -> str:
    """"working" while the newest Codex turn has started and not ended, "idle" once it
    completed or was aborted, "" when the file says nothing. herdr reports "unknown" for Codex
    after a turn, so this is what settles it. Reads the end of the file, widening only while no
    marker is in it (a long turn buries task_started under thousands of events)."""
    try:
        size = os.path.getsize(path)
        n = 64 * 1024
        with open(path, "rb") as f:
            while True:
                f.seek(max(0, size - n))
                found = _TURN.findall(f.read())
                if found:
                    return "working" if found[-1] == b"task_started" else "idle"
                if n >= size:
                    return ""
                if n >= TURN_TAIL_MAX:
                    return "working"  # no end marker in the last 8 MB: a turn is running
                n *= 4
    except OSError:
        return ""


_ROLLOUTS: dict[str, str] = {}
_MISSED: dict[str, float] = {}  # sid -> when a lookup found nothing: a new session's file comes later


def codex_rollout(sid: str) -> str:
    """The rollout file of a Codex session id ('' when there is none (yet))."""
    if not re.fullmatch(r"[0-9A-Za-z-]+", sid or ""):
        return ""
    hit = _ROLLOUTS.get(sid)
    if hit and os.path.exists(hit):
        return hit
    if time.time() - _MISSED.get(sid, 0.0) < 30:  # the glob walks every rollout: not on each poll
        return ""
    root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
    try:
        found = next(root.glob(f"*/*/*/rollout-*-{sid}.jsonl"), None)
    except OSError:
        found = None
    if found:
        _ROLLOUTS[sid] = str(found)
    else:
        _MISSED[sid] = time.time()
    return str(found or "")


def agent_status(a: dict) -> str:
    """A herdr agent record's status, with Codex's "unknown" settled from its rollout."""
    st = a.get("agent_status", "")
    if st == "unknown" and a.get("agent") == "codex":
        sid = (a.get("agent_session") or {}).get("value", "")
        path = codex_rollout(sid)
        return (codex_turn(path) if path else "") or st
    return st
