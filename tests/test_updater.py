"""The in-app update also rewrites herdr's key bindings, so a changed binding (F1 became a pane,
not a popup) reaches a machine that updates from the Navigator."""
import subprocess

from navigator import updater


def test_update_rewrites_key_bindings(monkeypatch):
    monkeypatch.setattr(updater, "check", lambda fetch=True: updater.State(True, version="1.0", behind=1))
    monkeypatch.setattr(updater, "upstream", lambda: "origin/main")
    monkeypatch.setattr(updater, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    ran = []
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda cmd, **k: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    msg = updater.update()
    assert ["-m", "navigator.setup", "install"] in [c[1:] for c in ran], ran
    assert "✗" not in msg


def test_update_says_when_the_bindings_were_not_refreshed(monkeypatch):
    monkeypatch.setattr(updater, "check", lambda fetch=True: updater.State(True, version="1.0", behind=1))
    monkeypatch.setattr(updater, "upstream", lambda: "origin/main")
    monkeypatch.setattr(updater, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1 if "navigator.setup" in cmd else 0, "", ""))
    assert "key bindings not refreshed" in updater.update()
