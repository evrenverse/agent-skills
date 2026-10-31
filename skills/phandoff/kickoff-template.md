# Impl kickoff template (implement the plan, then merge it into its base branch)

Placeholders (filled by the /phandoff SKILL): `{TOPIC}` `{SPEC_PATH}` `{PLAN_PATH}` `{CROSS_REPO}`
`{TRANSCRIPT}` `{TRANSCRIPT_PATH}` `{WORKDIR}` `{CALLER_REPO}` `{MODE}` (`sdd`, `native` or `auto`)
`{SKILLS_ROOT}` (absolute path of the plugin's `skills/` directory on this machine) `{LANGUAGE}` (the
language of the planning conversation) `{BASE}` (the base branch: the branch the plan's checkout is on,
`main` in the common case). Keep the body free of model names: the execution skill chooses
every subagent's model.

`{WORKDIR}` = absolute path of the plan's repo (`PLAN_REPO_ROOT`). `{CALLER_REPO}` = absolute path
of the workspace the agent will actually start in — set it ONLY when it differs from `{WORKDIR}`,
which is the cross-repo case. `{SPEC_PATH}` and `{PLAN_PATH}` are always ABSOLUTE, never
repo-relative: the agent's starting directory is not necessarily the plan's repo.

Conventions for whoever fills this template:
- `{NAME}` — substitute its value at EVERY occurrence.
- `{NAME: …text…}` — a conditional block (may span multiple lines): when `NAME` is provided
  (non-empty), emit its inner text (placeholders substituted); otherwise omit the **entire** block
  including braces.

The template is repo-neutral on purpose: test commands, ports, database names, migration tools
and the report's extra rows live in the repository's implementer section in `CONTRIBUTING.md`. The
kickoff points there; it does not restate it.

---

{CALLER_REPO: ## Your working directory — fix this before anything else

Your process starts in `{CALLER_REPO}`. **That is the wrong repository** — paseo's `--workspace`
overrides `--cwd`, so the starting directory is not the one this task belongs to. Reading that
repo's `AGENTS.md`, or its `docs/superpowers/` if it has one, would send you down a different
project's path entirely.

Your repository is:

    {WORKDIR}

**First action: `cd {WORKDIR}` and confirm with `git -C {WORKDIR} log --oneline` that you see the
commit carrying this spec and plan.** Use absolute paths, or a `cd` at the start of every shell
invocation. Never create, modify, stage or commit a file under `{CALLER_REPO}`, and pass the working
directory explicitly to every subagent you spawn — they inherit the same wrong-cwd hazard.

---

}You are implementing "{TOPIC}". A finished spec and plan are committed on `{BASE}`, your base
branch. Implement the whole plan end-to-end, fully autonomously. When the plan is fully implemented
and self-reviewed, finish the branch by merging it into `{BASE}` locally yourself (see "Finish the
branch" below).

## Study first — before any code
1. Read the spec COMPLETELY: {SPEC_PATH}
2. Read the plan COMPLETELY: {PLAN_PATH}
3. Read the repo's `AGENTS.md` COMPLETELY. It is binding. {CROSS_REPO: Read the sibling repo's
   `AGENTS.md` BEFORE touching it.}
4. Read the repository's implementer section in `CONTRIBUTING.md`. It carries THIS repo's test
   levels, waiting rule, worktree start, ports, database and the extra rows of the report, and it
   overrides every general habit and every waiting advice of a skill. If the repo has no such
   section, say so in your final report and follow this template.
5. {TRANSCRIPT: If genuinely uncertain about intent, grep the planning transcript at
   {TRANSCRIPT_PATH} by keyword — background only; the committed spec/plan override it.}

On contradictions the spec wins; note the divergence in your final report.

## Workspace
Create an isolated worktree exactly as the repo's rules mandate (a worktree script where the repo
has one, otherwise `superpowers:using-git-worktrees`), branching from `{BASE}` — never work on
`{BASE}` or main directly, never branch in the primary checkout. Push the branch right after
creating it (`git push -u origin HEAD`) and after every task commit: other developers see on
`git branch -r` what is being built, and nothing is lost if this machine dies mid-plan. **Where the
repo's `AGENTS.md` forbids pushing, never push**: commit locally only and skip every push step of
this kickoff, step 3 of the finish included; you run without permission prompts, so nothing asks
before a push. {CROSS_REPO: This plan touches both repos — create the paired worktree with the
SAME name in the sibling repo BEFORE editing it.}

