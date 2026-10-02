"""Startup/event hook: keep herdr's own UI project-aware.

* auto-named workspaces are renamed to their project (never a name you typed yourself);
* linked-worktree workspaces are named after the session(s) working in them;
* every workspace reports `$agents` (e.g. "⚠1 ◐2") and `$s1`..`$s8`: one line per session working
  in it (tabs included, sessions in other windows marked ↗), which the sidebar shows indented under
  the Space, so a session in a second tab is never hidden behind its workspace's name;
* every agent pane reports `$session` (its name as the CLI shows it), `$project`
  (project ⎇ worktree) and `$subagents` for the sidebar Agent rows.
Only changed values are sent, so frequent status events stay cheap. Idempotent.
"""
from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import time
from collections import Counter

from . import herdr, model, settings
from .model import summarize

SOURCE = f"plugin:{settings.PLUGIN_ID}"


_SEQ = (str(n) for n in itertools.count(int(time.time() * 1000) * 1000))
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
    """Queue one token; _flush sends every changed token of a target in a single call."""
    k = f"{kind}:{target}:{name}"
    if old.get(k) == value:
        sent[k] = value
        return
    _PENDING.setdefault((kind, target), []).append((k, name, value))


_PENDING: dict[tuple[str, str], list[tuple[str, str, str]]] = {}


def _flush(sent: dict) -> None:
    for (kind, target), items in list(_PENDING.items()):
        args = []
        for _, name, value in items:
            args += ["--token", f"{name}={value}"] if value else ["--clear-token", name]
        try:
            herdr.run(kind, "report-metadata", target, "--source", SOURCE, *args, "--seq", next(_SEQ))
            for k, _, value in items:
                sent[k] = value  # only confirmed reports are remembered, so a failure is retried
        except (herdr.HerdrError, OSError):
            pass
    _PENDING.clear()


SESSION_ROWS = 8
SIDE_ICON = {"blocked": "!", "reply": "?", "done": "●", "working": "◐", "idle": "○", "unknown": "·"}
PAD = "\u2800"  # braille blank: looks like a space, but herdr trims real spaces off token values


def side_counts(agents: list) -> str:
    c = Counter(a.status for a in agents)
    return " ".join(f"{SIDE_ICON[s]}{c[s]}" for s in ("blocked", "reply", "done", "working", "idle") if c.get(s))


def session_lines(label: str, agents: list) -> list[str]:
    """The indented lines a Space shows under itself: one per session, urgent first. A worktree
    Space already named after its only session shows none (it would just repeat the name)."""
    agents = sorted(agents, key=lambda a: (model.STATE_ORDER.get(a.status, 9), a.display.lower()))
    if len(agents) == 1 and agents[0].display.strip().lower() == label.strip().lower():
        return []
    lines = []
    for a in agents:
        icon = "↗" if not a.in_herdr else SIDE_ICON.get(a.status, "·")
        lines.append(f"{icon} {a.display[:30]}")
    if len(lines) > SESSION_ROWS:
        lines = lines[:SESSION_ROWS - 1] + [f"+{len(lines) - SESSION_ROWS + 1} more"]
    return [("└─ " if i == len(lines) - 1 else "├─ ") + ln for i, ln in enumerate(lines)]


def agent_tree(agents: list, folded: dict | None = None) -> list[tuple]:
    """herdr's Agents panel as a tree: (agent, order, heading, line, lane, hidden) per agent in
    herdr. Projects come in the order of their most urgent agent, agents most urgent first; the
    first shown agent of a project carries the project heading row. A folded project shows only
    the agents that need you (or its first agent, to carry the heading)."""
    folded = folded or {}
    agents = [a for a in agents if a.in_herdr]
    urg = lambda a: (model.STATE_ORDER.get(a.status, 9), a.waiting_since or 9e18)  # noqa: E731
    first: dict[str, tuple] = {}
    for a in agents:
        first[a.project.root] = min(first.get(a.project.root, (99, 9e18)), urg(a))
    agents.sort(key=lambda a: (first[a.project.root], a.project.name.lower(), urg(a),
                               a.project.worktree, a.display.lower()))
    out = []
    for i, a in enumerate(agents):
        group = [x for x in agents if x.project.root == a.project.root]
        shut = folded.get(a.project.root, False)
        shown = [x for x in group if x.status in model.NEEDS_YOU] if shut else group
        shown = shown or group[:1]
        if a not in shown:
            out.append((a, f"{i:04d}", "", "", "", True))
            continue
        last = a is shown[-1]
        head = ""
        if a is shown[0]:
            more = len(group) - len(shown)
            head = (f"{'▸' if shut else '▾'} {a.project.name[:30]}  {side_counts(group)}"
                    + (f"  +{more} folded" if more else ""))
        line = f"{'└─' if last else '├─'} {SIDE_ICON.get(a.status, '·')} {a.display[:34]}"
        lane = f"{PAD if last else '│'}{PAD * 4}▹ {a.project.worktree}" if a.project.worktree else ""
        out.append((a, f"{i:04d}", head, line, lane, False))
    return out


def _set_view(world) -> None:
    """Make herdr's Agents panel follow the tree order (until the herdr server restarts)."""
    herdr.request("agent.view.set", {
        "source": SOURCE, "label": "by project",
        "filter": {"op": "not", "filter": {"op": "eq", "field": {"token": "hide"}, "value": "1"}},
        "sort": [{"field": {"token": "order"}, "order": "asc"}]})


