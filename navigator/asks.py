"""Is an agent waiting on a question to you? Read from the end of its transcript.

Claude: the last AskUserQuestion tool call that has no tool result yet. Codex: the last
request_user_input call without an output. herdr's own state (blocked) covers permission prompts;
this adds the question text and its options, also for sessions running outside herdr.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

TAIL = 400_000


@dataclass
class Question:
    text: str
    options: list[str] = field(default_factory=list)
    multi: bool = False
    more: int = 0          # further questions in the same call


def _tail_lines(path: str) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if size > TAIL else lines


def _claude(lines: list[str]) -> Question | None:
    asked: dict[str, dict] = {}
    order: list[str] = []
    answered: set[str] = set()
    for ln in lines:
        if "AskUserQuestion" not in ln and "tool_result" not in ln:
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                asked[b.get("id", "")] = b.get("input") or {}
                order.append(b.get("id", ""))
            elif b.get("type") == "tool_result":
                answered.add(b.get("tool_use_id", ""))
    for qid in reversed(order):
        if qid in answered:
            return None  # the newest question was answered: nothing pending
        qs = asked[qid].get("questions") or []
        if not qs:
            return None
        q = qs[0]
        opts = [o.get("label", "") if isinstance(o, dict) else str(o) for o in q.get("options") or []]
        return Question(q.get("question", "").strip(), opts, bool(q.get("multiSelect")), len(qs) - 1)
    return None


def _codex(lines: list[str]) -> Question | None:
    pending: dict[str, dict] = {}
    for ln in lines:
        if "request_user_input" not in ln and "function_call_output" not in ln:
            continue
        try:
            p = json.loads(ln).get("payload") or {}
        except ValueError:
            continue
        if p.get("type") == "function_call" and p.get("name") == "request_user_input":
            try:
                args = json.loads(p.get("arguments") or "{}")
            except ValueError:
                args = {}
            pending[p.get("call_id", "")] = args
        elif p.get("type") == "function_call_output":
            pending.pop(p.get("call_id", ""), None)
    if not pending:
        return None
    args = list(pending.values())[-1]
    qs = args.get("questions") or [args]
    q = qs[0] if isinstance(qs[0], dict) else {}
    opts = [o.get("label", "") if isinstance(o, dict) else str(o) for o in q.get("options") or []]
    return Question(str(q.get("question") or q.get("prompt") or "").strip(), opts, False, len(qs) - 1)


def pending(cli: str, transcript: str) -> Question | None:
    if not transcript:
        return None
    lines = _tail_lines(transcript)
    if cli == "claude":
        return _claude(lines)
    if cli == "codex":
        return _codex(lines)
    return None
