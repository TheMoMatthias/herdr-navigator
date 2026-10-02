"""Saved prompts: the `[prompts]` table in the settings plus the ones saved from the Navigator."""
from __future__ import annotations

import json
from pathlib import Path

from . import settings


def path() -> Path:
    return settings.state_dir() / "prompts.json"


def saved() -> dict[str, str]:
    try:
        return dict(json.loads(path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def all_() -> dict[str, str]:
    return {**settings.load().prompts, **saved()}


def save(name: str, text: str) -> None:
    data = saved()
    data[name.strip()[:40]] = text
    path().write_text(json.dumps(data, indent=1), encoding="utf-8")
