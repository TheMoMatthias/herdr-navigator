"""Map any working directory to the project it belongs to.

A project is the main git checkout. A linked worktree (`.git` file -> gitdir) folds into its
main repository and keeps its own name as the `worktree` label. Directories without git fall
back to the first configured project root that contains them, then to the directory itself.
Paths compare case-insensitively on Windows and case-sensitively elsewhere.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

from . import settings

WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class Project:
    root: str           # comparison key of the main checkout (the grouping key)
    name: str           # display name
    worktree: str = ""  # linked worktree / sub-checkout label, "" for the main checkout
    path: str = field(default="", compare=False)      # main checkout path, original case
    wt_path: str = field(default="", compare=False)   # the worktree checkout path, if any

    @property
    def label(self) -> str:
        return f"{self.name} ⎇ {self.worktree}" if self.worktree else self.name


def norm(path: str) -> str:
    if not path:
        return ""
    p = os.path.normpath(os.path.expanduser(path.strip().strip('"')))
    return p.rstrip("\\/") if len(p) > 3 else p


def key(path: str) -> str:
    p = norm(path)
    return p.lower() if WINDOWS else p


def slashed(path: str) -> str:
    return key(path).replace("\\", "/")


def is_hidden(cwd: str) -> bool:
    k = slashed(cwd).lower() + "/"
    return any(re.search(pat, k, re.I) for pat in settings.load().hidden_patterns)


# Worktree folder conventions (Claude Code, generic); checkouts may already be deleted.
_WORKTREE_MARKERS = [(".claude", "worktrees"), (".millwright-worktrees",), ("worktrees",)]


def _parts(p: str) -> list[str]:
    return [x for x in re.split(r"[\\/]+", p) if x]


def _main_repo_from_gitfile(gitfile: str) -> str | None:
    try:
        with open(gitfile, encoding="utf-8", errors="replace") as fh:
            line = fh.readline().strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = norm(line.split(":", 1)[1].strip())
    # <main>/.git/worktrees/<name>  ->  <main>
    m = re.match(r"^(.*)[\\/]\.git[\\/]worktrees[\\/][^\\/]+$", gitdir)
    return norm(m.group(1)) if m else None


def _join(base: str, parts: list[str]) -> str:
    return os.path.join(base, *parts) if parts else base


def _worktree_split(cwd: str, main: str) -> tuple[str, str]:
    """(label, checkout path) when cwd sits inside a worktree folder below main."""
    rest = _parts(cwd[len(main):])
    low = [x.lower() for x in rest]
    for marker in _WORKTREE_MARKERS:
        n = len(marker)
        if len(rest) > n and tuple(low[:n]) == marker:
            return rest[n], _join(main, rest[: n + 1])
    return "", ""


@lru_cache(maxsize=4096)
def _resolve(cwd: str) -> Project:
    cfg = settings.load()
    k = key(cwd)
    # 1. pinned roots win (longest first)
    for root, name in sorted(cfg.projects.items(), key=lambda kv: -len(kv[0])):
        rk = key(root)
        if k == rk or k.startswith(rk + os.sep):
            wt, wt_path = _worktree_split(cwd, norm(root))
            return Project(rk, name or os.path.basename(norm(root)), wt, norm(root), wt_path)

    # 2. walk up existing ancestors looking for .git
    cur = cwd
    while cur and not os.path.isdir(cur) and os.path.dirname(cur) != cur:
        cur = os.path.dirname(cur)
    while cur:
        dotgit = os.path.join(cur, ".git")
        if os.path.isdir(dotgit):
            wt, wt_path = _worktree_split(cwd, cur)
            return Project(key(cur), _display(cur), wt, cur, wt_path)
        if os.path.isfile(dotgit):
            main = _main_repo_from_gitfile(dotgit)
            if main:
                return Project(key(main), _display(main), os.path.basename(cur), main, cur)
            return Project(key(cur), _display(cur), "", cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    # 3. deleted worktree checkouts: fold by path convention
    parts = _parts(cwd)
    low = [x.lower() for x in parts]
    for marker in _WORKTREE_MARKERS:
        n = len(marker)
        for i in range(1, len(parts) - n):
            if tuple(low[i:i + n]) == marker:
                sep_root = cwd[: len(cwd) - len(os.path.join(*parts[i:]))].rstrip("\\/") or cwd[:1]
                main = norm(sep_root)
                return Project(key(main), _display(main), parts[i + n], main, _join(main, parts[i:i + n + 1]))
    return Project(k, _display(cwd), "", cwd)


def _display(path: str) -> str:
    p = norm(path)
    if key(p) == key(os.path.expanduser("~")):
        return "~ home"
    return os.path.basename(p) or p


def resolve(cwd: str) -> Project:
    return _resolve(norm(cwd))


def clear_cache() -> None:
    _resolve.cache_clear()
