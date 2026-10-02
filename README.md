# herdr-navigator

A [herdr](https://herdr.dev) plugin that gives you one clickable, keyboard-driven screen for
everything your coding agents are doing:

* **Which project am I in?** Workspaces are named after their git repo. Worktrees fold into
  their repo and show up as indented children in the sidebar, and every session inside a
  Space gets its own indented row under it (`├ ⏳ STORAGE`), tabs included. herdr's Agents panel is
  nested the same way: a heading per project (`▾ AlgoTrader ⏳1 ○5`), its agents below as
  `├─ ? STORAGE · needs reply`, a worktree agent's `▹ lane` under its name, the project with
  whoever needs you first (`!` waits on you, `?` needs a reply). Fold a project there by
  right-clicking one of its panes or its workspace › `Navigator: fold / unfold this project`;
  folding in the Navigator's Agents tab does the same. A folded project keeps who needs you.
* **What is running where?** Every Claude Code and Codex agent is listed per project and
  worktree, including sessions running in *other* terminal windows and their live
  **sub-agents**, each with what it is doing right now (`⚙ Bash: run tests`, `💬 …`).
* **How do I get back in?** Every Claude Code and Codex session from the last 45 days can be
  searched and resumed with one click, in the right project, worktree and tab.
* **Sessions in other windows show up in herdr.** Each one gets a *mirror* tab in its
  project's workspace. The mirror appears in herdr's Agents panel under the session's real
  name and state, shows its live conversation, and resumes it inside herdr once the other
  window exits.
* **Real session names, for every CLI:** the name you gave it (`claude -n`, `/rename`), else
  the provider's title (Codex thread name, Claude session title), else the terminal title.
  Worktree workspaces are named after the session working in them.
* **One-click layouts:** ▥ Columns, ▤ Rows, ▦ Grid and ◧ Main + stack rearrange the panes
  already open in the current tab. Everything keeps running, because each pane is moved into
  place rather than rebuilt.
* **Arrange panes with the mouse:** a to-scale map of the current tab. Click a pane, then
  split, swap, resize, zoom, even out, move to a new tab or workspace, rename or close it
  with buttons. Or open a new tab from a preset (2 columns, 2×2 grid, …).
* **Get back and get to what matters:** `Ctrl+Alt+R` flips to the pane you were just in,
  `F7` lists where you were last, and `Ctrl+Alt+I` jumps to the next agent that needs you
  (blocked first, then finished, oldest first).
* **Talk to agents from one place:** read an agent's latest output, send it a message, or
  answer its prompt (`⏎` / `Esc` / `^C`) without switching panes.
* **Saved project layouts:** save a tab as a named layout. Restoring it rebuilds the panes
  and starts the agents again: each resumes its own session if that session isn't running
  elsewhere, otherwise a fresh agent starts in its place.
* **Drag and drop panes:** drag one pane onto another on the map. The middle swaps them, an
  edge puts the pane on that side.
* **A wider sidebar** when names don't fit: drag its right edge with the mouse (setup raises
  herdr's limit to 120 columns), or press `Ctrl+Alt+Shift+→` / `←`.
* **What do I press?** The key for everything is shown on screen: in the tab bar, in the
  Navigator's footer (clickable) and in a live cheat sheet built from your actual config.

## Install

Requirements: herdr ≥ 0.9.3, Python ≥ 3.11 on `PATH` (`python` on Windows, `python3` on
macOS/Linux) and git.

```sh
herdr plugin install TheMoMatthias/herdr-navigator
herdr plugin action invoke momatthias.navigator.setup
```

The first command clones the repo and builds a private `.venv` with Textual. The second adds
the keys, the status bar and the sidebar layout to herdr's `config.toml` and reloads it.
**Then press `F1`.**

Setup never overwrites a setting you already have. If you bound a key yourself, it only adds
the direct chord next to your binding. It backs up `config.toml` once
(`config.toml.before-navigator.bak`) and records what it added, so you can remove exactly that:

```sh
herdr plugin action invoke momatthias.navigator.uninstall-config
herdr plugin uninstall momatthias.navigator
```

## Using it

| Key | Opens |
|---|---|
| `F1` · `Ctrl+B › Space` | **Navigator.** Projects → worktrees → agents → sub-agents, plus recent sessions |
| `F2` · `Ctrl+B › U` | **Sessions.** Search every session of every CLI, resume it, tick it for logon |
| `F3` · `Ctrl+B › I` | **Agents.** Every agent, inside herdr or not, with its current activity |
| `F4` · `Ctrl+B › /` | **Keys.** Your live key bindings (⚙ Settings › Keys) |
| `F6` · `Ctrl+B › M` | **Layout.** One-click shapes, drag-and-drop pane map, pane buttons, saved layouts |
| `F7` · `Ctrl+B › Shift+O` | **Recent.** Agents (and other panes) in the order you last visited them |
| `F9` · `Ctrl+B › ,` | **Settings.** Logon restore, accounts, phone alerts, prompts, updates |
| `Ctrl+Alt+R` · `Ctrl+B › ;` | Back to the previous pane (press again to flip back) |
| `Ctrl+Alt+I` · `F8` | Jump to the next agent that needs you |
| `Ctrl+Alt+Shift+→` / `←` | herdr sidebar wider / narrower, applied live |

Inside the Navigator:

- **Six tabs, the same shape each:** a one-row toolbar on top (the main action is blue),
  the list below, and the keys that work right now in the footer. Keys `1`–`6` switch tabs:
  Projects · Agents · Sessions · Layout · Usage · ⚙ Settings. Buttons with `▾` open a
  small menu. Every button has a tooltip. **Right-click a row** (or press `.`) for everything
  you can do with it, and **`?`** explains the tab you are on.
- **⚙ Settings** holds everything you set up once, in sections: *Logon restore*, *Accounts*,
  *Phone alerts*, *Prompts*, *General* and *Keys*. Changes save on their own when you leave a
  field or tick a box (into `navigator.toml`, comments kept).

- **Click a row once** to select it; **click it again** (or press Enter) to open it.
- **Click ☐** (or press Space) to show a project in herdr's sidebar, and click ☑ to hide it.
  The sidebar holds only the projects you tick. A ticked repo's active worktrees open as
  indented children of the repo's workspace, and **F5** picks up worktrees that became active
  since then.
- Hiding a project never kills anything. A workspace that still has an agent or a running
  command stays open, and the Navigator tells you why.
- `c` / `x` starts a new Claude / Codex agent in the selected project or worktree.
- A session marked **↗ other window** is running in another terminal. Its mirror tab follows
  it live, but typing still happens in that window. Resuming it a second time would fork the
  conversation, so the mirror's **▶ Resume here** button unlocks only after the other window
  exits.
- In **Agents**: the lower half shows what the selected agent last did. `m` types a
  message to it; the `⏎ Enter` / `Esc` / `^C` buttons answer a question or approval it's
  waiting on. `g` jumps to the next agent that needs you. Sessions in other windows are read
  only, so type into their own window.
- **Saved layouts:** in Layout, name the tab and press 💾 to save it for its
  project. ▦ Restore (or `l` in Projects) rebuilds it as a *new* tab in the project's
  workspace, creating the workspace if needed.
- In **Layout**, the top row reshapes the whole tab in one click, and **＋ New tab** opens
  presets. On the map, drag a pane onto another to swap them (middle) or place it beside
  (edges). Click a pane to select it and double-click to jump into it. The buttons on the
  right (Pane, Swap, Size, More) act on the selected pane; hover any button to see what it
  does.
  Keys: arrows move the selection, `v`/`s` split, `z` zooms, `=` evens out, `t` moves the pane to a
  new tab, `n` renames it and `Del` closes it (press twice). `Shift+arrows` swap panes and
  `Ctrl+arrows` resize.

### Sessions: search, resume, reopen at logon, sign in, relaunch

The **Sessions** tab (`3`, or `F2` straight into its search) lists every session by project, as
a tree: **▸/▾** (click it, Enter, or `←` `→`) folds a project and remembers it; projects with
nothing ticked or running start folded. An unfolded project shows what is ticked or running plus
its recent sessions; the *… older sessions* row shows the rest. Search finds every session. The
`●` column marks a session that runs now (`↗` in another window, `❓` asking you something).
Press **Enter** to resume a session in its project, and use the box to decide which sessions come
back when you log on.

- **☑ Ticks ▾** changes many at once: tick everything running now, keep only what runs now,
  untick everything, or put everything back to automatic.
- **↻ Relaunch ▾** picks every CLI or one of them, then shows what will restart.
- In **Agents**, right-click an agent to tick its session for logon (or untick it), relaunch it,
  compact it or hand off its answer.

- **☑ / ☐** in front of each session is its tick. A bright box is your own choice; a dim one
  was set by the auto-tick, which keeps the newest 3 sessions of each project lane (the main
  checkout, and every worktree separately) that you worked in during the last 3 days. Click
  the box (or press Space) to tick or untick. The project row's box switches the whole
  project on or off.
- **Right-click** a row (or press `.`) for everything else. On a session: open it now, go to
  it, relaunch it in place, launch options (Claude: model, effort, permission mode, Remote
  Control; any CLI: extra arguments), back to automatic, show the resume command. On a project:
  restore on/off, auto-tick on/off, open all its ticked now, untick all, back to automatic.
- **⏻ Logon** (also ⚙ Settings › Logon restore, with how many sessions the auto-tick picks,
  the delay after logon and the Claude naming / Remote Control defaults) adds a logon entry (Windows: a per-user scheduled task; macOS LaunchAgent;
  Linux XDG autostart; no admin rights needed). At logon it opens herdr in a terminal. Every
  agent pane from last time comes back in its own pane, named and with its launch options, then
  every ticked session that isn't running yet opens in its project's workspace. A session in a
  linked worktree gets the worktree's own workspace, indented under its repo. Each launch is checked and logged to `restore.log` in the plugin's state directory,
  and a notification gives the summary. If the Claude token has expired, one Claude session is
  opened first and the rest wait until its refresh has landed. Many sessions refreshing the
  token at once log each other out.
- **▶ Open ticked** does the same thing right now.
- **⚙ Settings › Accounts** shows who is signed in to each CLI and offers Sign in / Sign out (Claude, Codex,
  OpenCode and Gemini by default; add any CLI under `[login.<cli>]`). Sign-in opens in a new tab.
  When the CLI has saved the new credentials, the Navigator comes back with **↻ Relaunch**.
  **Profiles** switch accounts without signing out. 💾 saves the current login as a named profile,
  and ⇄ switches to another. History stays shared, because only the login files are swapped
  (`[login.<cli>] files` and `json_keys`). The active profile's refreshed tokens are saved back
  before every switch. Profiles are stored in the plugin's state directory on this machine. On
  macOS, Claude keeps its login in the Keychain, so Claude profiles there are not supported yet.
- **↻ Relaunch** restarts open sessions in their own panes, so the layout stays: it stops the
  agent process and resumes the same conversation there. Sessions that are working right now are
  left alone unless you include them. Sessions in other terminal windows are listed, never killed.
- Coming from the *session-restore* tool? `python -m navigator.startup import <sessions-registry.json>`
  copies over its ticks, switched-off projects and Claude launch options.

**After a herdr restart** (logon, update, crash) every pane that had an agent gets its session
back in that same pane, with its name, Remote Control and launch options. herdr keeps pane ids
across restarts, and setup sets herdr's own `[session] resume_agents_on_restore = false`
because that resume starts sessions without them. Uninstall restores herdr's default.

**New session** (*With name, worktree, model…* under `+ New agent ▾` in Projects, or `N`) asks for the CLI, a name, the
folder, optionally a **new git worktree** (created by herdr, so it is grouped under the repo),
and, for Claude, model, effort, permission mode and Remote Control. The options stick to the
session, so later relaunches use them too.

**Send to many:** in Agents, tick agents with ☐ (or Space). `Send` then types the message, or
presses ⏎ / Esc / ^C, in every ticked agent at once.

### Working with many agents

- **Who needs you.** Agents puts first the agents that need you, longest wait first, with a
  `Waits` column: `⚠` waits for an approval or a question, `⏳` finished with a message that
  asks you something (a question at its end, "should I…", "let me know…"), `✔` finished and
  not yet seen. The rest is `○ Parked`. The top bar counts them (`⏳ 2 need a reply`).
- **Answer without leaving.** `⚑ Next waiting` (`g`) selects the next agent that needs you and
  puts you in the message box, its last words in the preview. When it shows numbered choices
  (a permission prompt or a question), **Answer: [1 Yes] [2 …] [3 No]** buttons press that number.
- **Nested by project.** Agents are grouped under their project (`▾ AlgoTrader ⏳1 ○6`), the
  `Where` column says which checkout. `←`/`→`, a click on `▾` or Enter on the header folds a
  group; a folded group still shows the agents that need you.
- **Recent first.** `⇅` sorts Agents by your last visit instead, other panes included (`F7`).
- **Watch and chain.** Right-click an agent: `🔔 Tell me when it finishes` (a herdr toast, and
  your phone when alerts are set up), or `⛓ When it finishes, hand its answer to…` another agent,
  which turns Hand off into a plan → review pipeline. Watched agents carry 🔔 / ⛓ after their
  name; right-click again to stop. In Layout, `🚨 Watch for errors` tells you when a test or
  server pane prints `FAILED`, a traceback or `Error:` (new output only).
- **Needs a reply, in herdr too.** herdr's own agent list and pane borders say `needs reply`, and
  a toast with sound appears when an agent starts asking you something (Settings › Phone alerts).
- **New agent from a workspace.** Right-click a workspace in herdr's sidebar ›
  `Navigator: new agent session here…`, or right-click a project heading in Agents.

- **Context gauge.** Agents has a `Ctx` column that shows how full each session's context is
  (yellow from 70%, red from 85%), and the top bar warns when one is nearly full. `⇣` sends
  `/compact` to the ticked agents, or to the selected one. Windows come from `[context]` (by model
  name); Codex reports its own.
- **Saved prompts.** `☰ Prompts ▾` sends a saved prompt in one click (Status, Compact, Wrap up,
  Commit & push, Review, or your own). Add and delete your own in ⚙ Settings › Prompts, or type a
  message and pick *＋ Save the message as a prompt*.
- **Hand off.** `⇢` sends the selected agent's last answer to another agent, for example to let
  Codex review Claude's plan (the text is editable in ⚙ Settings › Prompts).
- **Finish a worktree.** In Projects, select a worktree and press `⎇ Finish worktree`. It shows the
  branch, the commits not yet in the main branch, uncommitted files and running agents. It then
  closes the workspace and removes the checkout, but only when nothing is uncommitted or
  running. The branch is always kept.
- **While you were away.** If the Navigator was closed for more than 10 minutes, it opens with
  what changed: agents that ask something, agents that finished (each with the first line of its
  answer), and sessions that ended. Enter jumps there.
- **Phone alerts** (⚙ Settings › Phone alerts). You get a message when an agent has waited on you
  for `blocked_minutes`, and the result of the logon restore. Any mix of channels works:
  **Telegram** (create a bot with @BotFather and paste its token: the Navigator checks it, opens
  your bot in Telegram, waits for you to press Start and sends a confirmation into the chat), **ntfy** (free app, a private topic),
  **Discord / Slack / Teams / Mattermost** (an incoming-webhook URL) and **WhatsApp** (through
  the free, unofficial CallMeBot relay). Send test checks every channel. Messages carry only a
  session's name, its project and how long it waited.
- **Usage tab** (`5`). Tokens per project and session for today and the last 7 days, and how much
  of that was output, read incrementally from the Claude and Codex transcripts.

Questions an agent is waiting on (Claude `AskUserQuestion`, Codex `request_user_input`) show up
in **Agents** as `❓` with the question and its options, also for sessions in other windows.

Always visible in herdr:

- **Tab bar:** `⚠ other-project waiting for you (Ctrl+Alt+I) │ project: 2 working, 1 idle │ 3 sessions outside herdr (F3) │ F1 Navigator · F2 Sessions · F3 Agents · F6 Layout · F9 Settings · Ctrl+Alt+R Back` (most urgent first, plain words)
- **Sidebar, spaces:** one group per repo. The repo's own checkout comes first (its second
  line shows the branch, e.g. `main`), and the active worktrees are indented under it, named
  after the session working there. `◐2 ○1` counts agents by state (working, idle, …).
  `↗ NAME` lists sessions in other windows that have no mirror (only when mirrors are off).
