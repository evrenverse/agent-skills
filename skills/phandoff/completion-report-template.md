# Completion-report template for /phandoff
#
# Language: the labels and section titles below are English. Write the report in the language the
# kickoff names as {LANGUAGE} and translate every label and title into it; the placeholders in braces
# say what goes into a cell.
#
# Order: what the user must know or do comes first, how the run went second. Every fact appears
# ONCE: no row or section repeats another, and no sentence before the table restates the outcome.
# The rows "Open for you" and "Not verified" are the report's closing lines; do not end with
# them again.
# Fill EVERY row; use "—" ONLY where a row genuinely does not apply to this run. All times in
# local system time (the machine's own timezone) — never raw UTC; pprofile prints UTC in its
# per-agent blocks, so convert the start and end you cite.
# The cells marked {pprofile row} come from `python3 {SKILLS_ROOT}/pprofile/pprofile.py $PASEO_AGENT_ID`
# (`{SKILLS_ROOT}` is the path the kickoff gives), run as the last step before writing this report.
# The labels of those rows are pprofile's own and its values are copied as printed — never estimated. A row
# pprofile cannot measure (a Codex or Antigravity run, or a run it exited on without numbers) holds
# "not measured", translated like the labels.
# Placeholder the calling kickoff sets: {OUTCOME}. Everything else the agent fills; {PLAN} is the
# plan's title as its heading names it (e.g. "Plan 8 · Invoice export").

## {PLAN} — Completion Report

| Metric | Value |
|---|---|
| Outcome | {OUTCOME} · {`main` <old> → <new>, pushed} |
| Open for you | {each decision or action that needs the user, as **ID:** a few words · …; else "—"} |
| Not verified | {what was not verified, and the biggest open risk; else "—"} |
| Test evidence | {the proof the repo's implementer section names — e.g. the last line of the check on the merged result, exit 0} |
| E2E run | {local e2e specs run IN ADDITION to the check, with counts; else "—"; never a CI run against the branch} |
| Migration | {per database: applied count, run from the primary checkout · seed ran or unchanged; else "—"} |
| Tasks · Findings | {N/Y} · {found / fixed / escalated} |
| TODOs | {closed of the plan's list, else "—"} · {N new (below)} |
| **Run** | |
| Lead | {`provider/model · thinking`} |
| Duration (wall-clock) | {pprofile row} ({START HH:MM} → {END HH:MM} local) |
| Gaps > 5 min | {pprofile row} |
| API calls (lead) | {pprofile row} |
| Cache read (lead) | {pprofile row} |
| Subagents | {pprofile row} · {count per model, e.g. opus 23 · sonnet 38} |
| Polled calls | {pprofile row}; target 0 |
| Whole-file reads of large files | {pprofile row}; target 0 |
| Tokens total | {pprofile row} |
| Commits | {N} ({N without merges}) |

### Rulings
- **D1:** {the ruling, in one sentence}. Cost: {what it costs if wrong, or "none"}

### New TODOs
- **{id}:** {title}
- **Left open:** {the plan's TODOs left open, each with why}

### Good to know
- {anything else the user or the next plan should know: interruptions, cleanup, where the deviations are recorded, where this report was sent}

<!--
{OUTCOME} → "merged ✓"  |  "blocked ⤴ (escalated to user)". When blocked, the blocker is the first
item of "Open for you", with the concrete question.
Rulings: every ruling made, numbered D1, D2 … in the order made, one line each. A
ruling that needs the user also appears in "Open for you", by its number only.
Polled calls = identical tool calls from the third on; whole-file reads = Read without limit or
cat on files over 100 KB.
A section with nothing to say holds "—". The repository's implementer section in CONTRIBUTING.md may
name further rows (e.g. a rule count); add them to the upper block. Mention that section only when
the repository has one.
-->
