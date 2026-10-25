"""test-queue: one queue per user, so heavy test runs run one after another instead of side by side.

    test-queue run [--label L] [--last] -- <command…>   wait for a slot, run the command holding it
    test-queue status                                   who holds a slot and who waits, in queue order
    test-queue hook                                     Claude Code PreToolUse hook (payload on stdin)

Agents and hourly CIs of several repositories share one machine. Heavy suites that run side by side overload
its CPU and database: together they take longer than one after another, and timing tests and connection limits
turn red. `run` waits for one of N slots (`slots` in the configuration, `TEST_QUEUE_SLOTS` overrides it), runs
the command holding it and exits with the command's code, 128 + the signal when one killed it.

A slot is a lock file held with flock(2) for the command's whole life, so the kernel frees it when its holder
dies, SIGKILL included; the holder's label, directory and start sit beside it in `slot-<i>.json`, which counts
only while the lock is held. A waiting call leaves a ticket `waiting/<pid>.json`, checks every second and
takes a free slot only when fewer calls are ahead of it than slots are free. It holds the lock of
`waiting/<pid>.lock` from before the ticket exists until after it is gone, so the same rule holds: a ticket
whose lock is free is dead and dropped, whatever process has its pid now. SIGTERM, SIGINT, SIGHUP and SIGQUIT
during the wait remove the ticket and end the call with 128 + the signal. `--last` (for cron lines) lets every
other waiter go first until it has waited `last_wait_max_s` seconds.

The command runs in its own session, so its process group is its own, with the call's streams and environment
plus `TEST_QUEUE_SLOT=<label>` and `TEST_QUEUE_HOLDER=<the call's pid>`; the same four signals are passed on
to that group. A signal ignored when the call starts (nohup) stays ignored, in the wait and in the command. A
call whose `TEST_QUEUE_HOLDER` names a call that still holds its slot runs at once, without a slot or a
ticket: a check that starts a suite inside a held slot must not wait for a second one, and a daemon started
inside a slot queues again once that call is gone. A call that cannot use the state directory says so on
stderr and runs its command unqueued, without those two variables.

The hook (hooks/hooks.json) is off until the configuration file exists; `{}` turns it on with the defaults.
It acts in a session in bypassPermissions mode only: its `allow` would skip the permission prompt of any
other mode. It sends a Bash call whose command matches one of the `patterns` outside its quoted strings and
here-document bodies through `run`, as `test-queue run … -- bash -c '<command>'`. The wait in the queue
counts against the call's timeout: a background call gets at least two hours, the most an unattended session
allows; a foreground call keeps its own, since Claude Code moves it to the background when it is reached,
unless CLAUDE_CODE_DISABLE_BACKGROUND_TASKS is set, and then it gets the Bash tool's longest, ten minutes. A
pattern that does not compile is skipped; any other error means no output: the call runs as it was. So does
a Bash deny or ask rule in the user or project settings that may match the command: Claude Code applies the
rule, which a wrapped command would escape.

State, one queue for all of a user's sessions and cron jobs: $TEST_QUEUE_DIR, else
$XDG_STATE_HOME/test-queue, else ~/.local/state/test-queue, its directories made with mode 700. Configuration:
$XDG_CONFIG_HOME/test-queue/config.json, else ~/.config/test-queue/config.json; a missing or broken file or
value means the defaults.

WHAT IT DOES NOT DO
  - Stop the command when the call itself is SIGKILLed: the slot frees with the call, and the command, in its
    own session, runs on outside the queue.
  - Keep the slot for what the command leaves behind: a daemon it starts inherits no lock.
  - Wrap a call by what it does: `patterns` match the command's text, so a script that runs a suite is not
    heavy to the hook, and neither is a suite inside quotes, `bash -c 'bun run check'`. A wrapped command runs
    in a new `bash -c`, without the session's shell functions and aliases.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

POLL = 1.0  # seconds between a waiting call's checks
FORWARDED = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)


def state_dir() -> Path:
    """Where the slots' locks, their holders and the waiting tickets are."""
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return Path(os.environ.get("TEST_QUEUE_DIR") or base / "test-queue")


