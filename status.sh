#!/bin/sh
# Tab-bar status: print the line the Navigator daemon keeps fresh (no Python start).
# The daemon rewrites daemon.json every 2 s; an older heartbeat means it is gone and status.txt
# is a leftover, so compute the line the old way instead, which also starts a new daemon.
beat="$(dirname "$1")/daemon.json"
age() { now=$(date +%s); m=$(stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null) || return 1; echo $((now - m)); }
if [ -f "$1" ] && a=$(age "$beat") && [ "$a" -lt 10 ]; then
    cat "$1"
else
    exec sh "$(cd "$(dirname "$0")" && pwd)/run.sh" status
fi
