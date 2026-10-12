"""Account profiles: several logins per CLI, switched with one click, history shared.

A profile is a saved copy of the files a CLI keeps its login in (`[login.<cli>] files`), plus
named keys inside JSON files (`json_keys`, e.g. Claude's account record in ~/.claude.json).
Tokens rotate while you work (a refresh token is replaced on use, the old copy stops working),
so a saved copy must follow the live login: `keep` saves the live files into the profile of the
account that is signed in now (matched by email, a new profile when none matches), before every
switch, sign-in and sign-out, and whenever the CLI rewrites its login (the daemon). Switching then
copies the target profile in. Running sessions keep the old login until they are relaunched,
which the Navigator does right after a switch or a sign-in.

Profiles live in <state dir>/profiles/<cli>/<name>/ and never leave this machine.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from . import jsonfile, settings

SLOT_KEYS = "json-keys.json"


def _root(cli: str) -> Path:
    return settings.state_dir() / "profiles" / cli


def _meta_path() -> Path:
    return settings.state_dir() / "profiles" / "profiles.json"


def _meta() -> dict:
    try:
        return jsonfile.read(_meta_path(), {})
    except (OSError, ValueError):
        return {}


def _save_meta(m: dict) -> None:
    _meta_path().parent.mkdir(parents=True, exist_ok=True)
    jsonfile.write(_meta_path(), m, indent=1)


def _cfg(cli: str) -> dict:
    return settings.load().login.get(cli, {})


def files(cli: str) -> list[Path]:
    return [settings.user_path(f) for f in _cfg(cli).get("files", [])]


def json_keys(cli: str) -> dict[Path, list[str]]:
    return {settings.user_path(f): list(keys) for f, keys in (_cfg(cli).get("json_keys") or {}).items()}


def supported(cli: str) -> bool:
    return bool(files(cli))


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-")[:40]


def names(cli: str) -> list[str]:
    r = _root(cli)
    return sorted(p.name for p in r.iterdir() if p.is_dir()) if r.exists() else []


def active(cli: str) -> str:
    return _meta().get(cli, {}).get("active", "")


def label(cli: str, name: str) -> str:
    return _meta().get(cli, {}).get("labels", {}).get(name, "")


def _read_json(p: Path) -> dict:
    try:  # plain read: these are the CLIs' own login files, never copied aside
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _capture(cli: str, slot: Path) -> None:
    slot.mkdir(parents=True, exist_ok=True)
    for f in files(cli):
        if f.exists():
            shutil.copy2(f, slot / f.name)
    keys = {str(p): {k: _read_json(p).get(k) for k in ks if k in _read_json(p)} for p, ks in json_keys(cli).items()}
    jsonfile.write((slot / SLOT_KEYS), keys)


def _replace(tmp: Path, dst: Path) -> None:
    """os.replace fails on Windows while another process has `dst` open: retry, then write in place."""
    for _ in range(10):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            time.sleep(0.2)
    dst.write_bytes(tmp.read_bytes())
    tmp.unlink(missing_ok=True)


def _apply(cli: str, slot: Path) -> None:
    for f in files(cli):
        src = slot / f.name
        if src.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f.name + ".nav-tmp")
            shutil.copy2(src, tmp)
            _replace(tmp, f)
    saved = _read_json(slot / SLOT_KEYS)
    for p, ks in json_keys(cli).items():
        vals = saved.get(str(p), {})
        if not vals or not p.exists():
            continue
        data = _read_json(p)
        if not data:
            continue  # never rewrite a file we could not parse
        for k in ks:
            if k in vals:
                data[k] = vals[k]
        tmp = p.with_name(p.name + ".nav-tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _replace(tmp, p)


def email(who: str) -> str:
    """The account in a status line ("a@b.de · max" -> "a@b.de"), "" when it names none."""
    first = who.split(" · ")[0].strip().lower()
    return first if "@" in first else ""


def find(cli: str, who: str) -> str:
    """The profile saved for the account in `who` (by email), else ""."""
    want = email(who)
    return next((n for n in names(cli) if want and email(label(cli, n)) == want), "") if want else ""


def login_at(cli: str) -> float:
    """When the signed-in account last changed (a switch or a sign-in): sessions started before
    run on the previous account."""
    return float(_meta().get(cli, {}).get("login_at", 0) or 0)


def mark_login(cli: str, who: str) -> None:
    m = _meta()
    c = m.setdefault(cli, {})
    c["login_at"], c["email"] = time.time(), email(who)
    _save_meta(m)


def set_email(cli: str, mail: str) -> None:
    m = _meta()
    m.setdefault(cli, {})["email"] = mail
    _save_meta(m)


def last_email(cli: str) -> str:
    return _meta().get(cli, {}).get("email", "")


def keep(cli: str, who: str) -> str:
    """Save the live login into the profile of the account signed in now (`who`, its status line):
    the matching profile, or a new one named after the email. Returns the profile name, "" when
    nothing was saved (not signed in, no account to tell it by, or nothing to keep)."""
    if not supported(cli) or not any(f.exists() for f in files(cli)) or who.startswith("not "):
        return ""
    name = find(cli, who) or slug(email(who))
    if not name:
        return ""
    _capture(cli, _root(cli) / name)
    m = _meta()
    c = m.setdefault(cli, {})
    c["active"] = name
    c.setdefault("labels", {})[name] = who
    _save_meta(m)
    return name


def usable(cli: str, name: str, now: float | None = None) -> bool:
    """False when the saved login says its refresh token has expired (Claude records when);
    a CLI that records nothing counts as usable."""
    now = time.time() if now is None else now
    for f in (_root(cli) / slug(name)).glob("*.json"):
        d = _read_json(f)
        for v in d.values() if isinstance(d, dict) else ():
            exp = v.get("refreshTokenExpiresAt") if isinstance(v, dict) else None
            if isinstance(exp, (int, float)) and exp / 1000 < now:
                return False
    return True


def save_as(cli: str, name: str, who: str = "") -> str:
    """Save the current login of `cli` as profile `name` (and make it the active one)."""
    n = slug(name)
    if not n:
        return "✗ give the profile a name"
    if not supported(cli):
        return f"✗ no login files configured for {cli} ([login.{cli}] files)"
    if not any(f.exists() for f in files(cli)):
        return f"✗ {cli} is not signed in: nothing to save"
    _capture(cli, _root(cli) / n)
    m = _meta()
    c = m.setdefault(cli, {})
    c["active"] = n
    if who:
        c.setdefault("labels", {})[n] = who
    _save_meta(m)
    return f"💾 {cli}: saved the current login as '{n}'"


def switch(cli: str, name: str, who: str = "") -> str:
    """Copy profile `name` in. `who` is the status line of the login live now: it is kept first,
    into its own account's profile (never into whichever profile was marked active)."""
    n = slug(name)
    slot = _root(cli) / n
    if not slot.is_dir():
        return f"✗ no profile '{n}' for {cli}"
    live = find(cli, who) if who else active(cli)
    if who:
        live = keep(cli, who) or live
    elif live and (_root(cli) / live).is_dir():
        _capture(cli, _root(cli) / live)
    if live == n:
        return f"{cli}: '{n}' is already active"
    _apply(cli, slot)
    m = _meta()
    c = m.setdefault(cli, {})
    c["active"], c["switched_at"] = n, time.time()
    c["login_at"], c["email"] = time.time(), email(label(cli, n))
    _save_meta(m)
    return f"⇄ {cli}: switched to '{n}'"


def delete(cli: str, name: str) -> str:
    n = slug(name)
    slot = _root(cli) / n
    if not slot.is_dir():
        return f"✗ no profile '{n}'"
    shutil.rmtree(slot)
    m = _meta()
    if m.get(cli, {}).get("active") == n:
        m[cli]["active"] = ""
    m.get(cli, {}).get("labels", {}).pop(n, None)
    _save_meta(m)
    return f"🗑 {cli}: profile '{n}' deleted (the live login is unchanged)"
