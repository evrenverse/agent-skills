---
name: review-spec-and-plan
description: "Use when a spec and implementation plan have just been written (e.g. via superpowers brainstorming / writing-plans) and need a final completeness, consistency and session-coverage pass before implementation starts — or when the user invokes /review-spec-and-plan or asks whether everything discussed actually made it into the spec and plan. Triggers on /review-spec-and-plan, 'did everything make it into the spec and plan', 'check the spec and plan'."
---

# review-spec-and-plan

## Overview

A finished spec + plan is a hand-off artifact: it must hold everything decided this session, with nothing
lost, nothing contradictory, no gap left for the implementer to guess. This skill is a final audit pass
before implementation — **hunt for gaps, do not rubber-stamp.** "Looks complete" is only valid after an
actual walk-through. It is tool-neutral and provider-agnostic and runs under any agent.

## Invocation

`/review-spec-and-plan [spec-path] [plan-path]` — paths optional; default to the spec + plan just written
this session (or the newest matching pair under the project's spec/plan dirs).

## Steps

Check these four axes, in order:

1. **Session coverage** — Walk the *entire* conversation. Every decision, constraint, chosen option,
   rejected alternative, preference and "we should also…" raised this session must appear in the spec or
   plan. List anything discussed but not captured.
2. **Completeness / gaps** — What was never raised at all? Unhandled edge cases, error paths, migrations,
   seeds, tests, config/flags, rollout, and questions left undecided. Name them.
3. **Spec ↔ plan consistency** — Every spec requirement maps to plan task(s); every plan task traces back
   to the spec; no contradictions in scope, naming, ordering, data shapes or interfaces between the two
   documents.
4. **Opportunistic pickups** — Cleanups, small code improvements and adjacent bugfixes that genuinely lie
   on the path of this work. Propose each as *optional*; never silently widen scope.

Then report:

- Findings grouped by the four axes; give each a concrete, specific proposed fix (not "consider adding
  error handling" but *which* path, *what* handling).
- **Be honest, not performative.** The value is finding what's missing, not confirming the plan is fine. If
  a section is genuinely solid, say so plainly and state what you checked — don't pad with invented
  concerns either.
- Fold confirmed, unambiguous additions straight into the spec/plan files. For anything that's a real
  judgment call or a scope decision, surface it and get a yes before writing.

## Common mistakes

| Mistake | Fix |
|---|---|
| Declaring "looks complete" without walking the conversation | Walk it end to end; name what you checked |
| Padding the report with invented concerns | Report real gaps only; say plainly which sections are solid |
| Vague fixes ("consider adding error handling") | Name the path and the handling |
| Silently widening scope with a pickup | Propose it as optional and wait for a yes |
| Writing a judgment call straight into the files | Fold in only unambiguous additions; surface the rest first |
| Checking the spec and the plan separately | Trace every requirement to a task and every task back to the spec |
