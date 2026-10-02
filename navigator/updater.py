"""Keep the plugin up to date from its git checkout (Navigator › ⚙ Settings › Updates).

`check()` fetches and says how far behind (and ahead) the checkout is and what is new;
`update()` fast-forwards to the latest version, never over local changes or local commits,
and re-runs the bootstrap when the dependencies changed.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class State:
    ok: bool                       # git works here and the checkout has an upstream
    why: str = ""                  # why not, or what went wrong
    version: str = ""              # installed version
    latest: str = ""               # version on the upstream branch
    behind: int = 0
    ahead: int = 0
    dirty: list[str] = field(default_factory=list)  # tracked files changed locally
    new: list[str] = field(default_factory=list)    # one line per new upstream commit


def _git(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=_NO_WINDOW)


def _version(toml_text: str) -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', toml_text, re.M)
    return m.group(1) if m else "?"


def installed_version() -> str:
    try:
        return _version((ROOT / "herdr-plugin.toml").read_text(encoding="utf-8"))
    except OSError:
        return "?"


def check(fetch: bool = True) -> State:
    version = installed_version()
    if not shutil.which("git"):
        return State(False, "git is not installed, so the Navigator can't update itself", version)
    if not (ROOT / ".git").exists():
        return State(False, "installed without git: update it with `herdr plugin update`", version)
    if fetch:
        try:
            f = _git("fetch", "--quiet", timeout=45)
        except subprocess.TimeoutExpired:
            return State(False, "GitHub did not answer in time; try again", version)
        if f.returncode != 0:
            return State(False, "could not reach GitHub: " + (f.stderr.strip().splitlines() or ["?"])[-1][:120],
                         version)
    up = _git("rev-parse", "--abbrev-ref", "@{u}")
    if up.returncode != 0:
        return State(False, "this checkout has no upstream branch to update from", version)
    counts = _git("rev-list", "--left-right", "--count", "HEAD...@{u}").stdout.split()
    ahead, behind = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (0, 0)
    dirty = [ln[3:] for ln in _git("status", "--porcelain", "--untracked-files=no").stdout.splitlines() if ln.strip()]
    new = _git("log", "--format=%h %s", "HEAD..@{u}").stdout.splitlines()[:20]
    latest = _version(_git("show", "@{u}:herdr-plugin.toml").stdout) if behind else version
    return State(True, "", version, latest, behind, ahead, dirty, new)


def update() -> str:
    """Fast-forward to the latest version. Returns a message; ✗ when nothing was changed."""
    st = check()
    if not st.ok:
        return "✗ " + st.why
    if not st.behind:
        return f"Already up to date ({st.version})."
    if st.dirty:
        return "✗ Not updated: these files have local changes: " + ", ".join(st.dirty[:5])
    if st.ahead:
        return f"✗ Not updated: this checkout has {st.ahead} commit(s) of its own; merge them by hand."
    before = _git("rev-parse", "HEAD").stdout.strip()
    pull = _git("merge", "--ff-only", "@{u}", timeout=60)
    if pull.returncode != 0:
        return "✗ Update failed: " + (pull.stderr.strip().splitlines() or ["?"])[-1][:160]
    changed = _git("diff", "--name-only", before, "HEAD").stdout.split()
    note = ""
    if any(f in changed for f in ("bootstrap.py", "requirements.txt", "pyproject.toml")):
        boot = subprocess.run([shutil.which("python") or shutil.which("python3") or sys.executable, "bootstrap.py"],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=600, creationflags=_NO_WINDOW)
        note = "; dependencies refreshed" if boot.returncode == 0 else "; ✗ dependency refresh failed: run bootstrap.py"
    return f"Updated {st.version} → {installed_version()} ({st.behind} change{'s' * (st.behind != 1)}){note}."


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        print(update())
    else:
        s = check()
        print(s.why if not s.ok else f"{s.version}: {s.behind} behind, {s.ahead} ahead" + "".join(
            "\n  " + c for c in s.new))