- **Sidebar, agents:** the session name, its state, running sub-agents (`↳2`) and, on the
  second line, `project ⎇ worktree` and the CLI. `↗ other window` marks a mirror.

Direct chords (no prefix) that setup adds:

| Keys | Action |
|---|---|
| `Ctrl+Alt+H/J/K/L` | Focus pane left / down / up / right |
| `Ctrl+Alt+N` / `P` | Next / previous tab |
| `Ctrl+Alt+D` / `U` | Next / previous workspace |
| `Alt+1..9` | Workspace N |
| `Ctrl+Alt+A` / `Ctrl+Alt+Shift+A` | Next / previous agent, blocked first |
| `Ctrl+Alt+O` | Agent that sent the last notification |
| `Ctrl+Alt+G` / `W` | Goto picker / workspace picker |
| `Ctrl+Alt+V` / `S` · `Z` · `C` · `B` | Split right / down · zoom · new tab · sidebar |

`Ctrl+Alt` is AltGr on many European keyboards, so chords that type characters there
(`Q E M 2 3 7 8 9 0 + <`) are avoided. On Windows, `F1`/`F2` inside herdr replace PowerShell's
own F1/F2 shortcuts (command help, prediction view).

## How it knows

| Signal | Source |
|---|---|
| Agents in herdr | herdr's socket API (`herdr api snapshot`) |
| Claude sessions in other terminals | `~/.claude/sessions/<pid>.json`, kept only while that process is alive (`busy`/`shell` → working, `idle`) |
| Codex sessions in other terminals | a top-level rollout written in the last ~2 minutes |
| Sub-agents | `…/<session>/subagents/agent-*.jsonl` + `.meta.json` (Claude); child threads (Codex), both written in the last ~2 minutes |
| Resumable sessions | Claude `~/.claude/projects`, Codex `~/.codex/sessions`, pi `~/.pi/agent/sessions`, Qwen `~/.qwen/projects`, Gemini `~/.gemini/tmp`, Copilot `~/.copilot/session-state` (head and tail only, cached); OpenCode / Kilo `~/.local/share/{opencode,kilo}/*.db` and Hermes `~/.hermes/state.db` (read-only SQLite). Each row carries a coloured `[cli]` tag. Droid, Amp, Cline and Cursor have launch/resume templates but no session reader yet. |
| Projects and worktrees | the nearest `.git` directory; a `.git` *file* points a linked worktree at its main repo |

