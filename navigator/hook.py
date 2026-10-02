"""herdr event hook. With the daemon running it does nothing: the daemon got the same event
over its subscription. Without one it starts it, and syncs this once itself so nothing is
missed. Runs for every herdr event, so the alive check uses the standard library only."""
from __future__ import annotations

import json
import os
import time


def _alive() -> bool:
    state = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if not state:
        return False  # unknown dir: let the full path decide
    try:
        with open(os.path.join(state, "daemon.json"), encoding="utf-8") as f:
            return time.time() - json.load(f).get("at", 0) < 10.0  # daemon.STALE
    except (OSError, ValueError):
        return False


def main() -> None:
    if _alive():
        return
    try:
        from . import daemon, sync
        if daemon.alive():
            return
        daemon.start()
        sync.main()
    except Exception as e:  # a hook must never take anything down; herdr logs stderr
        print(f"navigator hook failed: {e}")


if __name__ == "__main__":
    main()