## Waiting — binding
On Claude Code (this overrides the SDD skill's "bounded stretches" advice): dispatch every
subagent in the FOREGROUND (`run_in_background: false`) and wait for its result without doing
anything in between. Send fix-round findings with `SendMessage` to the live implementer and wait
for its reply. Never poll: no `git log`, no `sleep`, no `ListAgents`, no
reading of output files — the harness notifies you when a background command or a subagent
finishes. A command that takes longer than two minutes (install, full check, e2e) runs with
`run_in_background`; waiting means ending your turn, the notification wakes you. Never
`until … sleep` in the foreground, the Bash tool aborts at ten minutes.

A subagent cannot wait for a notification: ending its turn IS its final report. So a subagent only
gets commands that finish in the FOREGROUND within ten minutes (`timeout: 600000` on the Bash
call); tell it that limit and forbid background commands in its brief. Anything longer — the
repo's full check, an e2e run, an install in a fresh worktree — you run yourself, in the
background, and end your turn. The repo's implementer section carries the measurements.

On Codex or Antigravity the two paragraphs above do not apply: a finished subagent does not wake an
idle session there, so never end your turn while subagents are out. Wait the way the execution
skill's tool reference for your platform says (Codex: `wait_agent` in stretches of five to ten
minutes; Antigravity: subagents through `invoke_subagent`, fix rounds through `send_message`).

## Execution
- Execution approach: **{MODE}**. `sdd` → `superpowers:subagent-driven-development` (a fresh
  implementer and a fresh reviewer per task, then a whole-branch review). `native` →
  `superpowers:executing-plans` (you implement every task yourself, then one fresh whole-branch
  review). `auto` → decide the way superpowers' plan handoff does — from
  how much the tasks depend on each other's interfaces, how many there are, and what a shipped
  mistake would cost — and state the choice with its one-sentence reason in your first message.
  Then follow the chosen skill STRICTLY: its built-in review discipline is mandatory, and the skill
  owns the review structure and every subagent's model, reviews included — do not impose or
  restate your own review stages or model choices.
- Report progress on every task-status update as N/Y of total. Write status messages to the user and
  the completion report in {LANGUAGE}; code, commits, identifiers and docs stay in English unless
  the repo's `AGENTS.md` names another language for its documentation.
- Work fully autonomously — no questions, no confirmation pauses, no progress check-ins; the only
  legitimate stops are those the chosen execution skill defines. If that skill ever
  pauses to ask a human partner (e.g. a plan-conflict review before execution), you run DETACHED with
  none — resolve it yourself by the spec-wins rule, record it in your final report, and NEVER block
  waiting for an answer.

## Database ordering (critical — only if the plan adds migrations or seeds)
Migration/seed changes are FILES ONLY during implementation — NEVER apply migrations or run seeds
mid-implementation. You apply them yourself AFTER you merge into `{BASE}` (see "Finish the branch"),
from the checkout where `{BASE}` is checked out (the PRIMARY checkout for main), with the repo's
migration command; check first whether a parallel branch landed a sibling migration (the repo's
implementer section says how) and resolve that before applying.

## Finish the branch — merge into `{BASE}` locally
The chosen skill's built-in reviews ARE the review of this work;
there is no separate review leg. When every plan task is implemented and review-approved by that
discipline (its scoped tests green, the whole-branch review done), finish the branch yourself — do
NOT present the merge/PR/keep/discard menu, just execute:
1. Run `superpowers:finishing-a-development-branch` and choose **Merge back to `{BASE}` locally**:
   `{BASE}` is the base, whatever the skill guesses. Follow the skill with the adaptations of the
   repo's implementer section (which check replaces the skill's "full test suite", where the merge
   happens, what to do when `{BASE}` moved); they win over the rest of this step. Where that section
   names no place for the merge, it happens in the checkout where `{BASE}` is checked out
   (`git worktree list`; the primary checkout for main); never check `{BASE}` out anywhere else. If
   no checkout has it, merge `{BASE}` into your branch and move `{BASE}` forward from your worktree
   with `git fetch . HEAD:{BASE}`.
2. Apply any pending migration from the checkout of `{BASE}` (the PRIMARY checkout for main) with the
   repo's command; run the seed if anything under the seed changed.
3. Push `{BASE}`, unless the repo's `AGENTS.md` forbids pushing. The verification the report cites is the
   proof the repo's implementer section names (e.g. the last line of the check on the merged result,
   exit 0). "Green" without that proof counts as not run.
4. Clean up the worktree and delete the branch.{CROSS_REPO: Do the merge + migration + cleanup in
   BOTH repos with the paired branch name; in the sibling repo the base is the branch its primary
   checkout is on.}

Then measure the run: `python3 {SKILLS_ROOT}/pprofile/pprofile.py $PASEO_AGENT_ID` prints the
rows the report needs (API calls, cache read, gaps, polled calls, whole-file reads, subagent counts).
It reads only Claude Code transcripts: on a Codex or Antigravity run it exits without numbers, and
those rows say "not measured" (translated into {LANGUAGE}) — never an estimate.
Write the Completion Report in {LANGUAGE}, **formatted EXACTLY per
`{SKILLS_ROOT}/phandoff/completion-report-template.md`** (English labels in the template: translate
them) with those measured values (OUTCOME = `merged ✓`, or `blocked ⤴ (escalated to user)` if you hit
the one legitimate stop; it supersedes any other report format for this run). You are then done.
