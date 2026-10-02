"""Finishing a worktree: is it safe to put away, then close its workspace and remove the checkout.

Never forces anything. Uncommitted changes or a running agent block the removal, and the
branch itself is always kept (merging and deleting it stay your call).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

from . import herdr

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


@dataclass
class Status:
    path: str
    branch: str = ""
    base: str = ""
    dirty: list[str] = field(default_factory=list)
    ahead: int = 0          # commits on the branch that the base does not have
    behind: int = 0
    agents: list[str] = field(default_factory=list)
    workspaces: list[str] = field(default_factory=list)

    @property
    def removable(self) -> bool:
        return not self.dirty and not self.agents and os.path.isdir(self.path)

    @property
    def blockers(self) -> list[str]:
        out = []
        if self.agents:
            out.append(f"{len(self.agents)} agent(s) still running: {', '.join(self.agents)[:80]}")
        if self.dirty:
            out.append(f"{len(self.dirty)} uncommitted change(s): commit or discard them first")
        return out


def status(wt_path: str, repo_path: str, agents: list[str], workspaces: list[str]) -> Status:
    st = Status(wt_path, agents=agents, workspaces=workspaces)
    if not os.path.isdir(wt_path):
        return st
    st.branch = _git(wt_path, "rev-parse", "--abbrev-ref", "HEAD")
    st.dirty = [ln for ln in _git(wt_path, "status", "--porcelain").splitlines() if ln.strip()]
    st.base = _git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    if st.base and st.branch and st.base != st.branch:
        counts = _git(wt_path, "rev-list", "--left-right", "--count", f"{st.base}...HEAD").split()
        if len(counts) == 2:
            st.behind, st.ahead = int(counts[0]), int(counts[1])
    return st


def close_workspaces(st: Status) -> list[str]:
    msgs = []
    for ws in st.workspaces:
        try:
            herdr.run("workspace", "close", ws)
            msgs.append(f"closed workspace {ws}")
        except herdr.HerdrError as e:
            msgs.append(f"✗ {ws}: {str(e)[:100]}")
    return msgs


def remove(st: Status, repo_path: str) -> str:
    """Remove the checkout (the branch stays). Refuses unless status says it is safe."""
    if not st.removable:
        return "✗ not removed: " + "; ".join(st.blockers or ["the folder is gone"])
    msgs = close_workspaces(st)
    try:
        r = subprocess.run(["git", "-C", repo_path, "worktree", "remove", st.path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        return f"✗ git worktree remove failed: {e}"
    if r.returncode != 0:
        return f"✗ git refused: {(r.stderr or r.stdout).strip()[:160]}"
    return "⎇ removed the worktree checkout" + (f" (branch {st.branch} kept)" if st.branch else "") + \
        (f"; {'; '.join(msgs)}" if msgs else "")
