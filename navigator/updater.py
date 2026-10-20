"""Keep the plugin up to date from its git checkout (Navigator › ⚙ Settings › Updates).

`check()` fetches and says how far behind (and ahead) the checkout is and what is new;
`update()` fast-forwards to the latest version, never over local changes or local commits,
re-runs the bootstrap when the dependencies changed, checks that the new code starts (and goes back to
the old version when it does not), and rewrites herdr's key bindings from the new code.
`auto()` is what the resident daemon calls: the same, by itself, but only from the configured GitHub repo.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
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


def upstream() -> str:
    """The ref to update from: the branch's upstream, else (herdr installs a detached commit)
    the remote's default branch."""
    for ref in ("@{u}", "origin/HEAD", "origin/main"):
        if _git("rev-parse", "--verify", "--quiet", ref).returncode == 0:
            return ref
    return ""


def check(fetch: bool = True) -> State:
    version = installed_version()
    if not shutil.which("git"):
        return State(False, "git is not installed, so the Navigator can't update itself", version)
    if not (ROOT / ".git").exists():
        return State(False, "installed without git: update it with "
                            "`herdr plugin install TheMoMatthias/herdr-navigator` (outside herdr)", version)
    if fetch:
        try:
            f = _git("fetch", "--quiet", timeout=45)
        except subprocess.TimeoutExpired:
            return State(False, "GitHub did not answer in time; try again", version)
        if f.returncode != 0:
            return State(False, "could not reach GitHub: " + (f.stderr.strip().splitlines() or ["?"])[-1][:120],
                         version)
    up = upstream()
    if not up:
        return State(False, "this checkout has no upstream branch to update from", version)
    counts = _git("rev-list", "--left-right", "--count", f"HEAD...{up}").stdout.split()
    ahead, behind = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (0, 0)
    dirty = [ln[3:] for ln in _git("status", "--porcelain", "--untracked-files=no").stdout.splitlines() if ln.strip()]
    new = _git("log", "--format=%h %s", f"HEAD..{up}").stdout.splitlines()[:20]
    latest = _version(_git("show", f"{up}:herdr-plugin.toml").stdout) if behind else version
    return State(True, "", version, latest, behind, ahead, dirty, new)


def _python() -> str:
    """The plugin's own interpreter (it has Textual and tomlkit); the daemon runs a bare one."""
    venv = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(venv) if venv.exists() else sys.executable


def _smoke() -> str:
    """'' when the checked-out code starts (its modules import); else why not."""
    code = "import navigator.app, navigator.daemon, navigator.setup, navigator.sync, navigator.settings_ui"
    try:
        r = subprocess.run([_python(), "-c", code], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120, creationflags=_NO_WINDOW,
                           env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"})
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"could not run the start-up check: {e}"
    return "" if r.returncode == 0 else (r.stderr.strip().splitlines() or ["import failed"])[-1][:200]


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
    up = upstream()
    detached = _git("symbolic-ref", "-q", "HEAD").returncode != 0
    # ahead == 0, so moving a detached HEAD to the upstream is a fast-forward too
    pull = _git("checkout", "--quiet", "--detach", up) if detached else _git("merge", "--ff-only", up, timeout=60)
    if pull.returncode != 0:
        return "✗ Update failed: " + (pull.stderr.strip().splitlines() or ["?"])[-1][:160]
    changed = _git("diff", "--name-only", before, "HEAD").stdout.split()
    note = ""
    if any(f in changed for f in ("bootstrap.py", "requirements.txt", "pyproject.toml")):
        boot = subprocess.run([shutil.which("python") or shutil.which("python3") or sys.executable, "bootstrap.py"],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=600, creationflags=_NO_WINDOW)
        note = "; dependencies refreshed" if boot.returncode == 0 else "; ✗ dependency refresh failed: run bootstrap.py"
    broken = _smoke()
    if broken:  # never leave the Navigator on code that does not start: back to what worked
        new = _git("rev-parse", "HEAD").stdout.strip()
        _git("checkout", "--quiet", "--detach", before) if detached else _git("reset", "--quiet", "--hard", before)
        _remember(failed=new)
        return f"✗ The new version did not start ({broken}); kept {st.version}."
    # the new code may bind its keys differently (F1 became a pane, not a popup): rewrite herdr's config with it
    keys = subprocess.run([_python(), "-m", "navigator.setup", "install"], cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", timeout=120, creationflags=_NO_WINDOW)
    note += "" if keys.returncode == 0 else "; ✗ key bindings not refreshed: run `python -m navigator.setup install`"
    return f"Updated {st.version} → {installed_version()} ({st.behind} change{'s' * (st.behind != 1)}){note}."


# ---- by itself (the resident daemon) ---------------------------------------------------------------

def _state_file() -> Path:
    from . import settings
    return settings.state_dir() / "update-state.json"


def last() -> dict:
    """What the automatic updater did last: {checked, result, updated, failed} (empty before the first run)."""
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _remember(**kv) -> None:
    try:
        f = _state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({**last(), **kv}), encoding="utf-8")
    except OSError:
        pass


def remote_ok(repo: str) -> bool:
    """Is `origin` the configured GitHub repo, over https? Code from anywhere else is never installed unasked."""
    url = _git("remote", "get-url", "origin").stdout.strip()
    m = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?", url, re.I)
    return bool(repo and m and f"{m.group(1)}/{m.group(2)}".lower() == repo.lower())


@contextlib.contextmanager
def exclusive():
    """One update at a time (the daemon and the Settings button must not both move the checkout)."""
    lock = _state_file().with_name("update.lock")
    got = False
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            if time.time() - lock.stat().st_mtime > 900:  # left by a crash
                lock.unlink()
        try:
            os.close(os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            got = True
        except OSError:
            pass
        yield got
    finally:
        if got:
            with contextlib.suppress(OSError):
                lock.unlink()


def auto(startup: bool = False) -> str:
    """Install a newer version by itself when it is safe. `startup` ignores the check interval (the
    daemon's first look after a boot). Returns what happened, or '' when there was nothing to say."""
    from . import settings
    s = settings.load()
    if not s.auto_update:
        return ""
    now = time.time()
    if not startup and now - float(last().get("checked", 0)) < s.update_check_hours * 3600:
        return ""
    _remember(checked=now)
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        return ""
    if not remote_ok(s.update_repo):
        _remember(result="skipped: origin is not " + s.update_repo)
        return f"not updating by itself: this checkout's origin is not https://github.com/{s.update_repo}"
    st = check()
    if not st.ok:
        _remember(result=st.why)
        return "check failed: " + st.why
    if not st.behind:
        _remember(result="up to date")
        return ""
    if st.dirty or st.ahead:
        why = "local changes" if st.dirty else f"{st.ahead} local commit(s)"
        _remember(result=f"{st.behind} new, not installed: {why}")
        return f"{st.behind} new change(s) not installed: this checkout has {why}"
    if last().get("failed") == _git("rev-parse", upstream()).stdout.strip():
        return ""  # that very version did not start here: wait for the next one
    with exclusive() as got:
        if not got:
            return ""
        msg = update()
    _remember(result=msg, **({"updated": now} if msg.startswith("Updated") else {}))
    return msg



if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        print(update())
    elif len(sys.argv) > 1 and sys.argv[1] == "auto":
        print(auto(startup=True) or "nothing to do")
    else:
        s = check()
        print(s.why if not s.ok else f"{s.version}: {s.behind} behind, {s.ahead} ahead" + "".join(
            "\n  " + c for c in s.new))