Apart from mirror tabs and the names and labels it reports to herdr, the Navigator changes
only what you click. Nothing leaves your machine. Pane operations use herdr's CLI or its
socket API (`pane.focus`, `layout.*`). Presets always build a *new* tab, so no running pane
is ever rebuilt.

## Settings

**Updates** (⚙ Settings › Updates) checks GitHub for a newer version, lists what changed and
updates with one click. It only fast-forwards: local changes or local commits stop it, so
nothing of yours is overwritten. Reopen the Navigator afterwards.

The common settings are in the Navigator itself (⚙ Settings, key `6` or `F9`). Everything else is in
`navigator.toml` in the plugin's config directory (`herdr plugin config-dir momatthias.navigator`;
⚙ Settings › General › *Open settings file* opens it). In it you can:

- pin or rename projects,
- change how far back sessions go,
- set directories to hide (temp folders are hidden by default),
- turn workspace auto-naming off,
- stop worktrees from opening automatically in the sidebar,
- turn mirror tabs off (`[sidebar] mirror_outside = false`),
- *sidebar width* lives in herdr's own config (`[ui] sidebar_width`); the shortcuts above edit it for you,
- change the launch commands (for example `claude --dangerously-skip-permissions --resume {id}`),
- tune the startup restore (`[restore]`: sessions per lane, window, cap, delay, Claude name / Remote Control, terminal) and the sign-in commands (`[login.<cli>]`).

## Develop

```sh
git clone https://github.com/TheMoMatthias/herdr-navigator && cd herdr-navigator
python bootstrap.py                      # .venv with Textual + tomlkit
herdr plugin link "$PWD"
herdr plugin action invoke momatthias.navigator.setup
.venv/bin/python -m pip install pytest && PYTHONPATH=. .venv/bin/python -m pytest -q
```

`run.cmd app resume` (Windows) or `./run.sh app resume` runs the UI outside herdr's popup.

## License

MIT
