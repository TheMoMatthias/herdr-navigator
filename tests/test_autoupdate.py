"""The automatic update (the daemon's `updater.auto`), exercised against a real git origin and clone:
what it installs, what it never touches, and how it goes back when a version does not start."""
import os
import subprocess
import sys
import time

import pytest

from navigator import settings, updater


def _git(cwd, *a):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


class World:
    def __init__(self, origin, work):
        self.origin, self.work = origin, work

    def publish(self, version="1.1"):
        (self.origin / "herdr-plugin.toml").write_text(f'version = "{version}"\n')
        _git(self.origin, "commit", "-qam", f"to {version}")

    def head(self):
        return _git(self.work, "rev-parse", "HEAD")

    def origin_head(self):
        return _git(self.origin, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """An origin with one commit, and a detached clone of it the way herdr installs a plugin."""
    monkeypatch.setenv("HERDR_PLUGIN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "cfg").mkdir()
    settings.load.cache_clear()
    origin, work = tmp_path / "origin", tmp_path / "work"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    (origin / "herdr-plugin.toml").write_text('version = "1.0"\n')
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", "one")
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "checkout", "-q", "--detach")
    real = subprocess.run
    monkeypatch.setattr(updater, "ROOT", work)
    monkeypatch.setattr(updater, "_python", lambda: sys.executable)
    monkeypatch.setattr(updater, "remote_ok", lambda repo: True)
    monkeypatch.setattr(updater, "_smoke", lambda: "")
    # the key-binding rewrite must never touch the real herdr config from a test
    monkeypatch.setattr(updater.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", "")
                        if "navigator.setup" in cmd else real(cmd, **k))
    yield World(origin, work)
    settings.load.cache_clear()


def test_auto_installs_a_new_version_on_a_clean_checkout(world):
    world.publish()
    msg = updater.auto(startup=True)
    assert msg.startswith("Updated 1.0 → 1.1"), msg
    assert world.head() == world.origin_head()
    assert updater.last()["result"] == msg and "updated" in updater.last()


def test_auto_leaves_an_up_to_date_checkout_alone(world):
    assert updater.auto(startup=True) == ""
    assert updater.last()["result"] == "up to date"


def test_auto_respects_the_check_interval(world):
    updater.auto(startup=True)
    world.publish()
    assert updater.auto() == ""                      # checked a moment ago
    assert world.head() != world.origin_head()
    updater._remember(checked=time.time() - 7 * 3600)
    assert updater.auto().startswith("Updated")      # the interval is over


def test_auto_can_be_switched_off(world, tmp_path):
    (tmp_path / "cfg" / "navigator.toml").write_text("[updates]\nauto = false\n", encoding="utf-8")
    settings.load.cache_clear()
    world.publish()
    assert updater.auto(startup=True) == ""
    assert world.head() != world.origin_head()


def test_auto_never_touches_local_changes(world):
    world.publish()
    (world.work / "herdr-plugin.toml").write_text('version = "mine"\n')
    msg = updater.auto(startup=True)
    assert "not installed" in msg and "local changes" in msg
    assert (world.work / "herdr-plugin.toml").read_text() == 'version = "mine"\n'


def test_auto_never_overwrites_local_commits(world):
    (world.work / "extra.txt").write_text("x")
    _git(world.work, "add", ".")
    _git(world.work, "commit", "-qm", "mine")
    mine = world.head()
    world.publish()
    assert "local commit" in updater.auto(startup=True)
    assert world.head() == mine


def test_auto_refuses_a_foreign_origin(world, monkeypatch):
    monkeypatch.setattr(updater, "remote_ok", lambda repo: False)
    world.publish()
    assert "origin is not" in updater.auto(startup=True)
    assert world.head() != world.origin_head()


def test_a_version_that_does_not_start_is_rolled_back_and_not_retried(world, monkeypatch):
    before = world.head()
    world.publish()
    monkeypatch.setattr(updater, "_smoke", lambda: "SyntaxError: boom")
    msg = updater.auto(startup=True)
    assert msg.startswith("✗ The new version did not start (SyntaxError: boom); kept 1.0"), msg
    assert world.head() == before
    assert updater.auto(startup=True) == ""          # that same version is not tried again
    assert world.head() == before
    world.publish("1.2")                              # a newer one is
    monkeypatch.setattr(updater, "_smoke", lambda: "")
    assert updater.auto(startup=True).startswith("Updated 1.0 → 1.2")


def test_only_one_update_runs_at_a_time(world):
    with updater.exclusive() as first:
        with updater.exclusive() as second:
            assert first and not second
    with updater.exclusive() as again:
        assert again                                  # released after use


def test_a_stale_lock_from_a_crash_does_not_block_forever(world):
    lock = updater._state_file().with_name("update.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    with updater.exclusive() as got:
        assert got


@pytest.mark.parametrize("url,ok", [
    ("https://github.com/TheMoMatthias/herdr-navigator.git", True),
    ("https://github.com/themomatthias/Herdr-Navigator", True),
    ("https://github.com/TheMoMatthias/herdr-navigator/", True),
    ("https://github.com/evil/herdr-navigator.git", False),
    ("https://github.com/TheMoMatthias/herdr-navigator-fork.git", False),
    ("http://github.com/TheMoMatthias/herdr-navigator.git", False),
    ("https://github.com.evil.example/TheMoMatthias/herdr-navigator.git", False),
    ("git@github.com:TheMoMatthias/herdr-navigator.git", False),
    ("/some/local/path", False),
])
def test_only_the_configured_github_repo_is_trusted(url, ok, monkeypatch):
    monkeypatch.setattr(updater, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 0, url + "\n", ""))
    assert updater.remote_ok("TheMoMatthias/herdr-navigator") is ok


def test_an_empty_repo_setting_trusts_nothing(monkeypatch):
    monkeypatch.setattr(updater, "_git", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, "https://github.com/TheMoMatthias/herdr-navigator.git\n", ""))
    assert updater.remote_ok("") is False


def test_the_checked_out_code_really_starts():
    """_smoke on this very checkout: what auto() trusts to say 'does not start'."""
    assert updater._smoke() == ""
