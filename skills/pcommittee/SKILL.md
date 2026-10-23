---
name: pcommittee
description: "Use when stuck, looping, or tunnel-visioning on a hard problem — forms a committee of two contrasting paseo agents that analyse it in parallel, argue out their disagreement, and converge on a plan. Triggers on \"/pcommittee\", \"committee\", \"we are going in circles\", \"root cause still unclear after several attempts\", \"have two models look at this\". Analysis only; the committee never edits files."
---

# pcommittee (two-agent root-cause committee)

## Overview

Two agents, two model families, the same problem, fresh context on both sides. They analyse in
parallel; you make them argue where they disagree; you synthesize what survives. Then YOU implement —
the committee never touches a file.

**When it earns its cost:** you have been round the same loop twice, the bug has no obvious root
cause, or a plan is expensive enough to be worth a second and third opinion before it is built. Two
`xhigh` agents are not cheap. For one opinion use `padvisor`; for handing work away use `pspawn` or
`phandoff`.

**What the spawned agents may do.** Both run without permission prompts as the team default
(`bypassPermissions` on Claude, `full-access` on Codex), but they are read-only by instruction, not by
mode: the prompt's no-edits suffix keeps them from changing files. A team that wants more can ask for it in
the repository's `AGENTS.md`, which is an instruction as well, or block it with Claude Code
`permissions.deny` rules, which still apply in `bypassPermissions`.

## Invocation

`/pcommittee <problem>` — the default pair is below; members the user names replace it.

## Composition

Contrast is the mechanism, not a nicety. Two agents from the same family produce the same blind spot
twice and call the agreement confirmation.

The default committee pairs **claude** and **codex**; both must show `available` and `Enabled` in
`paseo provider ls`:

- **Member A:** the newest Opus that `paseo provider models claude` lists (for example
  `claude/claude-opus-5-5`) · thinking `xhigh` · mode `bypassPermissions`
- **Member B:** Codex's newest model, the first id in `paseo provider models codex` · thinking `xhigh` ·
  mode `full-access`

Read the ids from those lists, never from memory. The claude member takes the plain id, which is the 1M
model (pspawn says why no `[1m]`). Both members run without permission prompts, as every spawn does (team
default); the no-edits suffix, not the mode, keeps them from changing files.

If the user names members, use those. If only one family is available, say so and run `padvisor`
instead of pretending a one-member committee is a committee.

## Steps

**1. Write ONE problem prompt.** Both members get the identical text — differing prompts make the
disagreement an artifact of your wording instead of a real split.

**Write it to a file of its own and pass `"$(cat <prompt file>)"` to both spawns.** Typing the same
heredoc twice is how the texts silently drift apart; a file makes identity structural instead of
careful. Create the file with `mktemp` and use the path it prints as `<prompt file>`: a fixed name in
`/tmp` collides with another session's committee and is readable by every user, a `mktemp` file is new
for this run and readable only by you.

Open with the answer language, the one the user's conversation is in (`Answer in <language>.`) — the
members answer in whatever language the prompt is in, and a conversation in one language with two
reports in another is friction you pay for at synthesis time.

```
[The language of the user's conversation, as an instruction: "Answer in <that language>."]

## The problem
[What is wrong or undecided. Symptoms, not your theory — your theory is what you want tested.]

## What is known
[Facts, measurements, error messages, verbatim. Not conclusions.]

## What has been tried
- [Approach] — [what happened, why it was abandoned]

## Where to look
- `/absolute/path/file.ts` — [what it is and why it matters]

## What I want back
A root-cause analysis and a plan. State your confidence, and name what would falsify your diagnosis.
If the problem as framed is the wrong problem, say that first.

This is analysis only. Do NOT edit, create, or delete any files. Do NOT write code.
```

Keep your own hypothesis OUT of the prompt, or you get it back twice in different words. If you must
mention it, mark it as a hypothesis to be attacked.

**2. Spawn both as children of this session.** Provider availability first, exactly as pspawn's
step 1 (`<this skill's base directory>/../pspawn/SKILL.md`). Then, in the same response so they think
in parallel:

```bash
paseo run --no-wait --json --provider '<claude/newest-opus>' --thinking xhigh \
  --mode bypassPermissions --title "[Committee A] <topic>" "$(cat <prompt file>)"
paseo run --no-wait --json --provider '<codex/newest>' --thinking xhigh \
  --mode full-access --title "[Committee B] <topic>" "$(cat <prompt file>)"
```

Under paseo both land in this session's workspace by themselves, as this session's children — and
stay that: **do not detach them**. The committee belongs to this question; archiving this session
archives them too, which is what should happen. Outside paseo add `--workspace <id>`, looked up once
as in pspawn's step 2.

**3. Wait for both — one `paseo-notify` per member, then end your turn.**

