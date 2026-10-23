---
name: ptakeover
description: "Use when the work of ANOTHER paseo agent has to be taken over and carried to the end — after it hit a usage limit or spend cap, crashed, errored out, or got stuck. Triggers on /ptakeover, 'take over the agent', 'finish what it started', 'the agent hit its limit'. For resuming your OWN interrupted work use continue; for merely reading an agent use pagent."
---

# ptakeover (adopt another agent's assignment)

## Overview

An agent stopped mid-assignment — limit, spend cap, crash, or it simply went in circles.
This skill moves the assignment to you: reconstruct what was asked, claim it so nobody
restarts the predecessor behind your back, verify what actually landed, and finish.

**The assignment survives; the agent does not.** You are not resuming its session, you are
adopting its job. Read what the user asked, not what the predecessor planned to do about it.

| Skill | Job |
| --- | --- |
| `pagent` | Look at an agent and its history. Reading only. |
| `continue` | Resume **your own** work after a limit hit this session. |
| **`ptakeover`** | Take over **another agent's** assignment and finish it. |
| `paseo-limit-watch` | Wakes limited agents back up — the thing step 2 must switch off. |

## Invocation

`/ptakeover <agent-id>` — id, id prefix, or part of the title (`pagent` resolves all three). Step 1
resolves it; from step 2 on, `<id>` is the full id on pagent's `id` line, because the watcher looks for the
claim marker by the full id.

## Steps

**1. Read the whole assignment, not the first line of it.**

```bash
PA=<base directory of this skill>/../pagent/pagent.py
python3 $PA <id> --task            # every instruction the user gave
python3 $PA <id> -t -n 40 --full   # how far it got, and how it died
```

`--task` prints the opening request **and every later correction**. Those corrections are
usually where the real decisions are: "use option B, but keep the retry limit at three" is
the assignment, the opening `/code-review 7` is only its subject. Taking over on the
opening line alone re-opens questions the user already answered — the single most annoying
way to take over a job.

Note the **kind of work** too. A review stays a review; do not turn it into an implementation
because implementing feels more productive.

**2. Claim it — before you touch a single file.**

```bash
mkdir -p ~/.paseo/takeovers
cat > ~/.paseo/takeovers/<id>.json <<EOF
{"owner": "$PASEO_AGENT_ID", "at": "$(date -Iseconds)", "why": "spend cap", "task": "<one line>"}
EOF
```

`paseo-limit-watch` (run from cron, see the README) wakes limited agents the moment their
window resets. Without this file it will wake your predecessor while you are working
its job — two agents, one task, one of them overwriting the other. The watcher checks the
file's **existence**, never its content, so a half-written marker still blocks the wake-up.
The content is for the human who reads it a week later.

Claim first, work second. Doing it the other way round leaves a window exactly as long as
your investigation, which is when the reset usually lands.

**3. Stop the predecessor if it is still alive.**

```bash
paseo stop <id>     # interrupts a running agent, no-op for an idle one
```

**Not `paseo archive` — not yet.** Archiving cascades to child agents, and a spawned
sub-agent may still hold work you are about to need (or be writing to the same worktree).
Stop the one agent now, leave the rest standing; archiving is step 7, once the job is done.

**4. Trust the disk, not the transcript.**

A predecessor that says "I published the review" usually did. One that said it was *about
to* did not. Check the artifacts themselves:

```bash
git -C <repo> log --oneline -10 && git -C <repo> status --short
git worktree list                       # it may have left one; work there, don't start a second
gh pr view <n> --json reviews,state     # a published review must not be published twice
python3 $PA <id> -t -n 40 --full | grep -i "error\|failed\|limit"
```

Anything already published — a review, a PR, a comment, a merge — is **done**. Redoing it
externally is visible to other people and cannot be taken back.

**5. Orient in three sentences, then work.**

> Assignment X. Done: Y. Open: Z. I'm taking it from here.

Then work. Do not ask the user questions the transcript already answers — that is what
step 1 was for. Existing approvals carry over as given; they are not a license for anything
broader ("approved the review" ≠ "approved merging the branch").

**6. Finish and prove it.**

The predecessor's claims are not evidence. Run the tests, read the published artifact, show
the command output. Then say what you finished and what you deliberately left — including
anything that turned out to be already done.

**7. Archive the predecessor — once the work is actually done.**

A finished takeover otherwise leaves the old agent lying around: it shows up in every
`paseo ls` and invites the next person to restart a job that
is already merged. So archive it — after step 6, never before:

```bash
# the cascade would take child agents with it — make them independent first (full id in the label)
for a in $(paseo ls -g --background --label "paseo.parent-agent-id=<id>" -q); do paseo agent detach "$a"; done
paseo archive <id>        # soft-delete; --force is only needed for a running agent
```

Nothing is lost: `python3 $PA <id>` still reads an archived agent, because the JSON stays on disk
and the transcript is untouched. The claim marker stays too — archiving already hides the
agent from `paseo ls -g --background --json`, which is the watcher's only source, but the marker is what
still holds if someone un-archives it later.

**Takeover called off, or stopped at an open question?** Then there is nothing to archive.
The predecessor stays where it is: stopped and claimed.

## When NOT to use this

- **Your own session hit the limit** → `continue`. No claim, no stop, nothing to adopt.
- **You only want to know what an agent is doing** → `pagent`. No marker file.
- **The predecessor is healthy and working** → leave it alone. Two agents on one task is the
  failure this skill exists to prevent, not a way to go faster.

## Releasing a claim

Only if the takeover is called off and the predecessor should carry on:

```bash
rm ~/.paseo/takeovers/<id>.json
```

Once the work is finished there is nothing to release — the marker is what keeps a
superseded agent from waking up months later and redoing a merged job, and it outlives the
step-7 archive if anyone un-archives the agent.

## Common mistakes

| Mistake | Fix |
|---|---|
| Reading only the opening request | `--task` prints every correction; the later ones carry the decisions |
| Working first, claiming second | The watcher wakes the predecessor during exactly that window |
| `paseo archive` at claim time instead of `paseo stop` | The cascade kills child agents whose work you may still need — archive in step 7 |
| Leaving the predecessor open after a finished takeover | It resurfaces in every `paseo ls`; archive it once step 6 is proven |
| Believing "I published X" | Check the artifact: `gh pr view`, `git log`, the file itself |
| Re-publishing a review or PR that already exists | External and visible to others; verify before you post |
| Re-asking questions the user already answered | They are in `--task`, verbatim |
| Turning a review into an implementation | Take over the assignment, including its kind |
| Treating an existing approval as broader permission | It covers what it says and nothing more |
| Starting a second worktree next to the predecessor's | `git worktree list` first, then work in the one that exists |