def _named(ws: str | None = None, label: str | None = None) -> dict:
    """Labels the Navigator gave workspaces, so it may update them but never your own names."""
    f = settings.state_dir() / "named.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if ws:
        d[ws] = label
        f.write_text(json.dumps(d), encoding="utf-8")
    return d


def sync(force: bool = False) -> None:
    started = time.time()
    world = model.build()
    try:  # which session runs in which pane, so a server restart can bring them back named
        from . import restore
        restore.record_panes(world, herdr.snapshot())
    except Exception as e:
        print(f"navigator: recording panes failed: {e}")
    cfg = settings.load()
    old = {} if force else _load_sent()
    force = force or not old
    sent: dict[str, str] = {}
    seq = ""
    global _SEQ
    _SEQ = (str(n) for n in itertools.count(int(started * 1000) * 1000))  # rises across runs and reports

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
        # sessions in other windows belong to the workspace of their worktree, else the primary
        open_wts = {x.project.worktree for x in world.workspaces if x.project and x.project.root == p.root}
        mine = [a for a in (v.agents if v else []) if (
            a.project.worktree == p.worktree if p.worktree else a.project.worktree not in open_wts - {""})]
        outside = [a for a in mine if not a.in_herdr and not a.mirror_pane]
        _report("workspace", w.id, "agents", summarize(inside), old, sent, seq)
        _report("workspace", w.id, "outside", "", old, sent, seq)  # now part of the session rows
        lines = session_lines(w.label, [a for a in world.agents if a.workspace_id == w.id] + outside)
        for i in range(SESSION_ROWS):
            _report("workspace", w.id, f"s{i + 1}", lines[i] if i < len(lines) else "", old, sent, seq)
        # a worktree workspace is named after the session(s) working in it, as your CLI shows them
        if w.linked_worktree and cfg.auto_name:
            names = sorted({a.display for a in mine if a.display})
            want = " · ".join(names)[:32] if names else os.path.basename(w.cwd.rstrip("\\/"))
            ours = _named().get(w.id)
            if want != w.label and (w.label == os.path.basename(w.cwd.rstrip("\\/")) or w.label == ours):
                herdr.run("workspace", "rename", w.id, want, check=False)
                _named(w.id, want)

    replies_file = settings.state_dir() / "replies.json"
    try:
        before = json.loads(replies_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = None
    for a in world.agents:  # herdr's own agent list and pane borders say "needs reply" too
        if not a.in_herdr:
            continue
        label = "needs reply" if a.status == "reply" else ""
        k = f"pane:{a.pane_id}:state-label"
        if old.get(k) != label:
            args = ["--agent", a.cli, "--state-label", f"idle={label}"] if label else ["--clear-state-labels"]
            herdr.run("pane", "report-metadata", a.pane_id, "--source", SOURCE, *args, "--seq", next(_SEQ),
                      check=False)
        sent[k] = label
        if (label and before is not None and a.pane_id not in before
                and settings.load().alerts.get("toast_reply", True)):
            herdr.run("notification", "show", f"{a.display[:40]} needs a reply", "--body",
                      f"{a.project.label}: its last message asks you something", "--sound", "request",
                      check=False)
    try:  # who waits on a reply (or a question in another window): read by alerts.check_waiting
        replies_file.write_text(json.dumps({
            (a.pane_id or f"out:{a.cli}:{a.session_id}"): {
                "name": a.display, "workspace_id": a.workspace_id,
                "why": "needs a reply" if a.status == "reply" else "waits for you"}
            for a in world.agents if a.status == "reply" or (not a.in_herdr and a.status == "blocked")}),
            encoding="utf-8")
    except OSError:
        pass

    # herdr's Agents panel, nested by project: heading row, ├/└ branches, the worktree below
    from . import startup
    for a, order, head, line, lane, hidden in agent_tree(world.agents, startup.ui_state().get("agents_folded", {})):
        _report("pane", a.pane_id, "hide", "1" if hidden else "", old, sent, seq)
        _report("pane", a.pane_id, "order", order, old, sent, seq)
        _report("pane", a.pane_id, "grp", head, old, sent, seq)
        _report("pane", a.pane_id, "line", line, old, sent, seq)
        _report("pane", a.pane_id, "lane", lane, old, sent, seq)
    if old.get("view") != "by project 2":
        try:
            _set_view(world)
            sent["view"] = "by project 2"
        except Exception as e:
            print(f"navigator: agent view not set: {e}")
    else:
        sent["view"] = "by project 2"

    for a in world.agents:
        if a.in_herdr:  # mirror panes report their own tokens
            subs = f"↳{len(a.subagents)}" if a.subagents else ""
            _report("pane", a.pane_id, "project", a.project.label, old, sent, seq)
            _report("pane", a.pane_id, "session", a.display, old, sent, seq)
            _report("pane", a.pane_id, "subagents", subs, old, sent, seq)
    _flush(sent)
    _save_sent(sent, started, force)


def spawn_background() -> None:
    """Run a sync detached (from the status line, which must stay fast)."""
    import subprocess
    root = Path(__file__).resolve().parent.parent
    py = root / ".venv" / ("Scripts/pythonw.exe" if os.name == "nt" else "bin/python")
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    subprocess.Popen([str(py), "-m", "navigator.sync"], cwd=str(root),
                     env={**os.environ, "PYTHONPATH": str(root), "PYTHONUTF8": "1"}, creationflags=flags,
                     close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
