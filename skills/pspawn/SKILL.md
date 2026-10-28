---
name: pspawn
description: "Use when any task should go to a standalone paseo agent in the CURRENT workspace — discussion, research, analysis, ops, debugging. The agent stands on its own: it survives this session being closed or archived. Triggers on /pspawn, 'spawn an agent', 'give this to an agent', 'hand this to a background agent'. For handing a committed spec + plan to an autonomous implementer, use phandoff instead."
---

# pspawn (standalone agent handoff)

## Overview

Start a paseo agent that is **detached**: it lives in the current workspace, has no parent, and survives
this session ending. Use it for anything that is not "implement a committed plan".

**Not phandoff.** `phandoff` hands a committed spec + plan to an implementer that works the whole plan and
merges to main itself. Use that one for implementation and nothing else. `pspawn` carries a task in plain
words — discuss, research, measure, decide, operate — and prescribes no workflow.

**What the spawned agent may do.** It runs without permission prompts as the team default
(`bypassPermissions` on Claude, `full-access` on Codex), so it can run any command and edit any file you
can, but pspawn gives it no mandate to merge or push; put whatever it must not do (push, `gh`, deleting)
into the task text, let the repository's `AGENTS.md` carry a team-wide no-push rule, and note that Claude
Code `permissions.deny` rules still apply in `bypassPermissions`.

## Invocation

`/pspawn [provider/model] [thinking] <task>` — model and thinking optional.

Defaults: the newest Opus that `paseo provider models claude` lists (for example `claude/claude-opus-5-5`)
· thinking `xhigh` · mode `bypassPermissions`. Codex → mode `full-access`. Claude → `bypassPermissions`,
never `auto`.

**The model id is the one `paseo provider models claude` lists, with the 1M window it lists.** An agent
working a long task must have the 1M window: the short-context variant runs out mid-task and fails
silently, forgetting earlier work. When the list shows one entry per model, as it does for
`claude-opus-5-5`, that plain id IS the 1M model. A `[1m]` suffix the list does not show does harm: paseo
reads the model Claude Code reports only at the end of a turn, so through the whole first turn the app
shows the raw id and no thinking selector. A model that has a separate `[1m]` entry in the list takes that
entry. Never construct an id the list does not show. A wrong id does not fail: Claude Code still answers,
only warns `unrecognized_model` in its log and assumes a 200k window. Read ids and thinking levels from
`paseo provider models <provider> --thinking`, never from memory.

## The one thing that must not be forgotten

```bash
paseo agent detach <new-id>
```

**Detach right after the spawn.** Under paseo, `PASEO_AGENT_ID` names this session, so `paseo run` places
the new agent in this session's workspace and records this session as its parent. Archiving a parent
archives every child in the same workspace whose tab is not open on some client — a background pspawn is
exactly that child, so it would be closed mid-task. `paseo agent detach` removes the parent label (the one
thing the cascade follows) and leaves the agent running where it is.

Archiving the WORKSPACE still archives every agent in it, parent or not; nothing protects against that.

## Steps

**1. Check the provider is actually available.**

```bash
paseo provider ls   # the provider must show BOTH `available` and `Enabled`
```

`paseo run` without `--provider` fails outright with "Provider is required" — it has no default. And a
provider that is listed but `unavailable` fails at spawn time. Stop and name the available ones rather than
guessing.

**2. Pick the workspace — only when this session does NOT run under paseo.**

Under paseo (`$PASEO_AGENT_ID` set) pass no `--workspace`: the agent lands in this session's workspace. In
a paseo terminal `$PASEO_WORKSPACE_ID` does the same. Anywhere else, look the workspace up by the repo
root; there is none → stop and tell the user. Never spawn without one: `paseo run` would mint a throwaway
workspace that is not the user's.

```bash
WS=$(paseo workspace ls --json | python3 -c "import json,sys
root=sys.argv[1]; print(next((w['workspaceId'] for w in json.load(sys.stdin) if w.get('cwd')==root), ''))" \
  "$(git rev-parse --show-toplevel)")
```

`paseo workspace ls` lists active workspaces only, and `--workspace` refuses an archived one with
"Workspace not found" — stop and tell the user then; never edit `~/.paseo/projects/workspaces.json`. The
daemon holds that registry in memory and overwrites any hand edit with its next write.

**3. Spawn.**

```bash
paseo run --no-wait --json \
  --provider 'claude/claude-opus-5-5' \
  --thinking xhigh \
  --mode bypassPermissions \
  --title "<short topic>" \
  "$(cat <<'TASK'
<the task, in plain words>
TASK
)"
```

Outside paseo add `--workspace "$WS"` from step 2.

Single-quote `provider/model` always — a `[1m]` in another model's id is a glob character in zsh and dies
unquoted. The heredoc delimiter `TASK` must not appear at column zero inside the body.

