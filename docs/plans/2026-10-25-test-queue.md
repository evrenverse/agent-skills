# test-queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One machine-wide queue for heavy test runs, so the agents and hourly CIs of every repository on a machine run
their heavy suites one after another at full power instead of side by side at a fraction, without any repository
calling or knowing it.

**Architecture:** A new command `test-queue` (`bin/test-queue`, code in `lib/paseo_tools/test_queue.py`, standard
library only). `test-queue run -- <command>` waits for one of N slots (lock files held with `flock`, freed by the kernel
when a holder dies), runs the command holding it and exits with its code. A Claude Code `PreToolUse` hook of the plugin
(`hooks/hooks.json` → `test-queue hook`) wraps a Bash command that matches a heavy pattern in `test-queue run`; a cron
line is wrapped by hand with `test-queue run --last`, which lets every other waiter go first for up to an hour.

**Tech Stack:** Python 3.10+ standard library (`fcntl`, `json`, `re`, `shlex`, `signal`, `subprocess`), unittest,
Claude Code plugin hooks.

**Why:** Several repositories' implementers, their checks and their hourly CIs share one development machine. Heavy
suites that run side by side overload its CPU and database: under load they take longer than one after another, and
timing tests and connection limits turn them red. One of these repositories already queues its own heavy runs, but a
queue per repository still lets each of them take the whole machine at once.

## Global Constraints

- The repository's standards hold (`CONTRIBUTING.md`): standard library only, Python 3.10 or newer, type hints and a
  docstring on every public function, paths computed when used (`Path.home()` inside a function), tests in a
  temporary home (`tests.support.TempHome`), no private names, no `version` in a manifest, ruff clean, Conventional
  Commits without AI attribution.
- The tool knows no repository: no project name, path or command of a particular repository in code, defaults, tests
  or documents.
- The hook never breaks a tool call: on any error, unknown input or a command it does not wrap, it exits 0 and prints
  nothing.
- The hook wraps only in a session whose `permission_mode` is `bypassPermissions`. `updatedInput` takes effect only
  with `permissionDecision: "allow"`, which skips the permission prompt; in any other mode the hook would widen what
  runs without asking, so it stays out.
- State under `$TEST_QUEUE_DIR`, else `$XDG_STATE_HOME/test-queue`, else `~/.local/state/test-queue`; configuration in
  `$XDG_CONFIG_HOME/test-queue/config.json`, else `~/.config/test-queue/config.json`.
- Finish with a pull request from `feat/test-queue`; never push to `main` (`AGENTS.md`).

## Review Focus

1. A command with quotes, `$`, backticks, newlines, a heredoc or `cd … &&` must run exactly as written once wrapped,
   in the same working directory (Task 2 pins it).
2. A holder or waiter killed by `SIGKILL` must never block the queue: its slot frees with its lock, its ticket is
   dropped once its process is gone (Task 1 pins it).
3. The Bash tool's timeout or an interrupt kills the wrapper: the command must die with it, never run on outside the
   queue (Task 1 pins `SIGTERM` and `SIGINT`).
4. A heavy command run inside a held slot (a check that starts a suite) must not wait for a second slot and deadlock
   (Task 1 pins `TEST_QUEUE_SLOT`).
5. Malformed or empty hook input, a non-Bash tool, a missing `tool_input` field: no output, exit 0 (Task 2 pins it).

## Lanes

- **In parallel:** Task 1 and Task 2. Task 2 builds the wrapped command as a string around `test-queue run` and needs
  none of Task 1's code; both add to `lib/paseo_tools/test_queue.py`, Task 2 below a `# The hook` section line, so
  the controller lands them in either order.
- **Last:** Task 3, then Task 4.

---

### Task 1: The queue: `test-queue run` and `test-queue status`

**Files:**
- Create: `lib/paseo_tools/test_queue.py`, `bin/test-queue`
- Modify: `tests/test_bin.py` (`COMMANDS` gains `"test-queue"`, as its entry-point test lists `bin/` exactly)
- Test: `tests/test_test_queue.py`

**Interfaces:**
- Produces:
  - `state_dir() -> Path`, `config_path() -> Path`
  - `@dataclass Config: slots: int = 1; patterns: list[str] = DEFAULT_PATTERNS; last_wait_max_s: int = 3600`
  - `load_config() -> Config` (missing or broken file → defaults; `TEST_QUEUE_SLOTS` overrides `slots`, below 1 → 1)
  - `@dataclass Entry: label: str; pid: int; cwd: str; since: float; last: bool`
  - `run(command: list[str], label: str, last: bool = False, config: Config | None = None) -> int`
  - `status(config: Config | None = None) -> tuple[list[Entry], list[Entry]]` (holders, waiters in queue order)
  - `main(argv: list[str] | None = None) -> int` with subcommands `run [--label L] [--last] -- <command…>`, `status`,
    `hook`; the label defaults to the working directory's name and the command's first 60 characters.
  - Environment of a running command: `TEST_QUEUE_SLOT=<label>`.

