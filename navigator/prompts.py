"""Saved prompts: the `[prompts]` table in the settings plus the ones saved from the Navigator."""
from __future__ import annotations

import json
from pathlib import Path

from . import jsonfile, settings


def path() -> Path:
    return settings.state_dir() / "prompts.json"


def saved() -> dict[str, str]:
    try:
        return dict(jsonfile.read(path(), {}))
    except (OSError, ValueError):
        return {}


def all_() -> dict[str, str]:
    return {**settings.load().prompts, **saved()}


def save(name: str, text: str) -> None:
    data = saved()
    data[name.strip()[:40]] = text
    jsonfile.write(path(), data, indent=1)


def delete(name: str) -> bool:
    """Remove a prompt saved from the Navigator (the ones in the settings file stay)."""
    data = saved()
    if name not in data:
        return False
    data.pop(name)
    jsonfile.write(path(), data, indent=1)
    return True
