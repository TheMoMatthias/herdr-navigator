"""JSON state files that several processes touch at once (the Navigator, herdr hooks, the logon
restore, a second Navigator window). Two rules keep them from losing data:

* a read that finds the file mid-write retries, and a file that stays unreadable is copied
  aside (`name.unreadable-<time>`) before anyone writes over it, so ticks are never wiped by a
  read-modify-write that started from an empty dict;
* a write goes to a temp file next to the target and replaces it in one step. On Windows that
  replace fails while another process has the file open (WinError 5), so it is retried and,
  as a last resort, written in place."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path


def read(path: Path, default=None):
    """The parsed file, or `default` when it does not exist (or stays unreadable)."""
    for attempt in range(4):
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return default
        except OSError:
            time.sleep(0.05)
            continue
        if not text.strip() and attempt < 3:
            time.sleep(0.05)  # caught between truncate and write
            continue
        try:
            return json.loads(text)
        except ValueError:
            if attempt < 3:
                time.sleep(0.05)
                continue
            try:  # keep what is there, so nothing is lost when the caller writes next
                shutil.copy2(path, path.with_name(f"{path.name}.unreadable-{int(time.time())}"))
            except OSError:
                pass
            return default
    return default


def write(path: Path, data, indent: int | None = None) -> None:
    write_text(path, json.dumps(data, indent=indent))


def write_text(path: Path, text: str) -> None:
    """Replace a file in one step (a reader never sees half of it)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        for _ in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # a reader has it open right now (Windows)
                time.sleep(0.03)
        path.write_text(text, encoding="utf-8")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
