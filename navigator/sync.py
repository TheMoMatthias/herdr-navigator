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
        try:  # the socket: one in-process call per target (the CLI costs a process each)
            herdr.report_metadata(kind, target, SOURCE, {name: (value or None) for _, name, value in items},
                                  int(next(_SEQ)))
        except (herdr.HerdrError, OSError, ValueError):
            args = []
            for _, name, value in items:
                args += ["--token", f"{name}={value}"] if value else ["--clear-token", name]
            try:
                herdr.run(kind, "report-metadata", target, "--source", SOURCE, *args, "--seq", next(_SEQ))
            except (herdr.HerdrError, OSError):
                continue
        for k, _, value in items:
            sent[k] = value  # only confirmed reports are remembered, so a failure is retried
    _PENDING.clear()


SESSION_ROWS = 8
SIDE_ICON = {"blocked": "!", "reply": "?", "done": "●", "working": "◐", "idle": "○", "unknown": "·"}
PAD = "\u2800"  # braille blank: looks like a space, but herdr trims real spaces off token values


def side_counts(agents: list) -> str:
    c = Counter(a.status for a in agents)
    return " ".join(f"{SIDE_ICON[s]}{c[s]}" for s in ("blocked", "reply", "done", "working", "idle") if c.get(s))


def session_lines(label: str, agents: list, folded: bool = False) -> list[str]:
    """The indented lines a Space shows under itself: one per session, urgent first. A worktree
    Space already named after its only session shows none (it would just repeat the name). A
    folded project keeps only the sessions that need you, plus a "+N folded" line."""
    agents = sorted(agents, key=lambda a: (model.rank(a), a.display.lower()))
    if len(agents) == 1 and agents[0].display.strip().lower() == label.strip().lower():
        return []
    hidden = [a for a in agents if a.status not in model.NEEDS_YOU] if folded else []
    lines = [f"{'↗' if not a.in_herdr else SIDE_ICON.get(a.status, '·')} {a.display[:30]}"
             for a in agents if a not in hidden]
    if hidden:
        lines.append(f"+{len(hidden)} folded")
    if len(lines) > SESSION_ROWS:
        lines = lines[:SESSION_ROWS - 1] + [f"+{len(lines) - SESSION_ROWS + 1} more"]
    return [("└─ " if i == len(lines) - 1 else "├─ ") + ln for i, ln in enumerate(lines)]


def agent_tree(agents: list, folded: dict | None = None) -> list[tuple]:
    """herdr's Agents panel as a tree: (agent, order, heading, line, lane, hidden) per agent in
    herdr. Projects come in the order of their most urgent agent, agents by model.rank (running,
    then needs you, then idle; the most recent state change first). The first shown agent of
    a project carries the project heading row. A folded project shows only the agents that need
    you (or its first agent, to carry the heading)."""
    folded = folded or {}
    agents = [a for a in agents if a.in_herdr]
    first: dict[str, tuple] = {}
    for a in agents:
        first[a.project.root] = min(first.get(a.project.root, (99,)), model.rank(a))
    agents.sort(key=lambda a: (first[a.project.root], a.project.name.lower(), model.rank(a), a.display.lower()))
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
        # herdr indents an entry's 2nd and later rows by two columns: the heading carrier's line is
        # its 2nd row, so every other agent's line (its 1st row) gets the same two-column pad
        line = (PAD * 2 if not head else "") + f"{'└─' if last else '├─'} {SIDE_ICON.get(a.status, '·')} {a.display[:34]}"
        lane = f"{PAD if last else '│'}{PAD * 4}▹ {a.project.worktree}" if a.project.worktree else ""
        out.append((a, f"{i:04d}", head, line, lane, False))
    return out


def space_order(world) -> list[str]:
    """herdr's Spaces in model.rank order: a repo with its worktree Spaces moves as one block (the
    block of its most urgent Space), the repo's own Space first, its worktrees by rank below it.
    Equal ranks keep their current order, so nothing moves without a reason."""
    ws = sorted(world.workspaces, key=lambda w: w.number)
    pos = {w.id: i for i, w in enumerate(ws)}
    none = (99,)
    best: dict[str, tuple] = {}
    for a in world.agents:
        if a.workspace_id:
            best[a.workspace_id] = min(best.get(a.workspace_id, none), model.rank(a))
    blocks: dict[str, list] = {}
    for w in ws:
        blocks.setdefault(w.project.root if w.project else w.id, []).append(w)
    key = lambda w: (best.get(w.id, none), pos[w.id])  # noqa: E731
    out = []
    for members in sorted(blocks.values(), key=lambda m: min(key(w) for w in m)):
        out += [w.id for w in members if not w.linked_worktree]
        out += [w.id for w in sorted((w for w in members if w.linked_worktree), key=key)]
    return out


def _sort_spaces(world) -> int:
    """Move herdr's Spaces into space_order with as few moves as possible. Returns the moves."""
    cur = [w.id for w in sorted(world.workspaces, key=lambda w: w.number)]
    want = space_order(world)
    moves = 0
    for i, wid in enumerate(want):
        if i < len(cur) and cur[i] != wid and wid in cur:
            herdr.request("workspace.move", {"workspace_id": wid, "insert_index": i})
            cur.remove(wid)
            cur.insert(i, wid)
            moves += 1
    return moves


VIEW_LABEL = "by project"


