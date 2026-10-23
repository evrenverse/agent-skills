"""paseo-notify: tell the calling paseo agent, once, when another agent stops.

    paseo-notify <agent-id>                 arm, return at once; the caller is $PASEO_AGENT_ID
    paseo-notify <agent-id> --to <caller>   arm for someone else (cron, scripts)

The caller arms it and ends its turn. A detached process blocks in ONE `paseo wait` (the daemon answers over
its websocket when the agent stops: no polling, no model call, no tokens) and then `paseo send`s the caller
one message: finished its turn / errored / needs a permission, with the agent's last activity. That is
paseo's own notifyOnFinish minus the parent link: notifyOnFinish exists only for children made through
paseo's MCP tools, and those archive with their parent, while team agents are detached on purpose.

WHAT IT DOES NOT DO
  - Wake an archived caller: `paseo send` would unarchive it.
  - Wake the caller for a usage limit or a transient API error: the agent is idle then, but not done, and a
    fresh `paseo wait` would return at once. A marker in ~/.paseo/notify/ hands the watch to the limit
    watcher, which re-arms it right after resuming the agent and reports to the caller when it gives up or
    the work was taken over (`parked_callers`, `unpark`, `arm`, `send`).
  - Watch twice: a second arm for the same agent and caller while a watch runs ends at once (a lock in
    ~/.paseo/notify/locks/), so re-arming after every `paseo send` never doubles the message.
  - Die silently: if `paseo wait` itself fails, the caller is told the watch is gone and how to re-arm it.
    That is all it does about it: the paseo daemon belongs to systemd, and nothing here starts, stops or
    restarts it.
  - Take "idle" for "done" while the agent still has background work out. A Claude Code agent that starts a
    long command or subagent in the background ends its turn, paseo reports it idle, and the task's
    completion starts its next turn. Its transcript says which tasks are still out
    (`agents.pending_tasks`); while any is, the watch re-checks once a minute without a model call, and
    gives up waiting after MAX_PENDING (a background Bash task is killed after at most 2 h anyway).

Exit code is 0 once armed; 1 on bad input, before anything detached. The detached process logs to
~/.paseo-notify.log.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from paseo_tools import agents, cli, limits

MAX_ACTIVITY = 3000
PENDING_RECHECK = 60
MAX_PENDING = 3 * 3600
TRANSCRIPT_TAIL = 1_000_000
# `paseo wait` has no timeout of its own and an agent can work for days; this only keeps a hung call from
# outliving every other process. Not larger: subprocess hands poll() milliseconds as a C int (about 24 days),
# and a bigger timeout raises OverflowError.
WAIT_TIMEOUT = 7 * 24 * 3600
# The detached watch closes inherited descriptors up to here: SC_OPEN_MAX can be a million or more, and
# without close_range(2) each number is one system call.
# shortcut: a descriptor above MAX_FD stays open; inherited ones are low, raise it if that ever changes.
MAX_FD = 65536


def notify_dir() -> Path:
    """Where watches parked on a limit leave a marker for the limit watcher."""
    return Path.home() / ".paseo" / "notify"


def log_path() -> Path:
    """The log of the detached watch."""
    return Path.home() / ".paseo-notify.log"


def marker(child: str, caller: str) -> Path:
    """The marker file of the watch of `caller` on `child`: `<child>--<caller>`."""
    return notify_dir() / f"{child}--{caller}"


def log(line: str) -> None:
    """Print a timestamped line; in the detached process stdout is the log file."""
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}", flush=True)


def outcome(stdout: str, stderr: str, last_text: str) -> tuple[str, str | None]:
    """What to do once `paseo wait --json` returned: ("park", None) or ("send", body).

    Pure, so tests need no paseo. `paseo wait` exits 0 even on error and timeout; only the JSON `status`
    tells.
    """
    try:
        res = json.loads(stdout)
        status = res["status"]
    except (ValueError, KeyError, TypeError):
        return "send", (
            "can no longer be watched: `paseo wait` failed with "
            f"{(stderr or stdout or '').strip()[:300]!r}. Re-arm with `paseo-notify <id>`."
        )
    activity = (res.get("message") or "").strip()[-MAX_ACTIVITY:]
    if status == "idle" and limits.classify(last_text) is not None:
        return "park", None
    head = {
        "idle": "finished its turn (done, or it asks something; read its last message).",
        "permission": "is waiting for a permission.",
        "error": "stopped with an error.",
    }.get(status, f"returned status {status!r}.")
    return "send", f"{head}\n\n{activity}"


def _inspect(agent_id: str) -> dict | None:
    """`paseo inspect --json` as a dict, or None when the agent is unknown or the call fails."""
    info = cli.json_out(cli.paseo("inspect", agent_id, "--json"))
    return info if isinstance(info, dict) and info.get("Id") else None


def send(caller: str, child: str, body: str) -> bool:
    """Tell `caller` that `child` stopped. True if delivered; never to an archived or unknown caller."""
    info = _inspect(caller)
    if not info or info.get("Archived"):
        log(f"{child[:7]} -> {caller[:7]}: caller archived or unknown, nothing sent")
        return False
    title = (_inspect(child) or {}).get("Name") or child
    text = (
        f"[paseo-notify] Agent {child} ({title}) {body}\n\n"
        f"Read more with `paseo logs {child} --tail 20`. After you `paseo send` it anything, "
        f"arm `paseo-notify {child}` again and end your turn."
    )
    # --behavior steer: a caller in the middle of a turn gets it folded in instead of interrupted.
    proc = cli.paseo("send", "--behavior", "steer", "--no-wait", caller, text)
    delivered = proc.returncode == 0
    result = "sent" if delivered else "SEND FAILED " + (proc.stderr or proc.stdout).strip()[:200]
    log(f"{child[:7]} -> {caller[:7]}: {result}")
    return delivered


def parked_callers(child: str) -> list[str]:
    """The callers whose watch on `child` is parked on a limit, waiting for the limit watcher."""
    return sorted(path.name.split("--", 1)[1] for path in notify_dir().glob(f"{child}--*"))


def unpark(child: str, caller: str) -> None:
    """Remove the marker of a parked watch (the limit watcher re-arms or reports it next)."""
    marker(child, caller).unlink(missing_ok=True)


def _pending_of(child: str) -> set[str]:
    """Background tasks `child` still has out, from the tail of its Claude Code transcript."""
    record = next((rec for rec in agents.load_agents(child) if rec.get("id") == child), None)
    path = agents.transcript_path(record) if record else None
    if not path:
        return set()
    try:
        return agents.pending_tasks(agents.tail_lines(path, TRANSCRIPT_TAIL))
    except OSError:
        return set()


def _last_text(child: str) -> str:
    message = agents.last_message_of({"id": child})
    return message.text if message else ""


def _is_idle(proc: subprocess.CompletedProcess[str]) -> bool:
    status = cli.json_out(proc)
    return isinstance(status, dict) and status.get("status") == "idle"


def watch(child: str, caller: str) -> None:
    """Block until `child` stops for good, then park the watch or send `caller` one message."""
    pending_since, note = None, ""
    while True:
        began = time.monotonic()
        proc = cli.paseo("wait", child, "--json", timeout=WAIT_TIMEOUT)
        if time.monotonic() - began > 5:
            pending_since = None  # it ran in between: the pending clock starts over
        if not (_is_idle(proc) and _pending_of(child)):
            break
        pending_since = pending_since or time.monotonic()
        if time.monotonic() - pending_since >= MAX_PENDING:
            note = (
                f"\n\n(It still has background tasks out, but nothing came back for {MAX_PENDING // 3600} h.)"
            )
            break
        time.sleep(PENDING_RECHECK)
    # A failed wait (daemon restarting, no JSON) is only reported; the daemon is not ours to touch.
    stdout, stderr = (proc.stdout, proc.stderr) if proc.returncode == 0 else ("", proc.stderr or proc.stdout)
    action, body = outcome(stdout, stderr, _last_text(child) if _is_idle(proc) else "")
    if action == "park":
        notify_dir().mkdir(parents=True, exist_ok=True)
        marker(child, caller).touch()
        log(f"{child[:7]} -> {caller[:7]}: blocked on a limit, parked for the limit watcher")
        return
    send(caller, child, (body or "") + note)


def _detach() -> bool:
    """Double fork into a new session, so the watch outlives the shell that armed it.

    True in the detached grandchild, False in the original process.
    """
    pid = os.fork()
    if pid:
        os.waitpid(pid, 0)
        return False
    # A forked copy of the caller from here on: on any failure it exits. Returning would run the rest of the
    # caller a second time, e.g. the limit watcher's pass with its resume lines and its state file.
    try:
        os.setsid()
        if os.fork():
            os._exit(0)
        fd = os.open(log_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
        # Unlike subprocess, a fork keeps every descriptor: a watch the limit watcher re-arms would hold
        # cron's `flock` lock for as long as it waits, and lock the watcher out.
        open_max = os.sysconf("SC_OPEN_MAX")
        os.closerange(3, open_max if 0 < open_max <= MAX_FD else MAX_FD)
    except BaseException:
        os._exit(1)
    # Inherited from the caller, it makes `paseo send` wrap the message as the caller writing to itself
    # (sourceAgentId); without it the message lands as a plain, visible turn.
    os.environ.pop("PASEO_AGENT_ID", None)
    return True


def _hold(path: Path) -> int | None:
    """An exclusive lock on `path` while the returned descriptor is open; None when another process has it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def arm(child: str, caller: str) -> None:
    """Watch `child` for `caller` from a detached process; returns at once in the calling process.

    One watch per pair: the skills re-arm after every `paseo send`, often while the last watch still waits,
    and a second watch would wake the caller a second time.
    """
    if not _detach():
        return
    lock, held = notify_dir() / "locks" / f"{child}--{caller}", None
    try:
        held = _hold(lock)
        if held is None:
            log(f"{child[:7]} -> {caller[:7]}: already watched, this watch ends")
            return
        watch(child, caller)
    except Exception as exc:  # last resort: the caller must not wait forever on a dead watch
        log(f"{child[:7]} -> {caller[:7]}: crashed: {exc!r}")
        send(
            caller,
            child,
            f"can no longer be watched: paseo-notify crashed ({exc!r}). Re-arm with `paseo-notify {child}`.",
        )
    finally:
        if held is not None:
            with contextlib.suppress(OSError):
                lock.unlink()
                os.close(held)
        os._exit(0)  # the detached process never returns into the caller's code, whatever was raised


