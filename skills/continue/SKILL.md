---
name: continue
description: "Use when resuming work that was interrupted by a usage or rate limit in Claude Code or Codex — after an account rotation, a session resume, or when the user types /continue, especially if subagents were mid-flight when the limit hit. Triggers on /continue, 'keep going', 'pick up where you left off'. For taking over ANOTHER agent's assignment, use ptakeover."
---

# continue (resume after a limit interruption)

## Overview

A usage-limit cutoff is a mid-flight crash. Only the conversation, todos, git and files survive. This skill
finds the stopping point, re-dispatches what died with the session, leaves alone what lives on in separate
processes, and finishes the unfinished tail. It resumes **your own** work; another agent's assignment is
`ptakeover`'s job.

## Invocation

`/continue` — no arguments. It also applies without the command whenever a session resumes after a limit.

## Steps

1. **Find the stopping point** — read the conversation tail, the todo or plan list, plan files, and
   `git status` / `git log --oneline -10`.
2. **Re-dispatch killed subagents.** An **in-process subagent** (Agent tool) died with the session and
   cannot be resumed. Output returned and landed (commit/file/edit) = done, skip it. No final result, or
   only the dispatch is visible = re-dispatch the same task fresh. For a parallel batch, only re-run the
   members whose output never landed.
3. **A paseo agent is not a subagent — check before you redo its work.** `pspawn` and `phandoff` agents are
   separate processes: they survive this session's limit and are often still running or idle with their
   work intact. The `pagent` skill (`pagent.py <id>` from its base directory) says which. Re-dispatching
   one of those duplicates work that already exists — and if it really is dead, adopting it belongs to
   `ptakeover` (which also stops the limit watcher from waking it up underneath you), not to a fresh
   dispatch. If you need its result, `paseo-notify <id>` and end your turn.
4. **Skip finished work.** Only redo the unfinished tail — never restart the plan from task 1 just because
   the session restarted.
5. **Finish.** Continue to completion; don't ask "where was I?" when the trail is clear.

## Common mistakes

| Mistake | Fix |
|---|---|
| Restarting the plan from task 1 | Redo only the unfinished tail; git and the todo list say what landed |
| Re-dispatching a subagent whose output already landed | Check the commit, file or edit first; landed means done |
| Treating a `pspawn` or `phandoff` agent as a dead subagent | It is a separate process; read it with `pagent` before touching it |
| Re-dispatching a dead paseo agent as a fresh task | Adopt it with `ptakeover`, which also silences the limit watcher |
| Waiting for a live paseo agent with `sleep`, Monitor, `/loop` or repeated `paseo ls`/`logs`/`inspect` | `paseo-notify <id>` and end your turn |
| Asking the user "where was I?" | The conversation tail, todos and git answer it; ask only when they do not |