```bash
paseo-notify <id-a>; paseo-notify <id-b>
```

Each member's message arrives on its own. If one is in and the other is still out, end your turn
again; the second message wakes you. `finished its turn` → it answered. `is waiting for a
permission` → a request is waiting on you. `stopped with an error` → say so and read its logs; do
not re-spawn silently. A usage limit does not wake you: `paseo-limit-watch` resumes the member and
re-arms the watch, where its cron line is installed (see the README); without it, check the member
yourself after the reset. Members routinely run 15–30 minutes. Never poll, never `sleep`, never send
hurry-ups.

Outside paseo (no `$PASEO_AGENT_ID`): `paseo wait <id-a> --json; paseo wait <id-b> --json` in ONE
bash call with `run_in_background: true`, and read each `status` from the JSON, never the exit code.

**On `permission`** (rare in bypass and full-access, but an `rm -rf` still asks) the member stalls
with nobody watching. Read it (`paseo permit ls`), decide whether it is inside the mandate you gave,
then `paseo permit allow <agent-id> <request-id>` or `deny` (both ids from `paseo permit ls`), and arm
`paseo-notify` again. Outside the mandate or unclear → ask the
user, do not approve for them.

**4. Read both.**

```bash
paseo logs <id> --filter text --tail 3
```

`pagent.py <id> -t -n 3 --full` (`<this skill's base directory>/../pagent/`) is richer, with
timestamps, and reads the Claude and the Codex member alike.

**5. Make them argue.** This is the step that makes a committee worth two agents — skipping it
leaves you with two reports and no resolution.

Find where they actually diverge — a different root cause, a different fix, a different risk
assessment. Ignore differences in wording and emphasis; those are not disagreements.

For each real split, send each member the OTHER's argument:

```bash
paseo send --behavior steer --no-wait <id-a> "Member B concludes <X>, on these grounds: <verbatim reasoning>. Address it directly: where is B wrong, or where were you? Change your position if B is right — say so plainly if you do."
paseo send --behavior steer --no-wait <id-b> "Member A concludes <Y>, on these grounds: <verbatim reasoning>. Address it directly: where is A wrong, or where were you? Change your position if A is right — say so plainly if you do."
```

Long verbatim reasoning is easier to pass as a file: write the message and use
`paseo send --behavior steer --no-wait --prompt-file <file> <id>`. If the provider cannot accept
steering, it can fall back to interruption. `paseo send` revives an archived agent — check
`ArchivedAt` in `paseo inspect <id> --json` first; do not revive archived members.

**`--no-wait` is mandatory here.** Plain `paseo send` BLOCKS until the agent finishes — two blocking
sends run the members sequentially instead of in parallel, and the first one alone busts the
10-minute foreground Bash cap. Fire both non-blocking, then arm both watches again (step 3) and read again
(step 4). Two rounds is normally enough. **If they still
disagree after two rounds, stop and report the disagreement as the result** — a genuine unresolved
split between two strong models is a real finding about the problem, and manufacturing consensus by
sending a third round destroys it.

**6. Synthesize for the user:**

- What both agreed on — this is the load-bearing part
- Where they split, and how it resolved (or that it did not)
- Your own reading, marked as yours
- The plan you would now follow

**7. Archive both** — `paseo archive <id>` each — unless the user wants to keep questioning them.

## Common mistakes

| Mistake | Fix |
|---|---|
| Two members from the same provider family | Contrast is the mechanism — claude + codex |
| Different prompt per member | One file, `"$(cat …)"` into both spawns — two heredocs drift |
| Answer language not stated | Members answer in the prompt's language; open with the user's |
| Your own hypothesis stated as fact in the prompt | Leave it out, or mark it as a hypothesis to attack |
| Skipping the argue round | Two reports are not a committee — step 5 is the value |
| Forcing consensus in round three | An unresolved split between two strong models IS the finding |
| Trusting `paseo wait`'s exit code | It is 0 for idle, permission and error alike — read `status` from `--json` |
| Foreground wait | `run_in_background: true`; the 10-minute Bash cap kills the wait, not the agents |
| Codex member left in its default mode, or claude in `auto` | codex → `full-access`, claude → `bypassPermissions` (team defaults); a member in its default mode stalls on prompts nobody answers |
| Polling / hurry-up messages | `paseo-notify` per member and end your turn; members reason for 15–30 minutes |
| Plain `paseo send` in round two | It BLOCKS until done — `--no-wait` for both, then step 3 |
| No-edits suffix omitted | Both members start implementing the thing you asked them to analyse |
| Detaching the members, or `env -u PASEO_AGENT_ID` | They are this session's children on purpose: they go when it is archived |
| Running a committee on a small question | `padvisor` — one agent, one opinion, a fraction of the cost |
