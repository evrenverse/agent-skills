# Security

This repository holds two kinds of code. The skills are Markdown instructions: the agent that loads one carries them
out with that agent's own permissions. The command-line tools in `bin/` and the scripts beside two skills are Python
programs that run as you. The plugin also declares one Claude Code hook, which runs `test-queue hook` before every Bash
call; it is described with `test-queue` below. This document lists what each of them may do, what it reads, writes,
runs and sends, and how to narrow what spawned agents can do.

Paths below use the defaults: `~` is your home directory, Claude Code transcripts are found under `~/.claude/projects/`,
under every `~/.config/claude-rotate/configs/*/projects/` and under `$CLAUDE_CONFIG_DIR/projects/`, Codex transcripts
under `~/.codex/sessions/` and `~/.codex/archived_sessions/`, Antigravity transcripts under `~/.gemini/antigravity*/`.

## Agents the skills start

Spawns run without permission prompts. That is the team default: a detached agent in a prompting mode waits for
answers nobody gives. On Claude Code the mode is `bypassPermissions`, on Codex and Antigravity `full-access`. Such an
agent can run commands and change files as you, without asking; Claude Code still asks before the few actions that no
mode approves on its own.

| Skill | Starts | Mode | Push and merge |
| --- | --- | --- | --- |
| `phandoff` | one implementer, detached from the calling session | `bypassPermissions` (Claude, the default; also a Claude Code provider the team configured), `full-access` (Codex, Antigravity) | Told to push its branch after every task commit, merge into `main` and push `main`; where the repository's `AGENTS.md` forbids pushing, it merges locally and pushes nothing |
| `pspawn` | one agent, detached | `bypassPermissions` (Claude), `full-access` (Codex) | No instruction to push or merge, and nothing technical stops it |
| `padvisor` | one agent, a child of the calling session | `full-access` (Codex, the default), `bypassPermissions` (Claude) | Told to change no file; that is an instruction, not a mode |
| `pcommittee` | two agents, children of the calling session | `bypassPermissions` (Claude member), `full-access` (Codex member) | Told to change no file; that is an instruction, not a mode |

`pagent`, `pprofile`, `ptakeover`, `continue` and `review-spec-and-plan` start no agent.

The text a skill hands over goes to the spawned agent's model provider through that provider's login on your machine,
and so does every file the agent then reads. `padvisor`'s default advisor and `pcommittee`'s second member are Codex
agents: their briefing and the files they open reach OpenAI even when you started from Claude Code.

What the skills tell the calling agent to do besides spawning:

- `phandoff` commits the calling session's own spec and plan (named paths only), searches the session's Claude Code
  transcripts for the planning transcript to pass its path on, and detaches the implementer with `paseo agent detach`.
- `ptakeover` writes a claim file `~/.paseo/takeovers/<id>.json` (owner, time, reason, one line of the task), stops the
  predecessor with `paseo stop`, and archives it with `paseo archive` once the work is done, after detaching its
  children.
- `pcommittee` writes the shared prompt to a new file from `mktemp` per run, readable only by you.
- `padvisor` and `pcommittee` archive their agents when the question is answered.
- `review-spec-and-plan` writes unambiguous additions into the spec and plan it reviews and asks before anything else.
- `continue` re-dispatches the calling session's own subagents that died with it; it starts no paseo agent.

## Tools and scripts

None of these programs reads credentials, a secret store or another process's environment, calls an undocumented
endpoint or sets a User-Agent. None starts, stops or restarts the paseo daemon, and no skill tells an agent to
(`phandoff` and `pspawn` forbid `paseo restart`). When the daemon does not answer, `paseo-limit-watch` skips the pass,
`paseo-notify` reports the ended watch and `paseo-title` logs the failed rename.

The three paseo tools run `paseo` (which talks to the local paseo daemon), `claude-rotate` and `claude`, each found on
`PATH`, then in `~/.local/bin`, then in the newest Node under `~/.nvm`.

### `paseo-limit-watch`

