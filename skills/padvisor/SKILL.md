---
name: padvisor
description: "Use when you want a second opinion on the current task from one separate paseo agent — an outside judgment without handing over the work. Triggers on \"/padvisor\", \"second opinion\", \"what does codex think\", \"have someone double-check this\", \"advisor\". For handing the work itself to an agent, use pspawn or phandoff instead."
---

# padvisor (single-agent second opinion)

## Overview

Spawn ONE agent that reads the situation, forms a judgment, and answers. It does not do the work — you keep
it. Use it when a decision is close, an approach smells wrong, or a plan wants a fresh pair of eyes before
it gets built.

**Not pspawn, not phandoff.** Those hand the WORK away. This one hands away only the QUESTION and brings
back an opinion you then act on yourself.

**What the spawned agent may do.** It runs without permission prompts as the team default
(`bypassPermissions` on Claude, `full-access` on Codex), but it is read-only by instruction, not by mode:
the briefing's no-edits suffix keeps it from changing files. A team that wants more can ask for it in the
repository's `AGENTS.md`, which is an instruction as well, or block it with Claude Code `permissions.deny`
rules, which still apply in `bypassPermissions`.

## Invocation

`/padvisor [provider/model] [--skill /<name>] <question>` — model optional.

**Default provider: the other model family than yours.** From Claude, Codex's newest model, the first id in
`paseo provider models codex`; from Codex, the newest Opus that `paseo provider models claude` lists (for
example `claude/claude-opus-5-5`, the 1M model under its plain id; pspawn says why no `[1m]`). Contrast is
the whole point — an advisor from your own model family agrees with you for the same reasons you were
already wrong. Override only when the user names a model. Read ids from the list, never from memory.

## Steps

**1. Spawn it as a child of this session.** Provider availability first, exactly as pspawn's step 1
(`<this skill's base directory>/../pspawn/SKILL.md`). Then:

```bash
paseo run --no-wait --json \
  --provider '<provider/model>' \
  --thinking xhigh \
  --mode <full-access for codex, bypassPermissions for claude> \
  --title "[Advisor] <topic>" \
  "$(cat <<'BRIEF'
<the briefing, step 2>
BRIEF
)"
```

Under paseo the advisor lands in this session's workspace by itself, as this session's child — and
stays one: **do not detach it**. An advisor belongs to the question; when this session is archived,
paseo archives the advisor with it, which is what should happen. Outside paseo add
`--workspace <id>`, looked up as in pspawn's step 2.

Mode: claude → `bypassPermissions`, codex → `full-access`, as for every spawn (team default): in a default
mode the advisor stalls on prompts nobody answers. The no-edits suffix, not the mode, keeps it from
changing files. Single-quote `provider/model`.

**2. Write the briefing.** The advisor has zero context and cannot see this conversation.

```
[The language of the user's conversation, as an instruction: "Answer in <that language>."]

## The question
[One sharp question. Not "what do you think" — "is X or Y the right call, and why".]

## Situation
[What is being built or decided, in three sentences.]

## What I have considered
- [Option] — [why it appeals, why it worries me]
- [Option I ruled out] — [on what grounds]

## Where to look
- `/absolute/path/file.ts` — [what it is and why it matters]

## What I want back
A recommendation with reasoning. Name the strongest argument AGAINST your own recommendation.
If my framing of the question is wrong, say that first.

This is analysis only. Do NOT edit, create, or delete any files. Do NOT write code.
```

**Point at files by absolute path; do not paste their contents.** The agent can read. Pasting costs
context and goes stale.

The "strongest argument against" line is not decoration — without it a fresh agent reliably produces
a confident one-sided answer, which is the failure mode this skill exists to avoid.

**3. Wait — arm `paseo-notify`, then end your turn.**

```bash
paseo-notify <advisor-id>
```

It returns at once. One message arrives in this session when the advisor stops: `finished its
turn` → it answered (the message carries its last activity lines), `is waiting for a permission` →
a request is waiting on you, `stopped with an error` → say so and read its logs; do not re-spawn
silently. A usage limit does not wake you: `paseo-limit-watch` resumes the advisor and re-arms the
watch, where its cron line is installed (see the README); without it, check the advisor yourself after
the reset. No polling, no `sleep` loop, no Monitor. Finish any other work first, then end your turn.