- [ ] **Step 1: Write the failing tests** in `tests/test_test_queue.py` (classes derive from `TempHome`; commands are
  short `python3 -c` scripts that write marker files and sleep at most 1 s):
  - `test_second_run_waits_for_the_first`: two `test-queue run` processes, one slot; the second's command starts only
    after the first's ended (marker timestamps).
  - `test_two_slots_run_two_at_once`: `TEST_QUEUE_SLOTS=2`; both commands overlap.
  - `test_exit_code_passes_through`: `run -- python3 -c "raise SystemExit(7)"` returns 7.
  - `test_last_lets_a_later_waiter_go_first`: a holder; a `--last` waiter, then a plain waiter; the plain one runs
    first.
  - `test_last_queues_normally_after_its_wait`: `last_wait_max_s: 0` in the config file; the `--last` waiter runs in
    its arrival order.
  - `test_killed_holder_frees_its_slot`: `SIGKILL` the holder; the next waiter starts within 3 s.
  - `test_dead_waiter_is_dropped`: a ticket file with a pid that does not exist; `status()` lists no waiter and the
    file is gone.
  - `test_sigterm_reaches_the_command`: `SIGTERM` to the wrapper; the command's process is gone within 3 s.
  - `test_nested_run_runs_at_once`: with `TEST_QUEUE_SLOT` set and the only slot held, `run` starts its command at
    once.
  - `test_status_prints_holders_and_waiters`: `main(["status"])` prints each holder's and waiter's label, cwd and age.
  - `test_broken_config_falls_back_to_defaults`: invalid JSON in the config file → `Config()`.
- [ ] **Step 2: Run them to see them fail**: `python3 -m unittest tests.test_test_queue -v` → errors, no module.
- [ ] **Step 3: Implement** in `lib/paseo_tools/test_queue.py`, with `bin/test-queue` as the other entry points are
  (`sys.path` to `lib/`, `raise SystemExit(test_queue.main())`):
  - Slot `i` is `state_dir()/slot-<i>.lock`, held with `fcntl.flock(LOCK_EX | LOCK_NB)` by the `run` process for the
    command's whole life; its holder writes `slot-<i>.json` (an `Entry`, written to a temporary name and renamed).
  - A waiting call writes `waiting/<pid>.json` and checks every second. Queue order: entries sorted by
    `(0 if not last or waited > last_wait_max_s else 1, since, pid)`; a call takes a free slot only when fewer calls
    are ahead of it than slots are free. A ticket whose pid is gone is removed when read.
  - While waiting it prints to stderr, once and again whenever the holders change:
    `test-queue: waiting; held by <label> (<cwd>) for <m> min; <k> ahead`.
  - The command runs in its own process group with the call's streams and environment plus `TEST_QUEUE_SLOT`;
    `SIGTERM` and `SIGINT` go on to the group; the call exits with the command's code (128 + signal when it died of
    one).
  - `TEST_QUEUE_SLOT` set: run at once, no slot, no ticket.
- [ ] **Step 4: Run the tests**: `python3 -m unittest tests.test_test_queue tests.test_bin -v` → OK.
- [ ] **Step 5: Commit**: `git add lib/paseo_tools/test_queue.py bin/test-queue tests/test_test_queue.py tests/test_bin.py` and
  `git commit -m "feat: test-queue, one machine-wide queue for heavy test runs"`.

### Task 2: The hook: heavy Bash commands go through the queue

**Files:**
- Modify: `lib/paseo_tools/test_queue.py` (a `# The hook` section)
- Create: `hooks/hooks.json`
- Test: `tests/test_test_queue_hook.py`

**Interfaces:**
- Consumes: the `test-queue run [--label L] -- <command…>` command line of Task 1 (by its documented form only).
- Produces:
  - `DEFAULT_PATTERNS: list[str]` (regular expressions, searched in the whole command)
  - `is_heavy(command: str, patterns: list[str]) -> bool`
  - `wrap(command: str, label: str, executable: str) -> str` →
    `f"{shlex.quote(executable)} run --label {shlex.quote(label)} -- bash -c {shlex.quote(command)}"`
  - `hook(payload: dict, config: Config, executable: str) -> dict | None`, and `main(["hook"])` reading the payload
    from stdin and printing the result as JSON.

