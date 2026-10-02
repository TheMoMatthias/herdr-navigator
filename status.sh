#!/bin/sh
# Tab-bar status: print the line the Navigator daemon keeps fresh (no Python start);
# without a daemon, compute it the old way (which also starts the daemon).
if [ -f "$1" ]; then cat "$1"; else exec sh "$(cd "$(dirname "$0")" && pwd)/run.sh" status; fi