def _set_view(world=None) -> None:
    """Make herdr's Agents panel follow the tree order."""
    herdr.request("agent.view.set", {
        "source": SOURCE, "label": VIEW_LABEL,
        "filter": {"op": "not", "filter": {"op": "eq", "field": {"token": "hide"}, "value": "1"}},
        "sort": [{"field": {"token": "order"}, "order": "asc"}]}, timeout=6)


def ensure_view() -> bool:
    """herdr drops a plugin's Agents view when the plugin is reloaded or re-linked and when the
    server restarts. A clear from a source that owns nothing changes nothing but reports what is
    active (~5 ms), so the view is re-applied exactly when it is missing. True = it was missing."""
    try:
        cur = herdr.request("agent.view.clear", {"source": "plugin:navigator.probe"}, timeout=4)
        if cur.get("active") and cur.get("source") == SOURCE and cur.get("label") == VIEW_LABEL:
            return False
        _set_view()
        return True
    except (herdr.HerdrError, OSError, ValueError) as e:
        print(f"navigator: agent view not checked: {e}")
        return False


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
    from . import startup
    folded = startup.ui_state().get("agents_folded", {})
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
        _report("workspace", w.id, "agents", side_counts([a for a in world.agents if a.workspace_id == w.id]),
                old, sent, seq)
        _report("workspace", w.id, "outside", "", old, sent, seq)  # now part of the session rows
        lines = session_lines(w.label, [a for a in world.agents if a.workspace_id == w.id] + outside,
                              folded.get(p.root, False))
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
            params = ({"agent": a.cli, "state_labels": {"idle": label}} if label else {"clear_state_labels": True})
            try:
                herdr.request("pane.report_metadata", {"pane_id": a.pane_id, "source": SOURCE,
                                                       "seq": int(next(_SEQ)), **params}, timeout=6)
            except (herdr.HerdrError, OSError, ValueError):
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
    for a, order, head, line, lane, hidden in agent_tree(world.agents, folded):
        _report("pane", a.pane_id, "hide", "1" if hidden else "", old, sent, seq)
        _report("pane", a.pane_id, "order", order, old, sent, seq)
        _report("pane", a.pane_id, "grp", head, old, sent, seq)
        _report("pane", a.pane_id, "line", line, old, sent, seq)
        _report("pane", a.pane_id, "lane", lane, old, sent, seq)
    ensure_view()

    for a in world.agents:
        if a.in_herdr:  # mirror panes report their own tokens
            subs = f"↳{len(a.subagents)}" if a.subagents else ""
            _report("pane", a.pane_id, "project", a.project.label, old, sent, seq)
            _report("pane", a.pane_id, "session", a.display, old, sent, seq)
            _report("pane", a.pane_id, "subagents", subs, old, sent, seq)
    _flush(sent)
    _save_sent(sent, started, force)
    if cfg.sort_spaces:
        try:
            _sort_spaces(world)
        except Exception as e:
            print(f"navigator: spaces not sorted: {e}")


def spawn_background() -> None:
    """Run a sync detached (from the status line, which must stay fast)."""
    from . import daemon
    if daemon.poke():
        return  # the resident daemon syncs within a second
    import subprocess
    root = Path(__file__).resolve().parent.parent
    py = root / ".venv" / ("Scripts/pythonw.exe" if os.name == "nt" else "bin/python")
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    subprocess.Popen([str(py), "-m", "navigator.sync"], cwd=str(root),
                     env={**os.environ, "PYTHONPATH": str(root), "PYTHONUTF8": "1"}, creationflags=flags,
                     close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


LOCK_STALE = 60  # seconds: a lock older than this belongs to a sync that died


def _lock() -> bool:
    f = settings.state_dir() / "sync.lock"
    try:
        fd = os.open(f, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            if time.time() - f.stat().st_mtime > LOCK_STALE:
                f.unlink()
                return _lock()
        except OSError:
            pass
        return False
    except OSError:
        return True  # no state dir to lock in: just run


def coalesced(force: bool = False) -> bool:
    """A burst of herdr events (ten agents changing state at once) starts ten hooks: one syncs,
    the others only leave a note, and the one syncing runs once more after a note. No pile-up,
    and nothing reported from a stale view of the world. False when another sync held the lock
    (it will run again for us, but not with `force`)."""
    note = settings.state_dir() / "sync.again"
    lock = settings.state_dir() / "sync.lock"
    ran = False
    while True:
        if not _lock():
            try:
                note.touch()
            except OSError:
                pass
            return ran
        try:
            for _ in range(5):
                note.unlink(missing_ok=True)
                sync(force=force)
                ran, force = True, False
                if not note.exists():
                    break
        finally:
            lock.unlink(missing_ok=True)
        if not note.exists():  # a hook that came in while the lock was being released
            return ran


def main() -> None:
    try:
        startup = os.environ.get("HERDR_PLUGIN_EVENT") == "startup"
        # after a server start herdr has no metadata: re-report everything
        coalesced(force=startup)
        if startup:
            herdr.run("notification", "show", "herdr navigator ready", "--body",
                      "Press F1 for the Navigator: projects, agents, resume. F4 lists every key.",
                      check=False)
    except Exception as e:  # a hook must never take anything down; herdr logs stderr
        print(f"navigator sync failed: {e}")


if __name__ == "__main__":
    main()