| | |
| --- | --- |
| Reads | agent records `~/.paseo/agents/*/<id>.json`; the last megabyte of a Claude Code agent's transcript; whether `~/.paseo/takeovers/<id>.json` exists (never its content); the names of the markers in `~/.paseo/notify/`; its state file; with `--reload`, `~/.paseo/config.json`, of which it uses only `agents.providers.claude.command` |
| Writes | `$XDG_STATE_HOME/paseo-limit-watch/state.json` (default `~/.local/state/paseo-limit-watch/state.json`), replaced atomically; removes the `~/.paseo/notify/` markers it handles. Log lines go to stdout. `--dry-run` writes, removes and sends nothing |
| Runs | `paseo ls`, `paseo inspect`, `paseo logs` (agents without a Claude Code transcript), `paseo usage ls --refresh` (a Codex limit without a time), `paseo send`, `paseo agent reload` (`--reload` only); `claude-rotate status --json` (a Claude model limit without a time, and `--reload`); after a resume, a detached watch process (the code of `paseo-notify`) for each agent that was waiting |
| Network | none of its own. `paseo usage ls --refresh` has paseo refresh the account quotas, and `claude-rotate status` may query its accounts' quotas |
| Sends | to a blocked agent, one fixed line: "Continue where you left off — unless you already did after the limit reset; then ignore this message."; to an agent waiting through `paseo-notify`, why nobody resumes the agent it waits for, with up to 200 characters of the limit message, sent the way `paseo-notify` sends |

It never sends to an agent that was taken over or archived (`paseo send` would unarchive it), and it sends with
`--behavior steer`, so a turn someone started in the meantime is not interrupted. `--reload` restarts the agent's
process, which ends whatever the process runs; that is why it is opt-in and skipped while a turn runs or the
transcript shows background tasks.

### `paseo-notify`

| | |
| --- | --- |
| Reads | `paseo inspect` of both agents; agent records; once the agent stops, its last message (from the Claude Code transcript, else `paseo logs`) and the last megabyte of its transcript, to see whether background tasks are still out |
| Writes | `~/.paseo-notify.log` (created with mode 600); `~/.paseo/notify/<agent>--<caller>` when the agent stopped on a usage limit; `~/.paseo/notify/locks/<agent>--<caller>`, an empty lock file that exists while the watch runs |
| Runs | one detached background process that blocks in `paseo wait` (at most seven days; while background tasks are out it checks once a minute, for at most three hours), then `paseo inspect`, `paseo logs` and `paseo send` with `PASEO_AGENT_ID` set to the watched agent, which paseo takes as the sender (if paseo refuses that, once more without it) |
| Network | none of its own |
| Sends | one message into the caller's conversation, as an agent message from the watched agent: the agent's id, title and status and up to 3 000 characters of its last activity. From there it goes to the caller's model provider with the rest of that conversation. Never to an archived caller |

### `paseo-title`

| | |
| --- | --- |
| Reads | every agent record `~/.paseo/agents/*/*.json`; the opening of each live agent's transcript (at most 4 000 lines), for Claude Code, Codex and Antigravity agents alike |
| Writes | no file; a temporary empty directory for the model call, removed afterwards. Log lines go to stdout |
| Runs | `claude -p --model haiku --output-format json --tools "" --strict-mcp-config --no-session-persistence --setting-sources ""` (through `claude-rotate run` when claude-rotate is installed, else the first `claude` found) from that temporary directory with the prompt on stdin; `paseo agent update <id> --name <title>` |
| Network | none of its own; `claude` sends the prompt to Anthropic |
| Sends | see below |

**Which agents.** An agent qualifies when its conversation has three user messages and its title is exactly what
paseo itself generates from the first message: the first non-empty line, whitespace collapsed, at most 60 characters.
A title a person or a skill set is left alone unless it is exactly that text. If paseo changes how it derives titles,
no title matches any more and the tool simply stops renaming.