Outside paseo (no `$PASEO_AGENT_ID`) run `paseo wait <advisor-id> --json` with
`run_in_background: true` instead — a foreground Bash call caps at 10 minutes, an advisor on high
thinking routinely runs 15–30 — and read `status` from the JSON, never the exit code.

**On `permission`** (rare in bypass and full-access, but an `rm -rf` still asks) an unattended
advisor stalls silently. Read the request (`paseo permit ls`), decide whether it is inside the
mandate you gave the agent, then `paseo permit allow <agent-id> <request-id>` or `deny` (both ids from
`paseo permit ls`), and arm `paseo-notify` again. If it is
outside the mandate, or you are unsure, ask the user rather than approving on their behalf.

**4. Read the answer.**

```bash
paseo logs <advisor-id> --filter text --tail 3
```

`pagent.py <id> -t -n 3 --full` (`<this skill's base directory>/../pagent/`) gives a richer view
with timestamps and reads Claude, Codex and Antigravity transcripts alike — and unlike `paseo logs`
it never reloads a closed agent to read it.

**5. Synthesize for the user.** The advisor's verdict AND your own read of it, clearly separated.
You are not a relay: if the advisor is wrong, say so and why. If it found something you missed,
say that plainly too.

**6. Archive it** — `paseo archive <advisor-id>` — unless the user wants it kept (below).

## Persistent advisor

If the user wants ongoing input ("keep it for the next decisions"), skip step 6 and send
follow-ups to the same agent instead of spawning a new one:

```bash
paseo send <advisor-id> "<follow-up question>" --behavior steer --no-wait
```

Then wait (step 3) and read (step 4) as before. If the provider cannot accept steering, dispatch
can fall back to interruption. **`--no-wait` matters:** without it the CLI waits for completion.
`paseo send` revives an archived agent, so check `ArchivedAt` in `paseo inspect <id> --json` first;
tell the user instead of reviving an archived advisor.

Its context carries over, which is the point. Archive when the user says they are done, or when the
topic shifts far enough that stale context hurts more than continuity helps.

## Forwarded skills

If the user passes a skill reference (`/padvisor /code-review is this over-engineered?`), the
advisor — not you — runs it. Pick an advisor whose harness has that skill: a skill that exists only in
Claude Code (such as `/code-review`) needs a claude advisor. Add to the briefing:

```
Use the `<name>` skill before doing anything else (Claude Code: the Skill tool; Codex: read its
SKILL.md), and apply it to this task.
```

Pass anything after the skill name through as that skill's input.

## Common mistakes

| Mistake | Fix |
|---|---|
| Advisor from your own model family by default | Codex's newest model — an agreeing advisor is a wasted spawn |
| Trusting `paseo wait`'s exit code | It is 0 for idle, permission and error alike — read `status` from `--json` |
| Foreground wait | `run_in_background: true`; the 10-minute Bash cap kills the wait, not the agent |
| Polling `paseo ls`, `sleep` or Monitor to see if it is done | `paseo-notify <id>` and end your turn; one message wakes you |
| Plain `paseo send` for a follow-up | It BLOCKS until done — `--no-wait`, then wait as in step 3 |
| Detaching the advisor, or `env -u PASEO_AGENT_ID` | It is this session's child on purpose: it goes when the session is archived |
| No-edits suffix omitted | Without it an advisor starts implementing the thing you asked it to judge |
| Pasting file contents into the briefing | Absolute paths; the agent reads them itself |
| Answer language not stated | The advisor answers in the prompt's language; open with the user's |
| Relaying the verdict as if it were fact | It is one opinion from zero context — say where you disagree |
| Codex left in its default mode, or claude in `auto` | codex → `full-access`, claude → `bypassPermissions` (team defaults); the no-edits suffix keeps it read-only |
| Handing over the actual work | That is `pspawn` (open task) or `phandoff` (committed spec + plan) |
