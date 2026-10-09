"""Token usage per session and project, today and over the last 7 days, from the transcripts.

Claude: every assistant message's `usage` (deduplicated by message id: one message is written
as several records). Sub-agent transcripts count toward their parent session. Codex: the
growth of `total_token_usage` across the `token_count` events (they repeat, so summing each
event's `last_token_usage` counts a turn several times; a total that falls is a reset).
Files are read incrementally: the cache keeps each file's read offset, last Codex total and
per-day sums, so a refresh reads only what was appended.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import settings

DAYS = 7
CACHE_VERSION = 3


@dataclass
class Tally:
    fresh: int = 0       # input not served from cache (+ cache writes)
    cached: int = 0      # input read from cache
    out: int = 0

    @property
    def total(self) -> int:
        return self.fresh + self.cached + self.out

    def add(self, o: "Tally") -> None:
        self.fresh += o.fresh
        self.cached += o.cached
        self.out += o.out


@dataclass
class SessionUsage:
    cli: str
    sid: str
    days: dict[str, Tally] = field(default_factory=dict)

    def period(self, since_day: str) -> Tally:
        t = Tally()
        for d, v in self.days.items():
            if d >= since_day:
                t.add(v)
        return t


def _day(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _cache_path() -> Path:
    return settings.state_dir() / "usage-cache.json"


def _claude_files(cutoff: float) -> list[tuple[Path, str]]:
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    out = []
    if not root.is_dir():
        return out
    for proj in root.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.glob("*.jsonl"):
            if f.stat().st_mtime >= cutoff:
                out.append((f, f.stem))
        for f in proj.glob("*/subagents/*.jsonl"):
            if f.stat().st_mtime >= cutoff:
                out.append((f, f.parent.parent.name))   # counts toward the parent session
        for f in proj.glob("*/subagents/workflows/*/agent-*.jsonl"):   # a workflow's agents: <sid>/subagents/workflows/<run>/
            if f.stat().st_mtime >= cutoff:
                out.append((f, f.parents[3].name))
    return out


def _codex_files(cutoff: float) -> list[tuple[Path, str]]:
    root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
    out = []
    if not root.is_dir():
        return out
    for f in root.glob("*/*/*/rollout-*.jsonl"):
        if f.stat().st_mtime >= cutoff:
            sid = f.stem.split("-", 6)[-1] if f.stem.count("-") >= 6 else f.stem
            out.append((f, sid))
    return out


def _scan(cli: str, path: Path, start: int, seen_ids: set[str], days: dict[str, list[int]], last: list[int]) -> int:
    """Add the usage found after byte `start` to `days`; return the new offset (end of the
    last complete line). `last` is the Codex running total [input, cached, output], updated in place."""
    with path.open("rb") as fh:
        fh.seek(start)
        data = fh.read()
    end = data.rfind(b"\n")
    if end < 0:
        return start
    for raw in data[: end + 1].splitlines():
        if b'"usage"' not in raw and b"token_count" not in raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if cli == "claude":
            msg = rec.get("message") or {}
            u = msg.get("usage") if isinstance(msg, dict) else None
            if rec.get("type") != "assistant" or not u:
                continue
            mid = msg.get("id") or rec.get("uuid", "")
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            fresh = int(u.get("input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0))
            cached = int(u.get("cache_read_input_tokens", 0))
            out = int(u.get("output_tokens", 0))
            day = _day(rec.get("timestamp", ""))
        else:
            p = rec.get("payload") or {}
            if p.get("type") != "token_count":
                continue
            tot = (p.get("info") or {}).get("total_token_usage")
            if not tot:
                continue
            now = [int(tot.get("input_tokens", 0)), int(tot.get("cached_input_tokens", 0)), int(tot.get("output_tokens", 0))]
            # growth since the previous event; a total that fell restarted (compaction, resume): all of it is new
            base = last if now[0] + now[2] >= last[0] + last[2] else [0, 0, 0]
            grew = [max(0, n - b) for n, b in zip(now, base)]
            last[:] = now
            cached = grew[1]
            fresh = max(0, grew[0] - cached)
            out = grew[2]
            day = _day(rec.get("timestamp", ""))
        if not day:
            continue
        d = days.setdefault(day, [0, 0, 0])
        d[0] += fresh
        d[1] += cached
        d[2] += out
    return start + end + 1


def collect(now: float | None = None) -> dict[tuple[str, str], SessionUsage]:
    now = now or time.time()
    cutoff = now - DAYS * 86400
    first_day = (datetime.fromtimestamp(now) - timedelta(days=DAYS - 1)).strftime("%Y-%m-%d")
    try:
        cache = json.loads(_cache_path().read_text(encoding="utf-8"))
        if cache.get("v") != CACHE_VERSION:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    files = cache.get("files", {})
    fresh_files = {}
    result: dict[tuple[str, str], SessionUsage] = {}
    for cli, lister in (("claude", _claude_files), ("codex", _codex_files)):
        for path, sid in lister(cutoff):
            k = str(path)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            ent = files.get(k)
            if not ent or ent["off"] > size:  # new file, or rewritten from scratch
                ent = {"off": 0, "ids": [], "days": {}, "last": [0, 0, 0]}
            if ent["off"] < size:
                ids = set(ent["ids"])
                try:
                    ent["off"] = _scan(cli, path, ent["off"], ids, ent["days"], ent.setdefault("last", [0, 0, 0]))
                except OSError:
                    continue
                ent["ids"] = list(ids)[-4000:]
            ent["days"] = {d: v for d, v in ent["days"].items() if d >= first_day}
            fresh_files[k] = ent
            su = result.setdefault((cli, sid), SessionUsage(cli, sid))
            for d, (f, c, o) in ent["days"].items():
                t = su.days.setdefault(d, Tally())
                t.add(Tally(f, c, o))
    try:
        _cache_path().write_text(json.dumps({"v": CACHE_VERSION, "files": fresh_files}), encoding="utf-8")
    except OSError:
        pass
    return result


def human(n: int) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return str(n)