Every spawn runs without permission prompts (team default): claude in `bypassPermissions`, codex in
`full-access`. A detached agent in `auto` waits on prompts nobody answers. A calling session in auto mode
may have its classifier refuse the bypass spawn as "Create Unsafe Agents"; then stop and tell the user —
they switch the session to bypass or run the spawn line themselves. Never fall back to `auto`. Bypass asks
before nothing, so whatever the agent must not do goes into the task text.

**Never let paseo create the isolation.** Pass none of `--new-workspace` / `--worktree-mode` /
`--worktree-slug` / `--new-branch` / `--base` / `--branch` / `--pr-number` — an agent that needs isolation
creates its own worktree (`superpowers:using-git-worktrees`), and paseo's lands under `~/.paseo/worktrees/`
as a second, competing one. If the agent will work in a worktree of its own and builds on work of this
session that is not committed yet, commit that first, by named paths; never stage another session's files.
Otherwise it shares this checkout: name the files it must not touch.

**The workspace decides the directory, not `--cwd`:** with a workspace the agent starts in the workspace's
path and `--cwd` is ignored. For a task in this session's repo — the normal pspawn case — the two coincide.
If you ever point a pspawn at a different repo, **say the absolute working directory in the task text
itself** and have the agent `cd` there first.

**4. Detach and verify, every time** (under paseo; outside there is no parent to remove):

```bash
paseo agent detach <new-id>
paseo inspect <new-id> --json | grep -i parent   # must report ParentAgentId: null
```

The id is `agentId` in the `paseo run --json` output, in full. If the detach fails, say so — never silently
delete and respawn work that is already underway. **Never `paseo restart`** to fix anything: it tree-kills
every running agent on the machine, including the ones other sessions are waiting on.

## Writing the task

The agent starts with nothing but this text and the repo. It cannot see this conversation.

- **Point at the written state first.** A handover file, a runbook section, a spec — "read X, then Y" beats
  any summary you could paste, and it stays true after the conversation is gone.
- **Say what is already decided and must not be re-opened.** Especially anything destructive or
  irreversible that has been switched off on purpose: name it, say it stays off.
- **Say what kind of work it is.** "This is a discussion, bring options and numbers, build nothing before
  the direction is settled" produces something else entirely than a task that reads like an order to
  implement.
- **Language:** the conversation in the user's language, code and commits in English.
- **Talking to it later:** `paseo send <id> "<text>" --behavior steer --no-wait`. Steering can fall back to
  interruption if the provider cannot accept it; `--no-wait` returns after dispatch, not after the turn.
  `paseo send` revives an archived agent, so check `ArchivedAt` in `paseo inspect <id> --json` first: leave
  a report with its work or tell the user instead.
- **Waiting for its result,** only if this session needs it: `paseo-notify <id>`, then end your turn. One
  message arrives when the agent finishes, errors or needs a permission; a usage limit is left to
  `paseo-limit-watch`, which re-arms the watch where its cron line is installed (see the README). Arm it
  again after every `paseo send`. The agent sends no progress reports, and nobody watches it.

## Common mistakes

| Mistake | Fix |
|---|---|
| `env -u PASEO_AGENT_ID` to spawn without a parent | Since paseo 0.13 a run with neither a caller nor `$PASEO_WORKSPACE_ID` mints a throwaway workspace per agent. Keep the variable, spawn, then `paseo agent detach` |
| No `paseo agent detach` after a spawn under paseo | Archiving this session would archive the agent mid-task. Detach, then check `ParentAgentId: null` |
| `--workspace` omitted outside paseo | Look it up by the repo root (step 2); a throwaway workspace is not the user's |
| Editing `~/.paseo/projects/workspaces.json` to revive an archived workspace | Never — the daemon overwrites hand edits. Stop and tell the user |
| `provider/model` unquoted | Single-quote it — a `[1m]` globs in zsh |
| `provider/` inside `--model` | One `--provider 'claude/claude-opus-5-5'`, no separate `--model` |
| `claude/claude-opus-5-5[1m]` | The plain id is the 1M model; the suffix only hides the model and thinking in the app until the first turn ends |
| `--mode auto` on claude, or codex in its default mode | claude → `bypassPermissions`; codex → `full-access` (team defaults) |
| Spawning for an implementation task | That is `phandoff` — it carries the spec/plan and merge contract this skill deliberately lacks |
| No `--provider` at all | `paseo run` has no default and fails with "Provider is required" |
| Provider listed but `unavailable` | `paseo provider ls` first; stop and name the available ones instead of guessing |
| `--new-workspace` / `--worktree-mode` / `--new-branch` | Never — the agent makes its own worktree if it needs one |
| Assuming `--cwd` places the agent | The workspace wins. Name the absolute working directory in the task text and have the agent `cd` there |
| `paseo restart` to fix a spawn | Never — it tree-kills every running agent |
| Watching the agent: `sleep`, Monitor, `/loop`, `paseo ls`/`logs`/`inspect` in a loop, a watcher script | `paseo-notify <id>` and end your turn |
| A worktree agent spawned before this session's own work is committed | Commit it by named paths, never another session's files; a worktree branches from `main` HEAD |
