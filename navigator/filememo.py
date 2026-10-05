"""Remember what a function read out of a file until the file changes.

The transcript readers (context fill, open question, last activity, last answer) tail up to
600 KB each, and every Navigator refresh asks them for every agent. Most agents are idle, so
their transcript has not changed since the last ask: keyed on the file's mtime and size, the
answer is reused and nothing is read.
"""
from __future__ import annotations

import functools
import os


def by_file(fn):
    """Cache fn(path, ...) or fn(cli, path, ...) by its arguments and the file's (mtime, size)."""
    memo: dict[tuple, tuple] = {}

    @functools.wraps(fn)
    def wrapper(*args):
        path = args[1] if len(args) > 1 else args[0] if args else ""
        try:
            st = os.stat(path) if path else None
        except OSError:
            st = None
        if st is None:
            return fn(*args)
        stamp = (st.st_mtime_ns, st.st_size)
        hit = memo.get(args)
        if hit and hit[0] == stamp:
            return hit[1]
        out = fn(*args)
        memo[args] = (stamp, out)
        return out

    wrapper.cache_clear = memo.clear
    return wrapper
