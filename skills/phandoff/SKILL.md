---
name: phandoff
description: "Use when planning is finished and a committed spec + plan should go to an autonomous implementer that implements the whole plan and merges to main itself. Triggers on /phandoff, 'hand this to an implementer', 'have this implemented', 'handoff'. Provider-agnostic: an explicit provider/model or a numbered option (2 to 4, listed in the skill) overrides the default, Claude Opus at thinking xhigh in bypassPermissions."
---

# phandoff (autonomous implementer handoff)

## Overview

Spawn an autonomous implementer for a committed spec + plan. It implements the whole plan and merges to
main itself — the built-in reviews of the chosen superpowers execution skill ARE the review, so no
separate gate exists. (That skill owns the review structure — do not restate or pin its stages here; it
changes between superpowers versions.)

**What the implementer may do.** It runs detached and unattended with permission prompts off
(`bypassPermissions` on Claude Code, `full-access` on Codex and Antigravity): it can run any command, edit
any file the user can, create branches and worktrees, push, and merge to main. Its kickoff tells it to
push its branch after every task commit. A repository that wants no pushes says so in its `AGENTS.md`; the
kickoff makes the implementer obey that rule. Claude Code `permissions.deny` rules still apply in
`bypassPermissions`. The permission choice is a team default (see pre-flight step 6).

## Invocation

`/phandoff [provider/model] [thinking] [sdd|native] [plan-path]` — all optional.

**Execution mode (`{MODE}`, superpowers offers two):** `sdd` = subagent-driven
development, `native` = `executing-plans` (the session implements every task itself, one whole-branch
review at the end; about half the cost). An explicit `sdd`/`native` argument wins. Otherwise, when the
planning session already chose one — `writing-plans` asks "which execution approach" at the end of every
plan — carry that choice. Otherwise `auto`: the implementer decides by superpowers' own criteria and
states its choice in its first message. Never pin SDD out of habit; the choice belongs to superpowers'
recommendation or the user.

**Model.** No model/thinking → Option 1 below. The user explicitly saying "option 2" / "option 3" /
"option 4" (with no explicit `provider/model`) → that option; an explicit `provider/model` argument
overrides all four.

### Options

- **Option 1 (default):** the newest Opus that `paseo provider models claude` lists (today
  `claude/claude-opus-5-5`) · thinking `xhigh` · mode `bypassPermissions`. Check the thinking levels with
  `paseo provider models claude --thinking` before spawning.
- **Option 2 (only when the user says "option 2"):** model `claude-gemini/gemini-4-argon-high` · thinking
  `high` · mode `bypassPermissions`. Only where a provider named `claude-gemini` is configured in paseo; if
  it is absent, STOP and say so (pre-flight step 7) — never fall back silently.
- **Option 3 (only when the user says "option 3"):** model `antigravity/gemini-4-argon-high` · no
  `--thinking` · mode `full-access` — Option 2's model in Antigravity's own harness instead of Claude Code.