def config_path() -> Path:
    """The configuration file: $XDG_CONFIG_HOME/test-queue/config.json, default ~/.config/…."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "test-queue" / "config.json"


@dataclass
class Config:
    """The configuration file's keys and their defaults."""

    slots: int = 1
    # A lambda, so the name resolves when a Config is made: the hook's section at the end defines it.
    patterns: list[str] = field(default_factory=lambda: list(DEFAULT_PATTERNS))
    last_wait_max_s: int = 3600


def load_config() -> Config:
    """The configuration; a missing or broken file gives the defaults. TEST_QUEUE_SLOTS overrides `slots`."""
    try:
        data = json.loads(config_path().read_text())
        config = Config(**{key.name: data[key.name] for key in fields(Config) if key.name in data})
        config.slots, config.last_wait_max_s = int(config.slots), int(config.last_wait_max_s)
    except (OSError, ValueError, TypeError):
        config = Config()
    if not isinstance(config.patterns, list) or not all(isinstance(p, str) for p in config.patterns):
        config.patterns = list(DEFAULT_PATTERNS)
    with contextlib.suppress(ValueError):
        config.slots = int(os.environ.get("TEST_QUEUE_SLOTS") or config.slots)
    config.slots = max(1, config.slots)
    return config


@dataclass
class Entry:
    """A call in the queue: a slot's holder or a waiter."""

    label: str
    pid: int
    cwd: str
    since: float  # when it began to wait, or took its slot
    last: bool


def default_label(cwd: str, command: str) -> str:
    """The label of a call without one: the working directory's name and the command's first 60 characters."""
    return f"{Path(cwd).name}: {command[:60]}"


def _write(path: Path, entry: Entry) -> None:
    """Write `entry` to `path` through a temporary name, so a reader never sees half a file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(entry)))
    tmp.replace(path)


def _read(path: Path) -> Entry | None:
    """The entry in `path`, or None when it is missing or broken."""
    try:
        return Entry(**json.loads(path.read_text()))
    except (OSError, ValueError, TypeError):
        return None


def _mkdir(path: Path) -> None:
    """Make the directory `path`, and its missing parents, with mode 700; one that exists keeps its mode."""
    if path.parent != path and not path.is_dir():
        _mkdir(path.parent)
        with contextlib.suppress(FileExistsError):  # made meanwhile by another call
            path.mkdir(0o700)


def _current(path: Path, fd: int) -> bool:
    """Whether `path` still names the file open as `fd`: a reader removes a dead ticket's lock file."""
    try:
        return os.stat(path).st_ino == os.fstat(fd).st_ino
    except FileNotFoundError:
        return False


def _hold(path: Path) -> int:
    """Lock `path`, made if missing, and return its descriptor; again if a reader removed it meanwhile."""
    while True:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        if _current(path, fd):
            return fd
        os.close(fd)