**What goes to Anthropic.** For each qualifying agent, `paseo-title` sends about 2 000 characters of the
conversation's opening (the first user message, the first longer answer, the next two user messages, each cut short;
prose only, never the tool calls or tool output in between) and the name of the project folder, inside a fixed
instruction to write a title. It goes to Claude Haiku through your own Claude Code login: on the account
`claude-rotate run` picks when claude-rotate is installed, else on whichever account the `claude` it runs is logged in
to. Normally that happens once per agent: after the rename the title is no longer paseo's own. The agent is asked
again on the next run when the rename fails, when Claude gives no answer or an unusable one, or when the answer equals
the current title, which is possible only when the first line is 15 characters or shorter (the cap on a new title).
`--dry-run` sends the excerpt too. It applies to every agent, whatever provider it runs on: the openings of Codex and
Antigravity conversations reach Anthropic as well, a recipient those conversations would not otherwise have. If that
is not acceptable, do not run `paseo-title`; nothing else calls it.

The flags and the empty working directory keep the call from loading tools, MCP servers, hooks, your settings and the
agent's repository, and from saving a session. What still runs despite them: Claude Code's built-in system prompt and
its built-in plugins, without hooks. Managed policy settings of your organization still apply. `paseo-title` passes its
own environment on unchanged.

### `test-queue`

