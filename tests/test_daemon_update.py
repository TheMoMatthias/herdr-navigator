"""The daemon's update thread: what it reports, and that it holds the daemon back from restarting
while an update is still moving files."""
import threading

from navigator import daemon, updater


def make(monkeypatch, replies):
    d = daemon.Daemon()
    logged, shown = [], []
    d.log = logged.append
    monkeypatch.setattr(daemon.time, "sleep", lambda s: None)          # no waiting in a test
    seen = []

    def auto(startup=False):
        seen.append((startup, d.updating.is_set()))                   # flagged while it runs
        d.stop = len(seen) >= len(replies)
        return replies[len(seen) - 1]
    monkeypatch.setattr(updater, "auto", auto)
    monkeypatch.setattr(daemon.herdr, "run", lambda *a, **k: shown.append(a))
    return d, logged, shown, seen


def test_the_first_look_ignores_the_interval_and_the_update_is_announced(monkeypatch):
    d, logged, shown, seen = make(monkeypatch, ["Updated 1.0 → 1.1 (2 changes)."])
    d._auto_update()
    assert seen == [(True, True)]
    assert not d.updating.is_set()                                     # released afterwards
    assert shown and shown[0][:3] == ("notification", "show", "Navigator updated")
    assert "Reopen the Navigator (F1)" in shown[0][4]
    assert logged == ["update: Updated 1.0 → 1.1 (2 changes)."]


def test_a_failed_update_is_announced_as_failed(monkeypatch):
    d, logged, shown, seen = make(monkeypatch, ["✗ The new version did not start (boom); kept 1.0."])
    d._auto_update()
    assert shown[0][2] == "Navigator update failed" and "Reopen" not in shown[0][4]


def test_nothing_to_say_is_silent(monkeypatch):
    d, logged, shown, seen = make(monkeypatch, [""])
    d._auto_update()
    assert logged == [] and shown == []


def test_a_crash_in_the_updater_never_kills_the_thread(monkeypatch):
    d = daemon.Daemon()
    logged = []
    d.log = logged.append
    monkeypatch.setattr(daemon.time, "sleep", lambda s: None)
    calls = []

    def auto(startup=False):
        calls.append(startup)
        d.stop = len(calls) >= 2
        raise RuntimeError("boom")
    monkeypatch.setattr(updater, "auto", auto)
    d._auto_update()
    assert calls == [True, False] and all("boom" in m for m in logged) and not d.updating.is_set()


def test_the_daemon_is_started_with_an_update_thread():
    import inspect
    src = inspect.getsource(daemon.Daemon.run)
    assert 'target=self._auto_update' in src
    assert "and not self.updating.is_set()" in src                     # no restart on half-moved files
    assert isinstance(daemon.Daemon().updating, threading.Event)
