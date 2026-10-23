---
name: pprofile
description: "Use when you need to measure a paseo agent's run from its transcript — wall time, gaps, API calls, tokens, tool wait, polling, whole-file reads — for a completion report or to see where an implementer's time and tokens went. Triggers on /pprofile, 'how many tokens did the agent use', 'where did the time go', 'did the agent poll', 'profile the run'."
---

# pprofile

## Overview

Measured numbers for a run instead of estimates. pprofile reads the Claude Code transcript of a paseo
agent (the main session and every subagent) and prints one block per agent, then the rows that the
completion report of `/phandoff` asks for. It only reads; the transcript is append-only, so profiling a
running agent is safe (its numbers are a snapshot). The script locates the agent and its transcript with
the plugin's `paseo_tools` library, which it imports from the repository's `lib/` directory next to `skills/`.

## Invocation

```bash
SKILL=<this skill's base directory>/pprofile.py   # the harness prints the base directory above

python3 $SKILL 11111111-1111-4111-8111-111111111111   # by agent id
python3 $SKILL 11111111                               # by id prefix
python3 $SKILL "Impl: invoice-export"                 # by title substring, must match one agent
python3 $SKILL ~/.claude/projects/<slug>/<session>.jsonl   # by transcript path
```

`python3 $SKILL --help` prints the usage. A query that matches several agents lists them and exits; give
an id or a transcript path then.

## Steps

1. **Run it on the agent** with one of the forms above. When it lists several agents, run it again with
   the id or the transcript path of the one you mean.
2. **Check that a profile came back.** For a Codex or Antigravity agent it exits with `empty transcript`
   or `no transcript found`: write "not measured" in the report rows and stop.
3. **Read the blocks** with "What it prints" and "Reading the numbers" below. An agent that is still
   running gives a snapshot; profile it again once it has finished.
4. **Paste the completion-report rows** into the `/phandoff` completion report, with the blocks' UTC
   times converted to local system time.

## What it prints

- **Per agent (lead first, then each subagent):** wall time, gaps over five minutes (usually the
  account window), API calls, output / cache-create / cache-read tokens, context average and
  maximum, tool counts, wait per tool, the slowest bash commands, every `Agent` dispatch with model
  and whether it ran in the background. Times in these blocks are UTC.
- **Polling:** identical tool calls from the third repetition on (`git log --oneline -1` every two
  seconds is the classic) and identical assistant texts ("Waiting for task 4").
- **Whole-file reads of files over 100 KB:** a `Read` without `limit`, or a `cat` / `sed -n '1,…'`
  in bash, on a big file such as `docs/big-file.md`. One such read can cost over a hundred thousand
  tokens.
- **Completion-report rows**, ready to paste, in this order (the lead is the agent you profiled):

  | Row | Meaning |
  |---|---|
  | `Duration (wall-clock)` | first to last record of the lead, in hours |
  | `Gaps > 5 min` | total length and number of pauses over five minutes in the lead |
  | `API calls (lead)` | assistant turns of the lead that carry token usage |
  | `Cache read (lead)` | cache-read tokens of the lead |
  | `Subagents` | count, split into implementing, reviewing and other by each subagent's description |
  | `Polled calls` | the lead's identical tool calls from the third repetition on |
  | `Whole-file reads of large files` | lead and subagents together |
  | `Tokens total` | output and cache-read tokens of the lead and all subagents |

## Reading the numbers

- A lead with more than about 400 API calls per plan, or any polled calls, was waiting the expensive
  way: subagents in the background plus `git log`/`sleep`/`ListAgents` loops. The rule is in the
  `/phandoff` kickoff (and in the repository's implementer section in `CONTRIBUTING.md`, where it has
  one): dispatch in the foreground, never poll.
- Cache read dominates cost. One measured run made 3,000 API calls at 600k context, which is 1.8 billion
  cache-read tokens, more than all of that run's subagents together.
- Gaps over five minutes that line up across parallel agents are the shared account window, not
  the agents.

## Common mistakes

| Mistake | Fix |
|---|---|
| Estimating the report rows of a Codex or Antigravity agent | pprofile reads Claude Code transcripts only and exits with a message (`empty transcript` or `no transcript found`) for the others; write "not measured" in those rows |
| Reading a block's `UTC` times as local time | Convert to local system time before they go into a report |
| Passing a title fragment that matches several agents | Use the id, an id prefix or the transcript path |
| Treating the numbers of a running agent as final | They are a snapshot; profile again after the agent has finished |
