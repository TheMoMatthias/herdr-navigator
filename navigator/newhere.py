"""herdr workspace menu › "Navigator: new agent session here…": open the Navigator on the
new-session dialog for that workspace's project (or worktree)."""
from __future__ import annotations

import os

from . import herdr, settings


def main() -> None:
    ws = os.environ.get("HERDR_WORKSPACE_ID", "")
    entry = "navigator" if os.name == "nt" else "navigator-unix"
    herdr.run("plugin", "pane", "open", "--plugin", settings.PLUGIN_ID, "--entrypoint", entry,
              "--placement", "overlay", "--focus", "--env", f"NAV_NEW={ws}", check=False)


if __name__ == "__main__":
    main()
