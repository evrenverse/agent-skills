---
name: pagent
description: "Use when the user names a paseo agent — by id, by id prefix, or by part of its title — and wants to know what it is doing, what it was asked, or what its transcript says. Triggers on \"/pagent\", \"what is agent <id> doing\", \"which agent is running\", \"show me the transcript of\", \"paseo agent\", \"is the agent still running\"."
---

# pagent

## Overview

Find a paseo agent and read its conversation, without re-discovering where paseo keeps things every time.
pagent only reads: it never starts, stops, steers or revives an agent, and it never asks the daemon to
reload a closed one.

## Invocation

```bash
SKILL=<this skill's base directory>/pagent.py   # the harness prints the base directory above

python3 $SKILL 11111111-1111-4111-8111-111111111111   # head: status, cwd, model, transcript path
python3 $SKILL 11111111 --task                        # + every instruction the user gave
python3 $SKILL 11111111 -t -n 20                      # + the last 20 turns
python3 $SKILL 11111111 -t --full                     # turns untruncated
python3 $SKILL "invoice export"                       # search by title
python3 $SKILL                                        # every agent, newest first
```

A query is an id, an id prefix, or a case-insensitive substring of the title. Several matches print as a
list; exactly one prints the detail view. `python3 $SKILL --help` prints the usage.

## Steps

1. **Resolve the agent.** Run the query without flags: the head shows status, working directory, model and
   the transcript path. A list means the query was ambiguous; narrow it with an id or a longer title
   fragment.
2. **Read what it was asked.** `--task` prints every instruction the user gave, not only the first one. The
   opening line names the subject; the corrections two messages later carry the decisions ("use option B,
   but keep the retry limit at three"). Both are printed; harness preamble and injected skill bodies
   are not. This matters most when taking an agent's work over — see the `ptakeover` skill.
3. **Read how far it got.** `-t -n <turns>` prints the end of the conversation; add `--full` when a turn
   was cut off. Reading is safe while the agent runs, because the transcript is append-only.
4. **Check the neighbours.** An agent whose `cwd` matches yours may be editing the same files. Run
   `git status` before assuming an unexpected diff is yours.

With `-t` or `--task`, a missing transcript exits non-zero. A silent `0` would read as "the agent said
nothing", which is the wrong conclusion to hand anyone reconstructing an assignment.

## Where the data lives

- **Agent metadata:** `~/.paseo/agents/<workspace>/<id>.json` — title, `lastStatus` (`running` / `idle` /
  …), `cwd`, model, `requiresAttention`, timestamps.
- **Conversation:** *not* in that file. It lives with the harness that wrote it, keyed by
  `persistence.sessionId`:
  - **Claude Code** — `<config>/projects/<escaped-cwd>/<sessionId>.jsonl`. claude-rotate keeps one config
    dir per account, so it may sit under `~/.claude` or under any `~/.config/claude-rotate/configs/*/` —
    the script globs all of them instead of guessing the escaped path.
  - **Codex** — `~/.codex/sessions/<y>/<m>/<d>/rollout-<stamp>-<sessionId>.jsonl`, a different record shape
    (`payload.type`) that the parser handles alongside Claude's. Archiving an agent moves the rollout to
    the flat `~/.codex/archived_sessions/`.
  - **Antigravity** — `sessionId` is a `plugin:{…"conversationId":…}` wrapper; the history is
    `~/.gemini/antigravity-cli/brain/<conversationId>/.system_generated/logs/transcript_full.jsonl`
    (records keyed by `step_index`). Not `conversations/<id>.db`, that one is protobuf. Agents of the older
    `antigravity-acp` provider never wrote that log, so they have no readable history.

## Talking to an agent, waiting for one

This skill only reads. For the rest:

- **Talk:** `paseo send <id> "<text>" --behavior steer --no-wait`. Agent-scoped CLI and `send_agent_prompt`
  calls default to steering; an explicit behavior also works outside an agent. Steering can fall back to
  interruption when the provider cannot accept it (for example, during compaction). `--no-wait` returns
  after dispatch; it does not queue the message behind the turn.
- **Revive nothing by accident:** `paseo send` revives an archived agent, so check `ArchivedAt` in
  `paseo inspect <id> --json` first. Never revive an archived agent just to deliver a report; leave the
  information with its work (a TODO, protocol or commit), or tell the user.
- **Wait:** `paseo-notify <id>`, then end your turn — one message arrives when it stops. Outside paseo:
  `paseo wait <id> --json`, backgrounded; read `status` (`idle`, `permission`, `error`, `timeout`) from the
  JSON — the exit code is 0 for all of them. Never wait with `sleep`, Monitor, `/loop` or a poll of
  `paseo ls`/`logs`/`inspect`.

## Common mistakes

| Mistake | Fix |
|---|---|
| Taking over on the opening request alone | `--task` prints every later correction too; they carry the decisions |
| `paseo logs` to read a closed or archived agent | It makes the daemon reload the agent's session just to read it; use this skill, which only reads files |
| Reading a silent exit as "the agent said nothing" | A missing transcript exits non-zero; check the exit code and the transcript path in the head |
| Sending a report to an archived agent | `paseo send` revives it; check `ArchivedAt` first and leave the report with its work instead |
| Waiting on an agent with `sleep`, Monitor, `/loop` or repeated `paseo ls`/`logs`/`inspect` | `paseo-notify <id>` and end your turn |
| Treating an unexpected diff as yours when the agent shares your `cwd` | Check `git status`; the agent may be editing the same files |