- [ ] **Step 1: Write the failing tests** in `tests/test_test_queue_hook.py`:
  - `test_heavy_patterns`: a table. Heavy: `bun run check`, `mise exec -- bun run check > /tmp/x.log 2>&1`,
    `bun run test`, `bun run test:changed`, `bun run ci`, `bun run e2e`, `npm test`, `pnpm test`, `turbo run test`,
    `turbo run typecheck test --affected`, `bun run --cwd apps/web test`, `npx vitest run`, `bunx playwright test`,
    `pytest`. Light: `git status`, `bun run lint`, `bun run typecheck`, `vitest run src/a.test.ts`,
    `bunx vitest run src/a.test.ts src/b.test.ts`, `playwright test e2e/one.spec.ts`, `pytest tests/test_one.py`. The
    table also records `echo "bun run check"` as heavy: matching is textual, and a false positive only queues.
  - `test_hook_wraps_a_heavy_command`: payload with `permission_mode: "bypassPermissions"`, `tool_name: "Bash"`,
    `tool_input: {"command": "bun run check", "description": "d", "timeout": 120000, "run_in_background": true}` →
    `hookSpecificOutput.hookEventName == "PreToolUse"`, `permissionDecision == "allow"`, `updatedInput.command ==
    wrap("bun run check", <label>, <executable>)`, the other three fields unchanged. The same payload with
    `run_in_background: false` and no or a shorter `timeout` → `updatedInput.timeout == 600000`, the Bash tool's
    maximum, since a wait in the queue counts against it.
  - `test_wrapped_command_runs_as_written`: `bash -c` of `wrap(cmd, …)` with a stand-in executable that runs its
    arguments after `--` prints the same as `bash -c cmd` for commands with `'`, `"`, `$HOME`, a backtick, a newline,
    a heredoc and `cd sub && pwd`.
  - `test_hook_leaves_alone`: no output for a light command; a command already starting with the executable's path;
    `permission_mode` `"default"`, `"acceptEdits"`, `"plan"` or missing; `tool_name` `"Read"`; `tool_input` missing;
    stdin empty or not JSON (through `main(["hook"])`, exit 0).
  - `test_config_patterns_replace_the_defaults`: a config with `"patterns": ["^make check$"]` wraps `make check` and
    not `bun run check`.
- [ ] **Step 2: Run them to see them fail**: `python3 -m unittest tests.test_test_queue_hook -v`.
- [ ] **Step 3: Implement** `DEFAULT_PATTERNS`, `is_heavy`, `wrap`, `hook` and the `hook` subcommand. The label is the
  payload's `cwd` name and the command's first 60 characters; `executable` is the absolute path of `bin/test-queue`
  beside the running module. Any exception in `hook` → no output, exit 0.
- [ ] **Step 4: Declare the hook** in `hooks/hooks.json`: `PreToolUse`, matcher `Bash`, `type: "command"`,
  `command: "python3"`, `args: ["${CLAUDE_PLUGIN_ROOT}/bin/test-queue", "hook"]`, `timeout: 10`. Then check Codex's
  current hook documentation: if a Codex plugin can declare a pre-tool hook that rewrites a shell command, declare it
  for `.codex-plugin` the same way; otherwise the README says Codex sessions are not wrapped. Record which in the
  commit body.
- [ ] **Step 5: Run** `python3 -m unittest tests.test_test_queue_hook -v` → OK, and `claude plugin validate .` → no
  error.
- [ ] **Step 6: Commit**: `git add lib/paseo_tools/test_queue.py hooks/hooks.json tests/test_test_queue_hook.py` and
  `git commit -m "feat: the plugin's hook sends heavy Bash commands through test-queue"`.

### Task 3: Documents and the entry-point list

**Files:**
- Modify: `README.md`, `SECURITY.md`, `AGENTS.md` (layout), `CONTRIBUTING.md` ("links the three commands"),
  `install.sh` (its comment if it names the tools)

- [ ] **Step 1:** `README.md` gets a `test-queue` section: what it is for, `run`, `run --last` for cron lines,
  `status`, the hook and its `bypassPermissions` limit, that a foreground call whose wait and run pass the Bash tool's
  ten minutes ends there (heavy runs belong in the background), the configuration file with its three keys and defaults,
  `TEST_QUEUE_SLOTS`, and an example cron line
  `7 * * * * test-queue run --last --label "<name> CI" -- <the CI's command>`.
- [ ] **Step 2:** `SECURITY.md` gets a `test-queue` table in the form of the others: Reads (its configuration file,
  the queue's state files), Writes (the state directory's lock, holder and ticket files), Runs (the wrapped command,
  unchanged, in its own process group), Network (none), Sends (nothing); and a line on the hook: it rewrites a Bash
  command only in `bypassPermissions` sessions and only by prefixing `test-queue run … -- bash -c`.
- [ ] **Step 3:** `AGENTS.md`'s layout lists `bin/test-queue`, `lib/paseo_tools/test_queue.py`, `hooks/` and
  `docs/plans/`; `CONTRIBUTING.md` says four commands.
- [ ] **Step 4:** All six checks of `CONTRIBUTING.md` → green. Commit:
  `git commit -m "docs: test-queue in the readme, the security tables and the layout"` with the named paths.

### Task 4: The pull request

- [ ] **Step 1:** Push `feat/test-queue` and open a pull request against `main`; its body says what changes for users
  (the hook wraps heavy commands in `bypassPermissions` sessions once the plugin updates; `./install.sh` puts
  `test-queue` on `PATH` for cron lines) and how it was tested.
- [ ] **Step 2:** The completion report names the pull request; the maintainer merges.

### After the merge (people, not the implementer)

- [ ] Each machine updates the plugin and runs `./install.sh` from its clone.
- [ ] Each hourly CI's cron line is wrapped: `test-queue run --last --label "<name> CI" -- <its command>`.
- [ ] Each machine's configuration file adds the patterns of commands its repositories run that start a heavy suite
  under another name (a landing command that runs a check, for example); the defaults stay generic.
- [ ] A repository that queues its heavy runs itself drops its own queue in a change of its own.