- **Option 4 (only when the user says "option 4"):** the first id listed by `paseo provider models codex`
  (Codex's newest workhorse) · thinking `xhigh` · mode `full-access`.

**`claude-gemini`** is the Claude Code harness driving a Gemini model through a provider that the team
configured in paseo. The provider's model list must carry the id `gemini-4-argon-high`, or the app shows
the raw id without a thinking selector; check with `paseo provider models claude-gemini --thinking`. Pass
`--thinking high`. The modes are Claude Code's, so it spawns in `bypassPermissions` exactly as `claude/*`
does; superpowers skills ARE visible to the implementer, so the kickoff may rely on them. No `[1m]`
variant exists (the provider sets a 1M window itself). Do not quietly drop the thinking level.

**`antigravity`** is paseo's built-in Antigravity provider driving the installed `agy` CLI (Option 3),
launched with `--add-dir <cwd>`. It needs `agy` to be signed in on this machine — otherwise
`paseo provider ls` shows it `unavailable` and `paseo provider diagnostic antigravity` says "Run `agy`
and sign in" (pre-flight step 7 stops then). Its only mode is `full-access`.
`paseo provider models antigravity --thinking` lists no thinking ids: the level is the id's suffix
(`-low|-medium|-high`), so pass no `--thinking`. superpowers skills are visible to it when they are linked
into `~/.gemini/antigravity-cli/skills/`; its subagent tool is `invoke_subagent`, fix rounds go through
`send_message`. It refuses mid-turn steering as busy: send follow-ups after its turn. `pprofile` parses
only Claude Code transcripts, so the completion report of a Codex or Antigravity run has no measured rows;
the implementer says "not measured" instead of estimating.

**The model id is the one `paseo provider models claude` lists, with the 1M window it lists.** An agent
working a whole plan must have the 1M window: the short-context variant runs out mid-plan and fails
silently, forgetting earlier tasks. When the list shows one entry per model, as it does for
`claude-opus-5-5`, that plain id IS the 1M model. A `[1m]` suffix the list does not show does harm: paseo
reads the model Claude Code reports only at the end of a turn, so through the whole first turn the app
shows the raw id and no thinking selector. A model that has a separate `[1m]` entry in the list takes that
entry. Never construct an id the list does not show. This applies to every Claude spawn this skill makes,
an explicit `provider/model` argument included: drop a `[1m]` the list does not show.

## Steps

### Pre-flight (in the calling session)

0. **Placement: the implementer lands in THIS session's workspace**, no matter which repo the plan
   targets, so the user sees it where they are working. Capture the repo root first, before any `cd`:
   `CALLER_WS="$(git rev-parse --show-toplevel)"`.
   - **Under paseo** (`$PASEO_AGENT_ID` set): pass no `--workspace`. `paseo run` places the agent in the
     caller's workspace by itself, as the caller's child; the spawn step detaches it. Never
     `env -u PASEO_AGENT_ID` instead: without a caller, paseo 0.13 mints a throwaway workspace per agent.
   - **Outside paseo:** look the workspace up by `CALLER_WS`; none → STOP and tell the user. Never spawn
     without one — `paseo run` would mint a throwaway workspace that is not the user's.

     ```bash
     CALLER_WS_ID=$(paseo workspace ls --json | python3 -c "import json,sys
     root=sys.argv[1]; print(next((w['workspaceId'] for w in json.load(sys.stdin) if w.get('cwd')==root), ''))" "$CALLER_WS")
     ```

   `paseo workspace ls` lists active workspaces only, and `--workspace` refuses an archived one with
   "Workspace not found". Never edit `~/.paseo/projects/workspaces.json` to revive one: the daemon holds
   that registry in memory and overwrites hand edits. Stop and tell the user.

1. **Resolve the plan:** an explicit argument wins; otherwise take the newest plan in the repo's plans
   folder (`docs/superpowers/plans/`, or `docs/plans/` where the repo keeps them there) that HAS a
   matching spec in the specs folder beside it (`docs/superpowers/specs/` or `docs/specs/`; topic slug;
   plans without a spec are not handoff-ready — e.g. drive-by plans from parallel sessions). If an
   explicitly given plan has no spec, or no plan-with-spec exists: STOP and tell the user — never spawn
   without both. The plan may live in a **sibling repo** (e.g. session in `repo-a`, plan in `repo-b`): set
   `PLAN_REPO_ROOT` = whichever repo's checkout contains the resolved plan; it becomes `--cwd`.
2. **Detect cross-repo scope:** the plan names files in another repository than `PLAN_REPO_ROOT`. Then
   `{CROSS_REPO}` is non-empty in the kickoff (see Spawn), which makes the implementer read the other
   repository's `AGENTS.md`, create a paired worktree and finish the work in both.
3. **Commit your own uncommitted work:** if the repo — or, for cross-repo plans, the sibling repo — has
   uncommitted changes of THIS session (the spec, the plan, their docs), commit them NOW (atomic
   conventional commits, explicit `git add <paths>`, skip transient/lock files). Never stage another
   session's files: other sessions may work in the same checkout; name them in the report instead. The new
   agent branches from `main` HEAD, and the plan must be on it.
4. **Check for worktree/branch name collisions** (`git worktree list`) in every affected repo.
5. **Resolve the planning-session transcript** so the new agent can look up design rationale. Under
   paseo, `python3 <skills root>/pagent/pagent.py "$PASEO_AGENT_ID"` prints it on its `transcript` line
   (`<skills root>` = this skill's base directory minus `/phandoff`). Otherwise `CLAUDE_SESSION_ID` is not
   exported, and the newest file by mtime is WRONG whenever subagents or parallel sessions write too —
   identify the file by content instead: run `echo "handoff-marker-$RANDOM"`, then in a second command
   `grep -l '<the EXPANDED marker>' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<cwd-slug>/"*.jsonl`
   (`<cwd-slug>` = `$PWD` with every character but letters and digits → `-`, so `/a/.b_c` is `-a--b-c`:
   `pwd | sed 's/[^A-Za-z0-9]/-/g'`). Grep for the expanded value (e.g. `handoff-marker-12345`), never
   the literal `$RANDOM` command text; the single match is the current session. Empty result → re-run the
   grep once (write lag). From a Codex session: the same marker, `grep -rl '<marker>' ~/.codex/sessions/`.
   For an explicitly passed older plan, the current session is not the authoring session: instead grep
   the project transcripts for the plan filename and take the newest match; if none, leave
   `{TRANSCRIPT}` empty.
6. **Resolve the IMPL model:** take the full `provider/model` (argument or Options) and pass it as ONE
   single-quoted `--provider 'provider/model'` — never split the `provider/` prefix into `--model` (paseo
   then rejects it as "model may not exist"). Derive `--mode` from the provider prefix
   (`claude-gemini/*`→`bypassPermissions`, `claude/*`→`bypassPermissions`, `codex/*` and
   `antigravity/*`→`full-access`) and pass `--thinking` (`claude-gemini/*`→`high`, claude and
   codex→`xhigh`) for every provider except `antigravity/*`, whose level is in the model id.
   **Codex always spawns `full-access`** (team default; it passes the spawn classifier). **Claude and
   claude-gemini always spawn `bypassPermissions`** (team default): in `auto` an implementer waits on
   permission prompts nobody answers in a detached run. A calling session in auto mode may have its
   classifier refuse the bypass spawn as "Create Unsafe Agents"; then stop and tell the user — they switch
   the session to bypass or run the spawn line themselves. Never fall back to `auto`. Bypass asks before
   nothing, so a repo's no-push rule is carried by the kickoff alone (its Workspace section).
7. **Provider-availability guard:** run `paseo provider ls`; the impl provider MUST show `available` +
   `Enabled`. **No fallback:** the Option 1 default runs on the `claude` provider; a missing or disabled
   provider — `claude-gemini` for Option 2, `antigravity` for Option 3, `codex` for Option 4, or any one
   the user named — STOPS with a clear message naming the available providers; do NOT spawn.
   `paseo provider models <provider> --thinking` lists the ids and thinking levels the provider actually
   offers; read them from there, never from memory. NEVER `paseo restart` to fix a provider snapshot — it
   tree-kills every running agent. Report the situation and let the user decide.
8. **Overlap warning:** read the plan's header line `**Touches:**` (the areas the plan touches, as paths).
   Then `git fetch`, and for every other plan on main whose `**Touches:**` line shares a path prefix with
   it, ask whether an implementer still works on it:
   `paseo ls -g --label "plan=<that plan's absolute path>" -q` (labels are a filter only — neither
   `paseo ls --json` nor `paseo inspect` prints them). Any open implementer on a shared path prefix is a
   WARNING line in the report, naming both plans and the shared area — never a stop; two plans in the same
   area run one after the other, and that is the user's call. A plan without the header line gets no
   warning and a note.

### Spawn

Fill `kickoff-template.md` beside this SKILL.md (the harness printed the base directory) and emit only the
text below its first `---` line; the part above it is for you. Substitute each placeholder at every
occurrence; a `{NAME: …}` block is emitted only when `NAME` is non-empty:

- `{TOPIC}` — the plan's topic in a few words (also the `<topic-slug>` of the title below).
- `{SPEC_PATH}`, `{PLAN_PATH}` — ABSOLUTE paths of the resolved spec and plan.
- `{WORKDIR}` — `PLAN_REPO_ROOT`, the absolute path of the plan's repository.
- `{CALLER_REPO}` — the caller workspace's path, set ONLY when it differs from `{WORKDIR}`; that block is
  what rescues the cross-repo case from the `--workspace` overrides `--cwd` behaviour.
- `{CROSS_REPO}` — non-empty (the sibling repository's path) when pre-flight step 2 detected cross-repo
  scope, empty otherwise.
- `{TRANSCRIPT_PATH}` — the absolute path of the planning transcript that pre-flight step 5 found.
  `{TRANSCRIPT}` — any non-empty value (`yes`) exactly when that path exists; both are empty otherwise.
- `{MODE}` — `sdd`, `native` or `auto` (Invocation).
- `{SKILLS_ROOT}` — the absolute path of the `skills/` directory this skill lives in (base directory
  minus `/phandoff`); the spawned agent runs on this machine and reads the templates and `pprofile.py`
  from there.
- `{LANGUAGE}` — the language of the planning conversation (for example `German`), taken from the
  user's messages in this session; the implementer writes its status messages and the completion report
  in it. English if the conversation was in English.

Then:

```bash
paseo run --no-wait --json \
  --provider '<resolved impl provider/model>' \
  --thinking <resolved impl thinking; drop the line for antigravity/*> \
  --mode <resolved impl mode> \
  --cwd <PLAN_REPO_ROOT> \
  --title "Impl: <topic-slug>" \
  --label "plan=<absolute plan path>" \
  "$(cat <<'KICKOFF'
<filled phandoff/kickoff-template.md>
KICKOFF
)"
```

Outside paseo add `--workspace "$CALLER_WS_ID"` (pre-flight step 0).

- **Detach right after the spawn — mandatory under paseo:**

  ```bash
  paseo agent detach <new-id>
  paseo inspect <new-id> --json | grep -i parent   # must report ParentAgentId: null
  ```

  The id is `agentId` in the `paseo run --json` output. Spawned from this session, the implementer is
  this session's child, and archiving a parent archives every child in the same workspace whose tab is
  not open on some client — a background handoff is exactly that, closed mid-implementation.
  `paseo agent detach` removes the parent label the cascade follows and leaves the agent running where it
  is. If the detach fails, say so — never delete and respawn work already underway.
- ALWAYS single-quote the `provider/model` on `--provider`.
- The heredoc delimiter `KICKOFF` wraps the filled kickoff template; `KICKOFF` must not appear at column
  zero inside it.
- Never let paseo create the isolation — the spawned agent makes its own worktree as the kickoff says.
  Pass **none** of `--new-workspace` / `--worktree-mode` / `--worktree-slug` / `--new-branch` / `--base` /
  `--branch`: paseo's worktree lands under `~/.paseo/worktrees/` and competes with the one the agent
  creates itself.
- **The workspace decides the directory, not `--cwd`:** the agent starts in the workspace's path and
  `--cwd` is discarded. When the plan lives in the caller's own repo (the common case) the two coincide
  and nothing else matters.
  - **Consequence for a cross-repo handoff:** the kickoff prompt must state the working directory itself,
    as its first instruction, with an absolute path and an order to `cd` there and verify
    (`{CALLER_REPO}` in the template). Without that the agent starts in the caller's repo, reads *that*
    repo's `AGENTS.md` and — if the caller's repo also has `docs/superpowers/` — may read entirely the
    wrong spec. Keep passing `--cwd` anyway (harmless).
  - **Never pass the plan's repo as the workspace:** the agent then sits in a workspace the app cannot
    open. The caller's workspace is app-openable by definition — the user is in it right now.

### After spawning

**Verify the working directory:** `paseo inspect <new-id> --json | grep -i '"Cwd"'` — for a cross-repo
handoff this WILL show the workspace path, not the plan's repo. That is expected; what must then be true
is that the kickoff's first instruction names the absolute working directory. Confirm the agent acted on
it: `paseo logs <new-id> --filter tools` should show an early `cd` into the plan's repo. If it started
reading files in the caller's repo instead, stop it (`paseo stop`, then `paseo archive`) and re-spawn with
the directory instruction made explicit — before it commits anything anywhere.

Report: impl agent ID, title, impl model/thinking, which spec+plan were resolved, the workspace it landed
in (= the caller's current workspace), the overlap warning from pre-flight step 8 if any, and that the
agent implements and merges to main itself. No attach/watch hints.

**If this session follows the implementer** (a repo rule says so, or the user asks): `paseo-notify
<impl-id>`, then end your turn. One message arrives when it finishes, errors or needs a permission; a
usage limit is left to `paseo-limit-watch`, which re-arms the watch (both ship with this plugin in
`bin/`; the watcher only runs where its cron line is installed, see the README; without it, check the
agent yourself after the reset). Never watch it with `sleep`, Monitor, `/loop`, `paseo ls`/`logs`/`inspect`
or a script of your own, and do not have it send you progress reports — the kickoff already forbids
check-ins.

**After the agent has finished:** the `pprofile` skill (`pprofile.py <agent-id>`) gives the measured rows
of its completion report a second time, from the transcript — use it when a report's numbers look
estimated (round "~X.XM" tokens, no API-call count) or when the run should be compared with the previous
ones.

**Follow-up delivery.** `paseo send` revives an archived agent, so check `ArchivedAt` in
`paseo inspect <id> --json` first. Never revive an archived planning session or implementer just to report
back; leave the report with its work (a TODO, protocol or commit), or tell the user. Agent-scoped CLI and
`send_agent_prompt` calls default to steering; an explicit `--behavior steer` also works outside an agent.
`--no-wait` returns after dispatch, not after completion.

## Common mistakes

| Mistake | Fix |
|---|---|
| `env -u PASEO_AGENT_ID` to spawn without a parent | Since paseo 0.13 a run with neither a caller nor `$PASEO_WORKSPACE_ID` mints a throwaway workspace per agent. Keep the variable, spawn, then `paseo agent detach` |
| Prefix in `--model` (`--provider claude --model 'claude/…'`) | Pass ONE `--provider 'claude/claude-opus-5-5'`; never put `provider/` in `--model` |
| `claude/claude-opus-5-5[1m]` | The plain `claude/claude-opus-5-5` is the 1M model; the suffix only hides the model and thinking in the app until the first turn ends |
| Unquoted `provider/model` | Single-quote it; a `[1m]` in another model's id globs in zsh |
| Wrong mode: `auto` on claude, claude-gemini or codex | claude and claude-gemini → `bypassPermissions`; codex → `full-access` (team defaults); antigravity → `full-access`, its only mode |
| Downgrading the thinking level on your own (below `high`) | Option 2 is `claude-gemini/gemini-4-argon-high` with `--thinking high`; Option 3 is `antigravity/gemini-4-argon-high` (the `-high` IS the level), never `-low`/`-medium` |
| Option 3 spawned on `claude-gemini/*` (or Option 2 on `antigravity/*`) | Same model, different harness: Option 2 = Claude Code via the configured provider, Option 3 = agy |
| paseo rejects the model or thinking id | Check `paseo provider models <provider> --thinking`, report what is actually offered, and ask — never silently spawn with a lower tier or a different model |
| Interrupting a running implementer with a follow-up | `paseo send <id> "<text>" --behavior steer --no-wait`; steering can fall back to interruption when the provider cannot accept it |
| Impl agent NOT merging | The implementer MUST finish the branch and merge to main itself; there is no separate review leg to hand it to |
| No `paseo agent detach` after spawning under paseo → archiving the calling session KILLS the running implementer | Detach right after the spawn; verify `ParentAgentId: null` |
| Spawning before this session's spec and plan are committed | Commit them first, by named paths only (pre-flight step 3) |
| Editing `~/.paseo/projects/workspaces.json` or an agent record to fix placement | Never — the daemon overwrites hand edits. An archived workspace makes `--workspace` fail: stop and tell the user; never `paseo restart` |
| The plan's repo as the workspace (agent invisible in app) | The caller's workspace: none under paseo, `CALLER_WS_ID` outside; `--cwd` stays `PLAN_REPO_ROOT` |
| Trusting `--cwd` for a cross-repo handoff (agent starts in the CALLER's repo, may read that repo's `AGENTS.md` and its `docs/superpowers/` specs) | `--workspace` overrides `--cwd`. State the absolute working directory as the kickoff's FIRST instruction, with an order to `cd` and verify; then check `paseo logs <id> --filter tools` for that `cd` |
| Reading "Fixed open delegated agents being archived with their parent" as "the cascade is gone" | It exempts only OPEN tabs and CROSS-workspace children. A background handoff is still archived with its parent — detach it |
| `--new-workspace` / `--worktree-mode` | Pass none of them; the agent creates its own worktree as the kickoff says |
| Following the implementer with `sleep`, Monitor, `/loop`, status polls or a watcher script | `paseo-notify <impl-id>` and end your turn |
