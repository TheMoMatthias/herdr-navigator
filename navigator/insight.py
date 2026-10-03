"""What a session's transcript says right now: how full its context is, and its last answer.

Claude: the newest main-chain assistant message's usage (input + cache read + cache write) is
the context in use; the window comes from `[context]` in the settings (by model name), and
grows to 1M when a session is seen using more than the configured window.
Codex: `token_count` events carry the last turn's usage and the model's context window.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from . import settings

TAIL = 600_000


@dataclass
class Context:
    used: int
    window: int

    @property
    def pct(self) -> int:
        return min(100, round(100 * self.used / self.window)) if self.window else 0


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
        if key != "default" and key.lower() in model.lower() and len(key) > best_len:
            best, best_len = int(val), len(key)
    if "[1m]" in model.lower():
        best = max(best, 1_000_000)
    return best


def context(cli: str, path: str) -> Context | None:
    if not path:
        return None
    lines = _tail(path)
    if cli == "claude":
        for ln in reversed(lines):
            if '"usage"' not in ln or '"assistant"' not in ln:
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            if rec.get("isSidechain") or rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            u = msg.get("usage") or {}
            used = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) \
                + int(u.get("cache_creation_input_tokens", 0))
            if not used:
                continue
            window = _window_for(str(msg.get("model", "")))
            if used > window:
                window = max(window, 1_000_000)
            return Context(used, window)
        return None
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
                return Context(used, window)
        return None
    return None


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") in ("text", "output_text"))
    return ""


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
            if p.get("type") == "agent_message":
                t = str(p.get("message", "")).strip()
            elif p.get("type") == "message" and p.get("role") == "assistant":
                t = _text(p.get("content")).strip()
            else:
                continue
        else:
            return ""
        if t:
            return t[-limit:]
    return ""


def last_answer_at(cli: str, path: str) -> float:
    """When the agent last said something (epoch seconds), 0 if unknown."""
    from datetime import datetime
    for ln in reversed(_tail(path)):
        if cli == "claude" and '"assistant"' not in ln:
            continue
        if cli == "codex" and "agent_message" not in ln and '"assistant"' not in ln:
            continue
        try:
            rec = json.loads(ln)
            ts = rec.get("timestamp", "")
            if ts:
                return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return 0.0
