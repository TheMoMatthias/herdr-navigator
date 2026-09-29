# herdr-navigator

A [herdr](https://herdr.dev) plugin that gives you one clickable, keyboard-driven screen for
everything your coding agents are doing:

* **Which project am I in?** Workspaces are named after their git repo. Worktrees fold into
  their repo and show up as indented children in the sidebar.
* **What is running where?** Every Claude Code and Codex agent is listed per project and
  worktree, including sessions running in *other* terminal windows and their live
  **sub-agents**, each with what it is doing right now (`⚙ Bash: run tests`, `💬 …`).
* **How do I get back in?** Every Claude Code and Codex session from the last 45 days can be
  searched and resumed with one click, in the right project, worktree and tab.
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
| `F2` · `Ctrl+B › U` | **Resume.** Search every Claude/Codex session and open it in its project |
| `F3` · `Ctrl+B › I` | **Agents.** Every agent, inside herdr or not, with its current activity |
| `F4` · `Ctrl+B › /` | **Keys.** Your live key bindings |

Inside the Navigator:

- **Click a row once** to select it; **click it again** (or press Enter) to open it.
- **Click ☐** (or press Space) to show a project in herdr's sidebar, and click ☑ to hide it.
  The sidebar holds only the projects you tick. A ticked repo's active worktrees open as
  indented children of the repo's workspace, and **F5** picks up worktrees that became active
  since then.
- Hiding a project never kills anything. A workspace that still has an agent or a running
  command stays open, and the Navigator tells you why.
- `c` / `x` starts a new Claude / Codex agent in the selected project or worktree.
- A session marked **⧉ outside** is running in another terminal. The Navigator shows it but
  won't resume it a second time, because two copies would fork the conversation.

Always visible in herdr:

- **Tab bar:** `F1 ☰ Navigator │ ▣ project ◐2 │ ⚠ other-project waits: Ctrl+Alt+A │ ⧉3 outside herdr`
- **Sidebar, spaces:** the agent counts per workspace, plus `⧉n outside` for that repo or worktree.
- **Sidebar, agents:** `project ⎇ worktree`, the number of running sub-agents (`↳2`) and the
  session title.

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
| Claude sessions in other terminals | `~/.claude/sessions/<pid>.json`, kept only while that process is alive |
| Sub-agents | `…/<session>/subagents/agent-*.jsonl` + `.meta.json` (Claude); child threads (Codex), both written in the last ~2 minutes |
| Resumable sessions | `~/.claude/projects/**/*.jsonl` and `~/.codex/sessions/**/rollout-*.jsonl` (head and tail only, cached) |
| Projects and worktrees | the nearest `.git` directory; a `.git` *file* points a linked worktree at its main repo |

Everything is read-only apart from what you click. Nothing leaves your machine.

## Settings

The settings file is `navigator.toml` in the plugin's config directory
(`herdr plugin config-dir momatthias.navigator`). In it you can:

- pin or rename projects,
- change how far back sessions go,
- set directories to hide (temp folders are hidden by default),
- turn workspace auto-naming off,
- stop worktrees from opening automatically in the sidebar,
- change the launch commands (for example `claude --dangerously-skip-permissions --resume {id}`).

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
