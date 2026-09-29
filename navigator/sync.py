"""Startup/event hook: keep herdr's own UI project-aware.

* auto-named workspaces are renamed to their project (never a name you typed yourself);
* every workspace reports `$agents` (e.g. "⚠1 ◐2") and `$outside` (sessions of that project or
  worktree running in other terminals, e.g. "⧉2") for the sidebar Space rows;
* every agent pane reports `$project` (project ⎇ worktree) for the sidebar Agent rows.
Only changed values are sent, so frequent status events stay cheap. Idempotent.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter

from . import herdr, model, settings
from .model import summarize

SOURCE = f"plugin:{settings.PLUGIN_ID}"


RESEND_SECONDS = 60  # hooks run concurrently; a periodic full resend heals any lost report


def _load_sent() -> dict:
    try:
        d = json.loads((settings.state_dir() / "sync-sent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d.get("sent", {}) if time.time() - d.get("at", 0) < RESEND_SECONDS else {}


def _save_sent(sent: dict, started: float, forced: bool) -> None:
    f = settings.state_dir() / "sync-sent.json"
    try:
        at = started if forced else json.loads(f.read_text(encoding="utf-8")).get("at", started)
    except (OSError, ValueError):
        at = started
    try:
        f.write_text(json.dumps({"at": at, "sent": sent}), encoding="utf-8")
    except OSError:
        pass


def _report(kind: str, target: str, name: str, value: str, old: dict, sent: dict, seq: str) -> None:
    k = f"{kind}:{target}:{name}"
    if old.get(k) == value:
        sent[k] = value
        return
    args = ["--token", f"{name}={value}"] if value else ["--clear-token", name]
    try:
        herdr.run(kind, "report-metadata", target, "--source", SOURCE, *args)
        sent[k] = value  # only confirmed reports are remembered, so a failure is retried
    except (herdr.HerdrError, OSError):
        pass


def sync(force: bool = False) -> None:
    started = time.time()
    world = model.build()
    cfg = settings.load()
    old = {} if force else _load_sent()
    force = force or not old
    sent: dict[str, str] = {}
    seq = str(int(time.time() * 1000))

    for w in world.workspaces:
        if not w.project:
            continue
        p = w.project
        # herdr's automatic label is the folder name; linked-worktree children keep theirs
        # because the sidebar already indents them under their repo
        if cfg.auto_name and not w.linked_worktree:
            if w.label == os.path.basename(w.cwd.rstrip("\\/")) and p.label != w.label:
                herdr.run("workspace", "rename", w.id, p.label, check=False)
        inside = Counter(a.status for a in world.agents if a.workspace_id == w.id)
        v = world.view(p.root)
        # sessions in other terminals belong to the workspace of their worktree, else the primary
        open_wts = {x.project.worktree for x in world.workspaces if x.project and x.project.root == p.root}
        outside = [a for a in (v.agents if v else []) if not a.in_herdr and (
            a.project.worktree == p.worktree if p.worktree else a.project.worktree not in open_wts - {""})]
        _report("workspace", w.id, "agents", summarize(inside), old, sent, seq)
        _report("workspace", w.id, "outside", f"⧉{len(outside)} outside" if outside else "", old, sent, seq)

    for a in world.agents:
        if a.in_herdr:
            subs = f"↳{len(a.subagents)}" if a.subagents else ""
            _report("pane", a.pane_id, "project", a.project.label, old, sent, seq)
            _report("pane", a.pane_id, "subagents", subs, old, sent, seq)
    _save_sent(sent, started, force)


def main() -> None:
    try:
        startup = os.environ.get("HERDR_PLUGIN_EVENT") == "startup"
        # after a server start herdr has no metadata: re-report everything
        sync(force=startup)
        if startup:
            herdr.run("notification", "show", "herdr navigator ready", "--body",
                      "Press F1 for the Navigator: projects, agents, resume. F4 lists every key.",
                      check=False)
    except Exception as e:  # a hook must never take anything down; herdr logs stderr
        print(f"navigator sync failed: {e}")


if __name__ == "__main__":
    main()
