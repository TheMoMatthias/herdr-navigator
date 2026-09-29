#!/bin/sh
# herdr-navigator launcher: run.sh <module> [args]   e.g. run.sh app resume / run.sh status
here="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$here/.venv/bin/python" ]; then
  echo "herdr-navigator is not set up yet: run  python3 $here/bootstrap.py"
  exit 1
fi
mod="$1"; shift
PYTHONPATH="$here" PYTHONUTF8=1 exec "$here/.venv/bin/python" -m "navigator.$mod" "$@"
