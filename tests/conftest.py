import pytest

from navigator import herdr


@pytest.fixture(autouse=True)
def never_the_live_herdr(monkeypatch):
    """A test must not reach the herdr it runs under: one that switched Idle sessions on once closed a
    real session. Every unpatched socket request and CLI call fails; a test that needs more patches it."""
    def refuse(*a, **k):
        raise herdr.HerdrError("tests never reach the live herdr")
    monkeypatch.setattr(herdr, "request", refuse)
    monkeypatch.setenv("HERDR_BIN_PATH", "nav-tests-no-such-herdr.exe")
