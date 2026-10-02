"""Phone alerts: an agent waiting on you too long, and the logon restore's result.

Channels (any number at once, all off until configured in `[alerts]` or Navigator › 🔔 Alerts):
  Telegram   your own bot (@BotFather) -> telegram_bot_token + telegram_chat_id
  ntfy       ntfy_topic (+ ntfy_server), the free ntfy app, no account
  webhook    webhook_url: Discord, Slack, Mattermost, Teams, Google Chat or anything taking JSON
  WhatsApp   whatsapp_phone + whatsapp_apikey through CallMeBot (free, unofficial relay)
Messages carry only a session's name, its project and how long it waited.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import settings

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
TIMEOUT = 8


def _cfg() -> dict:
    return settings.load().alerts


def _val(key: str) -> str:
    return str(_cfg().get(key, "") or "").strip()


def channels() -> dict[str, bool]:
    """Which channels are set up."""
    return {"telegram": bool(_val("telegram_bot_token") and _val("telegram_chat_id")),
            "ntfy": bool(_val("ntfy_topic")),
            "webhook": bool(_val("webhook_url")),
            "whatsapp": bool(_val("whatsapp_phone") and _val("whatsapp_apikey"))}


def enabled() -> bool:
    return any(channels().values())


def _post(url: str, data: bytes, headers: dict) -> bool:
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return 200 <= r.status < 300
    except OSError:
        return False


def _get(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return 200 <= r.status < 300
    except OSError:
        return False


def _telegram_api() -> str:
    return (_val("telegram_api") or "https://api.telegram.org").rstrip("/")


def _send_telegram(title: str, body: str) -> bool:
    url = f"{_telegram_api()}/bot{_val('telegram_bot_token')}/sendMessage"
    payload = {"chat_id": _val("telegram_chat_id"), "text": f"{title}\n{body}", "disable_web_page_preview": True}
    return _post(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def _send_ntfy(title: str, body: str, tags: str) -> bool:
    url = (_val("ntfy_server") or "https://ntfy.sh").rstrip("/") + "/" + _val("ntfy_topic")
    return _post(url, body.encode("utf-8"), {"Title": title.encode("utf-8").decode("latin-1", "replace"),
                                              "Tags": tags})


def _send_webhook(title: str, body: str) -> bool:
    url = _val("webhook_url")
    text = f"**{title}**\n{body}" if "discord" in url else f"{title}\n{body}"
    payload = {"content": text} if "discord" in url else {"text": text}
    return _post(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def _send_whatsapp(title: str, body: str) -> bool:
    base = _val("whatsapp_api") or "https://api.callmebot.com/whatsapp.php"
    q = urllib.parse.urlencode({"phone": _val("whatsapp_phone"), "text": f"{title}\n{body}",
                                "apikey": _val("whatsapp_apikey")})
    return _get(f"{base}?{q}")


def send(title: str, body: str, tags: str = "robot") -> dict[str, bool]:
    """Send to every configured channel. Returns {channel: delivered}."""
    on = channels()
    out = {}
    if on["telegram"]:
        out["telegram"] = _send_telegram(title, body)
    if on["ntfy"]:
        out["ntfy"] = _send_ntfy(title, body, tags)
    if on["webhook"]:
        out["webhook"] = _send_webhook(title, body)
    if on["whatsapp"]:
        out["whatsapp"] = _send_whatsapp(title, body)
    return out


def send_background(title: str, body: str, tags: str = "robot") -> None:
    """Fire and forget, so a 3-second status line never waits on the network."""
    root = Path(__file__).resolve().parent.parent
    py = root / ".venv" / ("Scripts/pythonw.exe" if os.name == "nt" else "bin/python")
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONUTF8": "1"}
    subprocess.Popen([str(py), "-m", "navigator.alerts", "send", title, body, tags], cwd=str(root), env=env,
                     creationflags=_DETACHED | _NO_WINDOW, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---- setup helpers ------------------------------------------------------------------------------

def telegram_find_chat(token: str) -> tuple[str, str]:
    """After you sent your bot any message: (chat id, who) from its latest update, or ("", why)."""
    url = f"{_telegram_api()}/bot{token.strip()}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except OSError as e:
        return "", f"Telegram did not answer ({str(e)[:80]}): check the token"
    if not data.get("ok"):
        return "", "Telegram rejected the token"
    for upd in reversed(data.get("result", [])):
        chat = (upd.get("message") or upd.get("channel_post") or {}).get("chat") or {}
        if chat.get("id") is not None:
            who = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
            return str(chat["id"]), who
    return "", "no message yet: open your bot in Telegram, press Start (or send it anything), then try again"


def random_topic() -> str:
    return "herdr-" + secrets.token_urlsafe(12).replace("_", "").replace("-", "")[:16].lower()


def save(values: dict) -> None:
    """Write keys into [alerts] of navigator.toml, keeping everything else as it is."""
    import tomlkit
    path = settings.settings_path()
    settings.load()  # makes sure the file exists
    doc = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()
    tab = doc.get("alerts")
    if tab is None:
        tab = tomlkit.table()
        doc["alerts"] = tab
    for k, v in values.items():
        tab[k] = v
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    settings.load.cache_clear()


# ---- the waiting-agent check (runs with the status line) ------------------------------------------

def _state_path() -> Path:
    return settings.state_dir() / "alerts-state.json"


def check_waiting(snap: dict, now: float | None = None, sender=send_background) -> list[str]:
    """Called with each status-line snapshot: alert once per wait that passes the limit."""
    if not enabled():
        return []
    limit = float(_cfg().get("blocked_minutes", 10)) * 60
    if limit <= 0:
        return []
    now = now or time.time()
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    labels = {w["workspace_id"]: w.get("label", "") for w in snap.get("workspaces", [])}
    waiting = {a["pane_id"]: a for a in snap.get("agents", []) if a.get("agent_status") == "blocked"}
    sent = []
    new_state = {}
    for pane, a in waiting.items():
        st = dict(state.get(pane) or {"since": now, "alerted": False})
        if not st["alerted"] and now - st["since"] >= limit:
            name = a.get("terminal_title_stripped") or a.get("agent") or pane
            mins = int((now - st["since"]) // 60)
            title = f"{name[:40]} waits for you"
            body = f"{labels.get(a.get('workspace_id'), '')}: waiting {mins} min"
            sender(title, body, "warning")
            st["alerted"] = True
            sent.append(title)
        new_state[pane] = st
    if new_state != state:
        _state_path().write_text(json.dumps(new_state), encoding="utf-8")
    return sent


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "send":
        send(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "robot")
    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        res = send("herdr navigator", "Test alert: alerts reach you here.", "white_check_mark")
        print(res or "no channel set up: Navigator › Sessions › 🔔 Alerts, or [alerts] in navigator.toml")