def _gone(ticket: Path) -> bool:
    """Whether the waiter of `ticket` is gone, its lock free; then its ticket and lock file are removed."""
    lock = ticket.with_suffix(".lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not _current(lock, fd):  # another reader removed it first
            return False
        ticket.unlink(missing_ok=True)
        lock.unlink()
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def _minutes(since: float) -> int:
    """Whole minutes since `since`."""
    return int((time.time() - since) // 60)


def _lock(slot: int) -> int | None:
    """Take the slot's lock without waiting: its descriptor, or None while someone else holds it."""
    fd = os.open(state_dir() / f"slot-{slot}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _probe(config: Config) -> tuple[dict[int, int], list[Entry]]:
    """Lock every free slot (their descriptors by slot) and read the holders of the others.

    The kernel's lock is the truth: a holder file whose lock is free is stale and names no holder.
    """
    free: dict[int, int] = {}
    holders: list[Entry] = []
    for slot in range(config.slots):
        fd = _lock(slot)
        if fd is not None:
            free[slot] = fd
        elif (entry := _read(state_dir() / f"slot-{slot}.json")) is not None:
            holders.append(entry)
    return free, holders


def _queue_key(entry: Entry, config: Config, now: float) -> tuple[int, float, int]:
    """Queue order: arrival, except that a --last call stays behind until it has waited last_wait_max_s."""
    behind = entry.last and now - entry.since <= config.last_wait_max_s
    return (1 if behind else 0, entry.since, entry.pid)


def _waiters(config: Config) -> list[Entry]:
    """The waiting calls in queue order; a dead ticket is removed."""
    entries = []
    for path in (state_dir() / "waiting").glob("*.json"):
        if not _gone(path) and (entry := _read(path)) is not None:
            entries.append(entry)
    now = time.time()
    return sorted(entries, key=lambda entry: _queue_key(entry, config, now))


def status(config: Config | None = None) -> tuple[list[Entry], list[Entry]]:
    """The calls holding a slot, and the waiting ones in queue order."""
    config = config or load_config()
    _mkdir(state_dir() / "waiting")
    free, holders = _probe(config)
    for fd in free.values():
        os.close(fd)
    return holders, _waiters(config)


def _wait(me: Entry, config: Config) -> tuple[int, int]:
    """Wait until a slot is free for `me`; the slot and the descriptor holding its lock."""
    shown = None
    while True:
        free, holders = _probe(config)
        now = time.time()
        mine = _queue_key(me, config, now)
        ahead = sum(_queue_key(other, config, now) < mine for other in _waiters(config))
        slot = min(free) if ahead < len(free) else None
        for other, fd in free.items():
            if other != slot:
                os.close(fd)
        if slot is not None:
            return slot, free[slot]
        current = [(holder.pid, holder.since) for holder in holders]
        if current != shown:
            shown = current
            held = ", ".join(f"{h.label} ({h.cwd}) for {_minutes(h.since)} min" for h in holders) or "nobody"
            print(f"test-queue: waiting; held by {held}; {ahead} ahead", file=sys.stderr, flush=True)
        time.sleep(POLL)


class _Interrupted(Exception):
    """A signal that ended the wait; args[0] is its number."""


def _interrupt(signum: int, _frame: object) -> None:
    """The wait's signal handler: end the wait so its ticket goes."""
    raise _Interrupted(signum)


def _catch(handler: Callable[[int, Any], None]) -> dict[signal.Signals, Any]:
    """Set `handler` for the forwarded signals the call does not ignore (nohup); the handlers it replaced."""
    return {
        signum: signal.signal(signum, handler)
        for signum in FORWARDED
        if signal.getsignal(signum) != signal.SIG_IGN
    }


def _queue(me: Entry, config: Config) -> tuple[int, int]:
    """Wait under a ticket that goes however the wait ends; the slot and the descriptor holding its lock."""
    ticket = state_dir() / "waiting" / f"{me.pid}.json"
    _mkdir(ticket.parent)
    lock = ticket.with_suffix(".lock")
    fd = _hold(lock)  # before the ticket exists: a ticket whose lock is free is dead
    previous = _catch(_interrupt)
    try:
        _write(ticket, me)
        return _wait(me, config)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        ticket.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)  # still locked, so no reader has replaced it
        os.close(fd)


def _execute(command: list[str], env: dict[str, str]) -> int:
    """Run `command` in its own session with the environment `env`, passing signals on; its exit code."""
    child: list[subprocess.Popen[bytes]] = []
    early: list[int] = []  # signals that came before the command existed

    def forward(signum: int, _frame: object) -> None:
        if not child:
            early.append(signum)
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child[0].pid, signum)

    previous = _catch(forward)
    try:
        try:
            # A new session, not preexec_fn=os.setpgrp: no Python runs between fork and exec, and a command
            # that reads the terminal is not stopped as a background group (SIGTTIN).
            proc = subprocess.Popen(command, env=env, start_new_session=True)
        except OSError as error:
            print(f"test-queue: {error}", file=sys.stderr)
            return 127
        child.append(proc)
        for signum in early:
            forward(signum, None)
        code = proc.wait()
        return 128 - code if code < 0 else code
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _nested(config: Config) -> bool:
    """Whether the call named by TEST_QUEUE_HOLDER, which started this one inside its slot, still holds it."""
    holder = os.environ.get("TEST_QUEUE_HOLDER")
    return bool(holder) and any(str(entry.pid) == holder for entry in status(config)[0])


def _take(me: Entry, config: Config) -> tuple[Path, int]:
    """Wait for a slot and write `me` as its holder; the holder file and the descriptor holding its lock."""
    slot, fd = _queue(me, config)
    holder = state_dir() / f"slot-{slot}.json"
    try:
        _write(holder, replace(me, since=time.time()))
    except OSError:
        os.close(fd)
        raise
    return holder, fd


def run(command: list[str], label: str, last: bool = False, config: Config | None = None) -> int:
    """Wait for a slot, run `command` holding it and return its exit code.

    Inside a slot that its holder (TEST_QUEUE_HOLDER) still holds, the command runs at once, without a slot or
    a ticket. When the state directory cannot be used, it runs unqueued.
    """
    config = config or load_config()
    me = Entry(label, os.getpid(), os.getcwd(), time.time(), last)
    try:
        taken = None if _nested(config) else _take(me, config)
    except _Interrupted as stop:
        return 128 + stop.args[0]
    except OSError as error:
        print(
            f"test-queue: cannot use {state_dir()} ({error}); running unqueued", file=sys.stderr, flush=True
        )
        outside = {k: v for k, v in os.environ.items() if k not in ("TEST_QUEUE_SLOT", "TEST_QUEUE_HOLDER")}
        return _execute(command, outside)
    if taken is None:
        return _execute(command, {**os.environ, "TEST_QUEUE_SLOT": label})
    holder, fd = taken
    try:
        return _execute(command, {**os.environ, "TEST_QUEUE_SLOT": label, "TEST_QUEUE_HOLDER": str(me.pid)})
    finally:
        holder.unlink(missing_ok=True)  # while the lock is still held, so it is never the next holder's file
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    """The command line: `run`, `status` and `hook`."""
    parser = argparse.ArgumentParser(
        prog="test-queue", description="One machine-wide queue for heavy test runs."
    )
    actions = parser.add_subparsers(dest="action", required=True)
    run_parser = actions.add_parser("run", help="wait for a slot, then run the command holding it")
    run_parser.add_argument(
        "--label", help="what status shows (default: the directory's name and the command)"
    )
    run_parser.add_argument(
        "--last",
        action="store_true",
        help="let every other waiter go first, up to last_wait_max_s (cron lines)",
    )
    run_parser.add_argument("command", nargs=argparse.REMAINDER, help="the command, after --")
    actions.add_parser("status", help="who holds a slot and who waits, in queue order")
    actions.add_parser("hook", help="PreToolUse hook: read the payload on stdin, print the rewritten call")
    args = parser.parse_args(argv)

    if args.action == "hook":
        return _hook_command()

    if args.action == "status":
        holders, waiters = status()
        for word, entries in (("holds", holders), ("waits", waiters)):
            for entry in entries:
                print(f"{word}  {_minutes(entry.since)} min  {entry.label}  ({entry.cwd})")
        if not holders and not waiters:
            print("no slot held, nobody waiting")
        return 0

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        run_parser.error("a command is needed after --")
    return run(command, args.label or default_label(os.getcwd(), shlex.join(command)), args.last)


# The hook

# Regular expressions searched in the whole command. A suite is heavy; one test file's run is not. Matching is
# textual: a false positive only queues, and a heavy run in an unusual spelling is missed (extend `patterns`).
DEFAULT_PATTERNS: list[str] = [
    # A package script that runs a suite: `bun run check`, `pnpm run test:changed`, `bun run --cwd web test`.
    # `ci:report` and `check:fast` are other scripts, so no `:` may follow `check`, `ci` or `e2e`. A flag's
    # value never starts with `-`: else `--a --b …` reads two ways per flag, which takes exponential time.
    r"\b(?:bun|npm|pnpm|yarn)\s+run\s+(?:--[\w-]+(?:=\S+|\s+(?!-)\S+)?\s+)*"
    r"(?:check|ci|e2e|test(?::[\w:-]+)?)(?![\w:-])",
    r"\b(?:npm|pnpm|yarn)\s+test(?![\w:-])",
    # `turbo run typecheck test --affected`: `test` among the task names, which are words, not flags.
    r"\bturbo\s+(?:run\s+)?(?:[\w:-]+\s+)*?test(?![\w-])",
    # A test runner is light only when an argument looks like a test file (`a.test.ts`, `t.py::test_a`); bare,
    # with flags and their values, or with a directory it runs a suite. Its arguments end at a shell operator.
    r"\b(?:vitest(?:\s+run)?|playwright\s+test|pytest|bun\s+test)"
    r"(?:[ \t]+(?![^\s;&|<>()]*\.(?:py|[cm]?[jt]sx?)(?![\w.]))[^\s;&|<>()]+)*"
    r"[ \t]*(?=$|[;&|<>()\n]|\d[<>])",
]

# Claude Code moves a foreground call that reaches its timeout to the background, unless
# CLAUDE_CODE_DISABLE_BACKGROUND_TASKS is set; unattended sessions stop a background call after 30 minutes
# unless its timeout says longer, up to two hours.
BACKGROUND_TIMEOUT_MS = 7_200_000
FOREGROUND_TIMEOUT_MS = 600_000  # the Bash tool's longest foreground timeout

# The shell syntax that hides text from the patterns, in one pass: an escaped character, a quoted string, a
# here-string's `<<<`, a here-document's operator and delimiter, a newline (where pending here-document bodies
# start), and a lone quote, which means the quoting does not parse. No alternative can backtrack.
_SHELL = re.compile(
    r"""\\.|'[^']*'|"(?:[^"\\]|\\.)*"|<<<|<<(-?)[ \t]*('[^']*'|"[^"]*"|[^\s;&|<>()'"]+)|\n|['"]""",
    re.DOTALL,
)


def _unquoted(command: str) -> str:
    """`command` with its quoted strings emptied and its here-document bodies removed.

    When its quoting does not parse (an unbalanced quote), `command` itself.
    """
    out: list[str] = []
    bodies: list[tuple[bool, str]] = []  # (tabs stripped, delimiter) of the bodies after the next newline
    pos = 0
    while (token := _SHELL.search(command, pos)) is not None:
        text = token.group()
        out += [command[pos : token.start()], text]
        pos = token.end()
        if text in ("'", '"'):
            return command
        if text[0] in "'\"":
            out[-1] = text[0] * 2
        elif token.group(2) is not None:
            bodies.append((token.group(1) == "-", token.group(2).strip("'\"\\")))
        elif text == "\n":
            for tabs, delimiter in bodies:  # a body without its delimiter line runs to the end, as in bash
                line = re.compile(("^\t*" if tabs else "^") + re.escape(delimiter) + "$", re.M)
                end = line.search(command, pos)
                pos = end.end() if end else len(command)
            bodies = []
    return "".join([*out, command[pos:]])


def is_heavy(command: str, patterns: list[str]) -> bool:
    """Whether one of the regular expressions `patterns` is found in `command` outside its quoted strings.

    Here-document bodies do not count either. A pattern that does not compile is skipped.
    """
    code = _unquoted(command)
    for pattern in patterns:
        with contextlib.suppress(re.error):
            if re.search(pattern, code):
                return True
    return False


def wrap(command: str, label: str, executable: str) -> str:
    """`command` as a call of `executable run`, which runs it in `bash -c` holding a slot."""
    return f"{shlex.quote(executable)} run --label {shlex.quote(label)} -- bash -c {shlex.quote(command)}"


def _project_dirs(cwd: str) -> list[Path]:
    """The directories whose `.claude/settings*.json` Claude Code may read for a session that runs in `cwd`.

    `$CLAUDE_PROJECT_DIR` (the session's start directory) when set, then the git root of `cwd` and the root of
    the main checkout (they differ in a worktree). When git cannot say, `cwd` itself stands for both.
    """
    dirs = [Path(cwd)]
    try:
        git = ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"]
        done = subprocess.run(
            git, capture_output=True, text=True, timeout=2, check=True, stdin=subprocess.DEVNULL
        )
        top, common = done.stdout.splitlines()
        dirs = [Path(top), Path(common).parent]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    start = os.environ.get("CLAUDE_PROJECT_DIR")
    return [Path(start), *dirs] if start else dirs


def _bash_specs(cwd: str) -> list[str]:
    """The specs of the Bash rules in `permissions.deny` and `permissions.ask` of the session's settings.

    The files are the user's (`$CLAUDE_CONFIG_DIR/settings.json`, else `~/.claude/settings.json`) and the
    `.claude/settings.json` and `.claude/settings.local.json` of each of `_project_dirs(cwd)`: a union, since
    a rule found anywhere only skips the queue. The bare rule `Bash` comes as `*`. A file that does not exist
    adds nothing; one that is unreadable, not JSON or not shaped like settings raises.
    """
    files = [Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"]
    for directory in _project_dirs(cwd):
        files += [directory / ".claude" / "settings.json", directory / ".claude" / "settings.local.json"]
    specs: list[str] = []
    for path in dict.fromkeys(files):
        if not path.exists():
            continue
        permissions = json.loads(path.read_text(encoding="utf-8")).get("permissions", {})
        for kind in ("deny", "ask"):
            rules = permissions.get(kind, [])
            if not isinstance(rules, list):
                raise TypeError(f"{path}: permissions.{kind} is not a list")
            for rule in rules:
                if rule == "Bash":
                    specs.append("*")
                elif isinstance(rule, str) and rule.startswith("Bash(") and rule.endswith(")"):
                    specs.append(rule[5:-1])
    return specs


def _matches(command: str, spec: str) -> bool:
    """Whether the Bash rule `spec` may match `command`; wide on purpose, a false alarm only skips the queue.

    With a `*`, the text before it (a `:` before the `*` dropped) must occur anywhere in the raw command:
    behind `&&`, in quotes, in a here-document. Without one, the whole spec must. A whitespace run counts as
    one space.
    """
    command, spec = (re.sub(r"\s+", " ", text) for text in (command, spec))
    if "*" in spec:
        spec = spec.split("*", 1)[0].removesuffix(":").strip()
    return spec in command


def hook(payload: Any, config: Config, executable: str) -> dict[str, Any] | None:
    """The PreToolUse answer for a Bash call that is heavy and in a bypassPermissions session, else None.

    Without the configuration file the hook is off: None. So is a command that a Bash deny or ask rule of the
    session's settings matches, and Claude Code then applies the rule, which a wrapped command would escape;
    settings that cannot be read count as such a rule. Any error, a malformed payload included, gives None:
    the call runs as it was.
    """
    with contextlib.suppress(Exception):  # this runs before every Bash call and must never fail one
        if not config_path().exists():
            return None
        if payload["permission_mode"] != "bypassPermissions" or payload["tool_name"] != "Bash":
            return None
        tool_input = payload["tool_input"]
        command = tool_input["command"]
        queued = command.startswith((executable, shlex.quote(executable)))
        if queued or not is_heavy(command, config.patterns):
            return None
        if any(_matches(command, spec) for spec in _bash_specs(payload["cwd"])):
            return None  # Claude Code checks its rules against the wrapped command, which they do not match
        updated = {**tool_input, "command": wrap(command, default_label(payload["cwd"], command), executable)}
        timeout = tool_input.get("timeout") or 0  # the wait in the queue counts against it
        if tool_input.get("run_in_background"):
            updated["timeout"] = max(timeout, BACKGROUND_TIMEOUT_MS)
        elif os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "0") not in ("", "0"):
            updated["timeout"] = max(timeout, FOREGROUND_TIMEOUT_MS)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": updated,
            }
        }
    return None


def _hook_command() -> int:
    """`test-queue hook`: the payload from stdin, the answer (if any) as JSON on stdout; exit code 0."""
    with contextlib.suppress(Exception):  # nothing on stderr either: the tool call stays quiet
        executable = str(Path(__file__).resolve().parents[2] / "bin" / "test-queue")
        result = hook(json.loads(sys.stdin.read()), load_config(), executable)
        if result is not None:
            print(json.dumps(result))
    return 0
