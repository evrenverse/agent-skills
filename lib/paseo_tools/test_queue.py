"""test-queue: one machine-wide queue, so heavy test runs run one after another instead of side by side.

    test-queue run [--label L] [--last] -- <command…>   wait for a slot, run the command holding it
    test-queue status                                   who holds a slot and who waits, in queue order

Agents and hourly CIs of several repositories share one machine. Heavy suites that run side by side overload
its CPU and database: together they take longer than one after another, and timing tests and connection limits
turn red. `run` waits for one of N slots (`slots` in the configuration, `TEST_QUEUE_SLOTS` overrides it), runs
the command holding it and exits with the command's code, 128 + the signal when one killed it.

A slot is a lock file held with flock(2) for the command's whole life, so the kernel frees it when its holder
dies, SIGKILL included; the holder's label, directory and start sit beside it in `slot-<i>.json`, which counts
only while the lock is held. A waiting call leaves a ticket `waiting/<pid>.json`, checks every second and
takes a free slot only when fewer calls are ahead of it than slots are free; the ticket of a call whose
process is gone is dropped. `--last` (for cron lines) lets every other waiter go first until it has waited
`last_wait_max_s` seconds.

The command runs in its own session, so its process group is its own, with the call's streams and environment
plus `TEST_QUEUE_SLOT=<label>`; SIGTERM, SIGINT and SIGHUP are passed on to that group. A call with
`TEST_QUEUE_SLOT` already set runs at once, without a slot or a ticket: a check that starts a suite inside a
held slot must not wait for a second one.

State: $TEST_QUEUE_DIR, else $XDG_STATE_HOME/test-queue, else ~/.local/state/test-queue. Configuration:
$XDG_CONFIG_HOME/test-queue/config.json, else ~/.config/test-queue/config.json; a missing or broken file means
the defaults.

WHAT IT DOES NOT DO
  - Stop the command when the call itself is SIGKILLed: the slot frees with the call, and the command, in its
    own session, runs on outside the queue.
  - Keep the slot for what the command leaves behind: a daemon it starts inherits no lock.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

POLL = 1.0  # seconds between a waiting call's checks
FORWARDED = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


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


def _alive(pid: int) -> bool:
    """Whether process `pid` exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # another user's process
        pass
    return True


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
    """The waiting calls in queue order; the ticket of a call whose process is gone is removed."""
    entries = []
    for path in (state_dir() / "waiting").glob("*.json"):
        entry = _read(path)
        if entry is None or not _alive(entry.pid):
            path.unlink(missing_ok=True)
        else:
            entries.append(entry)
    now = time.time()
    return sorted(entries, key=lambda entry: _queue_key(entry, config, now))


def status(config: Config | None = None) -> tuple[list[Entry], list[Entry]]:
    """The calls holding a slot, and the waiting ones in queue order."""
    config = config or load_config()
    (state_dir() / "waiting").mkdir(parents=True, exist_ok=True)
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


def _execute(command: list[str], label: str) -> int:
    """Run `command` in its own session, passing SIGTERM, SIGINT and SIGHUP on to it; its exit code."""
    child: list[subprocess.Popen[bytes]] = []
    early: list[int] = []  # signals that came before the command existed

    def forward(signum: int, _frame: object) -> None:
        if not child:
            early.append(signum)
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child[0].pid, signum)

    previous = {signum: signal.signal(signum, forward) for signum in FORWARDED}
    try:
        try:
            # A new session, not preexec_fn=os.setpgrp: no Python runs between fork and exec, and a command
            # that reads the terminal is not stopped as a background group (SIGTTIN).
            proc = subprocess.Popen(
                command, env={**os.environ, "TEST_QUEUE_SLOT": label}, start_new_session=True
            )
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


def run(command: list[str], label: str, last: bool = False, config: Config | None = None) -> int:
    """Wait for a slot, run `command` holding it and return its exit code.

    Inside a held slot (TEST_QUEUE_SLOT set) the command runs at once, without a slot or a ticket.
    """
    if os.environ.get("TEST_QUEUE_SLOT"):
        return _execute(command, label)
    config = config or load_config()
    state = state_dir()
    (state / "waiting").mkdir(parents=True, exist_ok=True)
    me = Entry(label, os.getpid(), os.getcwd(), time.time(), last)
    ticket = state / "waiting" / f"{me.pid}.json"
    _write(ticket, me)
    try:
        slot, fd = _wait(me, config)
    except KeyboardInterrupt:
        return 128 + signal.SIGINT
    finally:
        ticket.unlink(missing_ok=True)
    holder = state / f"slot-{slot}.json"
    try:
        _write(holder, replace(me, since=time.time()))
        return _execute(command, label)
    finally:
        holder.unlink(missing_ok=True)  # while the lock is still held, so it is never the next holder's file
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    """The command line: `run` and `status`."""
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
    args = parser.parse_args(argv)

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

DEFAULT_PATTERNS: list[str] = []