| | |
| --- | --- |
| Reads | its configuration file (`$XDG_CONFIG_HOME/test-queue/config.json`, default `~/.config/test-queue/config.json`); the holder and ticket files of the state directory; the environment variables `TEST_QUEUE_SLOTS`, `TEST_QUEUE_DIR` and `TEST_QUEUE_HOLDER`; with `hook`, whether the configuration file exists, the environment variables `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`, `CLAUDE_CONFIG_DIR` and `CLAUDE_PROJECT_DIR`, the Bash call that Claude Code puts on stdin (command, timeout, working directory, permission mode), the user settings (`$CLAUDE_CONFIG_DIR/settings.json`, else `~/.claude/settings.json`) and the `.claude/settings.json` and `.claude/settings.local.json` under `$CLAUDE_PROJECT_DIR`, the git root and the main checkout's root for their Bash deny and ask rules, and the output of `git -C <working directory> rev-parse --path-format=absolute --show-toplevel --git-common-dir` (the two roots) |
| Writes | the state directory (`$TEST_QUEUE_DIR`, else `$XDG_STATE_HOME/test-queue`, default `~/.local/state/test-queue`) and its `waiting/` directory, both created with mode 700, as is any missing parent directory (a directory that already exists keeps its mode); in them the lock files `slot-<n>.lock` and `waiting/<pid>.lock` (mode 600); while a command runs, its holder file `slot-<n>.json`; while a call waits, its ticket `waiting/<pid>.json`. Holder files and tickets are written through a temporary `.tmp` name and follow the umask, not mode 600; they hold the label (by default the working directory's name and the first 60 characters of the command), the working directory, the pid and the start time. A call removes its own holder file or ticket when it ends, a SIGTERM, SIGINT, SIGHUP or SIGQUIT included. After a SIGKILL they stay: a reader ignores a holder file whose lock is free and the slot's next holder overwrites it, and the next waiting call or `status` removes a dead waiter's ticket and lock file. The slot lock files are never removed. `status` creates the directories and the lock files too, and so does `run` when it checks whether it runs inside a held slot. When `run` cannot create or lock these files, it fails open: it prints one line to stderr and runs the command unqueued. `hook` writes no file |
| Runs | `run`: the wrapped command, unchanged and without a shell, in its own session (so its own process group) with the call's environment plus `TEST_QUEUE_SLOT=<label>` and `TEST_QUEUE_HOLDER=<the call's pid>`, or without both when it fails open; SIGTERM, SIGINT, SIGHUP and SIGQUIT are passed on to that group, unless the call ignored them when it started (nohup). `status` runs nothing; `hook` runs only that `git rev-parse` (2 s timeout), and only for a command it would otherwise wrap |
| Network | none |
| Sends | nothing. `hook` prints its answer to Claude Code on stdout |

**The hook.** `hooks/hooks.json` makes Claude Code run `test-queue hook` before every Bash call of a session with the
plugin enabled. It rewrites a command only once its configuration file exists (a file holding `{}` is enough; without
it the hook prints nothing), only in `bypassPermissions` sessions (its answer is "allow", which would skip the
permission prompt of any other mode), only when the command matches one of the configuration's `patterns` outside its
quoted strings and here-document bodies, and only by prefixing `test-queue run --label … -- bash -c`, with the original
command as bash's one argument. It also sets the call's timeout: a background call gets at least 7200000 ms (two
hours); a foreground call keeps its own, unless `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` is set to anything but `0`,
which raises it to at least 600000 ms. A pattern that does not compile is skipped; any other error, a malformed payload
included, gives no output and exit code 0, so the call runs as it was. Codex sessions are not wrapped.

**A command that a Bash deny or ask rule may match is not wrapped.** Claude Code checks permission rules against the
command the hook returns, and a Bash rule does not match inside `bash -c '…'`: Claude Code's
[permissions page](https://code.claude.com/docs/en/permissions) says such a rule "isn't a security boundary around the
program". So, for a command it would wrap, the hook reads the Bash rules of `permissions.deny` and `permissions.ask` in
the files named in the table above (the user settings, and the project settings under `$CLAUDE_PROJECT_DIR`, the git
root and the main checkout's root, which differ in a worktree), and prints nothing when one may match the command: the
call runs unqueued and the rule applies as without the hook. The match is wide on purpose (the text of a rule before its
first `*` found anywhere in the raw command, quotes and here-documents included; a bare `Bash` or an empty prefix
matches everything). A settings file that exists but cannot be read or parsed, or whose `deny` or `ask` is not a list,
also means: do not wrap. The hook does **not** read managed settings, rules passed with `--settings` or rules passed
with `--disallowedTools` on the command line: a wrapped command stays outside those. If you rely on them, do not create
the configuration file, or set `"patterns": []` in it: the hook then wraps nothing.

### Skill scripts and `install.sh`

| Program | Reads | Writes | Runs | Network |
| --- | --- | --- | --- | --- |
| `skills/pagent/pagent.py` | agent records and transcripts | nothing | nothing | none |
| `skills/pprofile/pprofile.py` | agent records, one Claude Code transcript with its subagent transcripts and their `.meta.json`; the size (only) of files that transcript read | nothing | nothing | none |
| `install.sh` | the `bin/` directory | symlinks in `~/.local/bin` (replaces only symlinks, never a regular file) | `ln`, `mkdir` | none |

Both scripts print what they read. When an agent runs them, that output becomes part of its conversation and goes to
its model provider.

## Restricting spawned agents

- **A rule in `AGENTS.md`.** Write "never push" (or "never push to `main`") into the repository's `AGENTS.md`. The
  `phandoff` kickoff makes the implementer read it and skip the pushes the rule forbids; for `pspawn`, `padvisor` and
  `pcommittee`, repeat the rule in the task text. Agents are told to follow it; nothing enforces it.
- **`permissions.deny` for Claude Code agents.** Deny rules in the repository's `.claude/settings.json` hold in
  `bypassPermissions`. In the words of Claude Code's
  [permission modes documentation](https://code.claude.com/docs/en/permission-modes): "Deny rules block in every mode,
  including `bypassPermissions`." For example:

  ```json
  {
    "permissions": {
      "deny": ["Bash(git push *)", "Bash(gh pr merge *)"]
    }
  }
  ```

  Bash rules match the command as written, so treat them as a guard rail, not a sandbox. The `test-queue` hook leaves a
  command alone that a rule in the user or project settings may match; managed settings, rules passed with `--settings`
  and rules passed with `--disallowedTools` do not see a wrapped command (see [`test-queue`](#test-queue)).
- **On the remote.** Branch protection or a ruleset on `main` stops a push however the agent phrases it.
- **A prompting mode.** You can spawn in a mode that asks before it acts. A detached agent then waits until someone
  answers its request (`paseo permit ls`).

## Reporting a vulnerability

Please report it privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability):
on the repository page, open the security tab and choose **Report a vulnerability**. Do not open a public issue for
it.