def _fail(message: str) -> int:
    print(f"paseo-notify: {message}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    """The `paseo-notify` command line; the exit code is 0 once armed."""
    parser = argparse.ArgumentParser(
        prog="paseo-notify", description="Tell the calling paseo agent once when another agent stops."
    )
    parser.add_argument("agent", help="id or id prefix of the agent to wait for")
    parser.add_argument(
        "--to",
        default=os.environ.get("PASEO_AGENT_ID"),
        help="agent to notify (default: $PASEO_AGENT_ID, the caller)",
    )
    args = parser.parse_args(argv)
    if not args.to:
        return _fail(
            "no caller — run it inside a paseo agent or pass --to <id>. Outside paseo, "
            "run `paseo wait <id> --json` as a background command instead."
        )
    child_info, caller_info = _inspect(args.agent), _inspect(args.to)
    if not child_info:
        return _fail(f"no agent {args.agent!r}")
    if not caller_info:
        return _fail(f"no caller agent {args.to!r}")
    child, caller = child_info["Id"], caller_info["Id"]
    if child == caller:
        return _fail("an agent cannot wait for itself")
    print(
        f"armed: {caller[:7]} gets one message when {child[:7]} ({child_info.get('Name')}) stops. "
        f"End your turn now; do not sleep, poll or watch.",
        flush=True,
    )
    arm(child, caller)
    return 0
