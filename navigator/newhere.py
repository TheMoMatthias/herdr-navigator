"""herdr's menu (right-click a Space or a pane) › the Navigator's new-session entries: open the
Navigator on the matching dialog, which closes again once it is answered.
  newhere        new session in this Space's project (or worktree)
  newhere any    new session in any folder
  newhere open   open a project folder as a Space"""
from __future__ import annotations

import os
import sys

from . import herdr, settings

ENV = {"any": ("NAV_NEWANY", "1"), "open": ("NAV_OPENDIR", "1")}


def main(mode: str = "") -> None:
    mode = mode or (sys.argv[1] if len(sys.argv) > 1 else "")
    var, value = ENV.get(mode, ("NAV_NEW", os.environ.get("HERDR_WORKSPACE_ID", "")))
    entry = "navigator" if os.name == "nt" else "navigator-unix"
    herdr.run("plugin", "pane", "open", "--plugin", settings.PLUGIN_ID, "--entrypoint", entry,
              "--placement", "overlay", "--focus", "--env", f"{var}={value}", check=False)


if __name__ == "__main__":
    main()
