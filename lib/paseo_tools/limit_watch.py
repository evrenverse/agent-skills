"""paseo-limit-watch: resume paseo agents that stopped on a usage limit or a transient API error.

    paseo-limit-watch [--dry-run] [--reload]

Cron runs it every three minutes; one run is one pass. Every agent that is not running, not taken over
(~/.paseo/takeovers/<id>.json) and whose last message is a blocker (`limits.classify`) gets RESUME_LINE one
minute after its limit resets, then every 30 minutes while the blocker still stands, at most three times. A
transient error is resumed at once, under the same cap. Watches paseo-notify parked on the agent are re-armed
after a resume, and told why when the watcher gives up (unknown reset, retries exhausted, taken over).

--reload (opt-in) restarts a Claude agent launched through claude-rotate whose reset is far away, so that
its next process starts on another account, then resumes it at once (`may_reload`). Only accounts
claude-rotate allows in the agent's working directory (`allowed_here`, `claude-rotate status --dir`) count.

WHAT IT NEVER DOES
  - Read credentials, a secret store or another process's environment, call an undocumented endpoint or
    fake a User-Agent. The only programs it runs are `paseo` and `claude-rotate`.
  - Send to a claimed or archived agent (`paseo send` would unarchive it), or interrupt a running turn.
  - Start, stop or restart the paseo daemon: when `paseo ls` fails, the pass is logged and skipped.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from paseo_tools import agents, cli, limits, notify
from paseo_tools.agents import Message
from paseo_tools.limits import Blocker

GRACE = timedelta(minutes=1)
RETRY_AFTER = timedelta(minutes=30)
MAX_TRIES = 3
RELOAD_MIN_WAIT = timedelta(minutes=45)
RESUME_LINE = (
    "Continue where you left off — unless you already did after the limit reset; then ignore this message."
)
# Every transient error of an agent is one sighting: keyed by its own text and time, a lasting 529 would be
# a new blocker on every pass and be resumed forever.
TRANSIENT = "transient"
TRANSCRIPT_TAIL = 1_000_000


@dataclass
class Entry:
    """What the watcher remembers about one blocked agent.

    `message` identifies the blocker sighting (its text plus `written_at`, or TRANSIENT). The reset is
    computed once, at the first sighting, and reused while the same message stands: a clock time without a
    timestamp (Codex through `paseo logs`) is anchored at the first sighting and never rolls over to the next
    day.
    """

    message: str
    reset: datetime | None
    attempts: int = 0
    last: datetime | None = None
    reloaded: bool = False


def _log(now: datetime, line: str) -> None:
    print(f"{now.isoformat(timespec='seconds')} {line}", flush=True)


def _why(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stderr or proc.stdout).strip()[:200]


def state_path() -> Path:
    """The state file: $XDG_STATE_HOME/paseo-limit-watch/state.json, default ~/.local/state/…."""
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "paseo-limit-watch" / "state.json"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _time(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else None


def _entry(raw: Any) -> Entry | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("message"), str):
        return None
    attempts = raw.get("attempts")
    return Entry(
        raw["message"],
        _time(raw.get("reset")),
        attempts if isinstance(attempts, int) else 0,
        _time(raw.get("last")),
        raw.get("reloaded") is True,
    )


def load_state() -> dict[str, Entry]:
    """The saved entries by agent id; unreadable entries are left out."""
    raw = _read_json(state_path())
    return {key: e for key, value in (raw if isinstance(raw, dict) else {}).items() if (e := _entry(value))}


def save_state(state: dict[str, Entry]) -> None:
    """Write `state` atomically: a pass killed halfway leaves the previous file intact."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        key: {**asdict(e), "reset": e.reset and e.reset.isoformat(), "last": e.last and e.last.isoformat()}
        for key, e in state.items()
    }
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def claimed(agent_id: str) -> bool:
    """True when another agent took this one's work over (`ptakeover`).

    Existence is the whole signal: a half-written marker still means someone is on the work, and two agents
    on one task is worse than one that stays parked a while.
    """
    return (Path.home() / ".paseo" / "takeovers" / f"{agent_id}.json").exists()


def due(entry: Entry, now: datetime, *, transient: bool) -> bool:
    """Whether to send the resume line now.

    Transient: at once; timed: from `reset + GRACE`; then every RETRY_AFTER (the blocker still standing
    heals a reset that was off); never after MAX_TRIES attempts, never with an unknown reset.
    """
    if entry.reset is None or entry.attempts >= MAX_TRIES:
        return False
    if not transient and now < entry.reset + GRACE:
        return False
    return entry.last is None or now - entry.last >= RETRY_AFTER


def codex_reset() -> datetime | None:
    """The earliest reset of an exhausted Codex window in `paseo usage ls --json --refresh`, or None."""
    reports = cli.json_out(cli.paseo("usage", "ls", "--json", "--refresh"))
    resets = []
    for report in reports if isinstance(reports, list) else []:
        if not isinstance(report, dict) or report.get("sourceId") != "codex":
            continue
        for window in (report.get("report") or {}).get("windows") or []:
            if isinstance(window, dict) and (window.get("usedPct") or 0) >= 100:
                with_zone = str(window.get("resetsAt")).replace("Z", "+00:00")
                if reset := _time(with_zone):
                    resets.append(reset.astimezone())
    return min(resets, default=None)


def rotate_status(cwd: str | None = None) -> dict | None:
    """`claude-rotate status --json` for `cwd` (`--dir`), or None when claude-rotate is missing or fails.

    Exit 2 (an allowed account needs a login, or none is usable) still prints the complete JSON. An older
    claude-rotate rejects `--dir`: None, so nothing is reloaded.
    """
    argv = [cli.resolve("claude-rotate"), "status", "--json"]
    if cwd:
        argv += ["--dir", os.path.expanduser(cwd)]  # `paseo ls` reports the cwd `~`-abbreviated
    status = cli.json_out(cli.run(argv), ok=(0, 2))
    return status if isinstance(status, dict) else None


def _accounts(status: dict) -> list[dict]:
    return [a for a in status.get("accounts") or [] if isinstance(a, dict)]


def _own_account(status: dict, session_id: str | None) -> dict | None:
    # `claude_sessions` lists the Claude sessions running on each account; older claude-rotates lack it.
    for account in _accounts(status):
        sessions = account.get("claude_sessions")
        if session_id and isinstance(sessions, list) and session_id in sessions:
            return account
    return None


def scoped_reset(status: dict, session_id: str, model: str, now: datetime) -> datetime | None:
    """When the `model` window of the account running `session_id` resets; None when anything is unknown."""
    for window in (_own_account(status, session_id) or {}).get("w7_scoped") or []:
        if not isinstance(window, dict) or str(window.get("label")).lower() != model:
            continue
        if isinstance(secs := window.get("reset_secs"), (int, float)):
            return now + timedelta(seconds=secs)
    return None


def _provider(agent: dict) -> str:
    # `paseo ls` says "claude/<model>", the record on disk "claude".
    return str(agent.get("provider") or "").split("/")[0]


def _session(agent: dict) -> str | None:
    return (agent.get("persistence") or {}).get("sessionId")


def reset_for(
    agent: dict,
    blocker: Blocker,
    message: Message,
    now: datetime,
    status: Callable[[str | None], dict | None],
    codex: Callable[[], datetime | None],
) -> datetime | None:
    """When the limit in `message` resets, or None when nobody can tell.

    In order: the message itself, anchored at when it was written (in the system zone), or at `now` when the
    source has no timestamps; a spend cap goes no further, a person set it and no usage window lifts it; a
    Codex limit: its exhausted usage window (`codex`, `codex_reset` for the pass); a Claude model limit: that
    model's window on the agent's own account (`status`, `rotate_status` for the pass).
    """
    anchor = message.written_at.astimezone() if message.written_at else now
    if reset := limits.parse_reset(message.text, written_at=anchor):
        return reset
    if blocker.spend_cap:
        return None
    if "codex" in _provider(agent):
        return codex()
    session = _session(agent)
    # No directory: the own account's windows do not depend on it, and a deleted cwd would fail `--dir`.
    if blocker.model and session and (found := status(None)):
        return scoped_reset(found, session, blocker.model, now)
    return None


def _launched_through_rotate() -> bool:
    # Only this one command is read from paseo's config, and the file is never written.
    config = _read_json(Path.home() / ".paseo" / "config.json")
    try:
        command = config["agents"]["providers"]["claude"]["command"]
    except (KeyError, TypeError):
        return False
    return isinstance(command, list) and bool(command) and Path(str(command[0])).name == "claude-rotate"


def _no_background_work(agent: dict) -> bool:
    # A reload kills the process and every background task with it; without a readable transcript nothing
    # shows the agent is free of them.
    path = agents.transcript_path(agent)
    try:
        return path is not None and not agents.pending_tasks(agents.tail_lines(path, TRANSCRIPT_TAIL))
    except OSError:
        return False


def _usable(account: dict, model: str | None) -> bool:
    used = [account.get("h5_pct"), account.get("w7_pct")]
    used += [
        w.get("pct")
        for w in account.get("w7_scoped") or []
        if isinstance(w, dict) and str(w.get("label")).lower() == model
    ]
    return (
        account.get("status") == "ok"
        and not account.get("disabled")
        and account.get("allowed_here") is not False  # absent in older claude-rotates: allowed
        and all(isinstance(pct, (int, float)) and pct < 100 for pct in used)
    )


def may_reload(
    agent: dict,
    blocker: Blocker,
    reset: datetime | None,
    now: datetime,
    status: Callable[[str | None], dict | None],
    entry: Entry | None,
) -> bool:
    """Whether `--reload` may restart `agent` so that claude-rotate starts it on another account.

    All of: a timed limit, not reloaded for yet, whose reset is known and more than RELOAD_MIN_WAIT away (an
    unknown one is never guessed at: its callers are told instead); provider claude, launched through
    claude-rotate; no background work outstanding; and a usable account to move to. An account is usable when
    claude-rotate reports its status "ok" (the others are "relogin", "rate_limited", "sub_canceled" and
    "no_data"), it is not disabled, it is allowed in the agent's working directory (`allowed_here`; an
    older claude-rotate does not say, then it is), and neither its 5-hour, its 7-day nor the blocking model's
    own window is used up. Knowing the agent's own account (`claude_sessions`), any other usable account will
    do; not knowing it, the account claude-rotate launches next in that directory (`chosen`) must be usable.
    `status` (claude-rotate for the agent's directory, which may probe its accounts) is asked last, only when
    everything else holds.
    """
    if not blocker.timed or (entry and entry.reloaded) or reset is None or reset - now <= RELOAD_MIN_WAIT:
        return False
    if _provider(agent) != "claude" or not _launched_through_rotate() or not _no_background_work(agent):
        return False
    if (found := status(agent.get("cwd"))) is None:
        return False
    own = _own_account(found, _session(agent))
    candidates = (
        _accounts(found) if own else [a for a in _accounts(found) if a.get("name") == found.get("chosen")]
    )
    return any(a is not own and _usable(a, blocker.model) for a in candidates)


def _deliverable(agent_id: str, now: datetime, *, idle: bool = False) -> bool:
    # Checked right before every reload and send: since `paseo ls`, the agent may have been taken over or
    # archived. Before a reload (`idle`) also whether a turn started meanwhile: a reload kills it, a send only
    # steers.
    if claimed(agent_id):
        _log(now, f"{agent_id[:7]}: taken over, nothing sent")
        return False
    info = cli.json_out(cli.paseo("inspect", agent_id, "--json"))
    # Unknown or unreadable counts as archived: sending blind could unarchive an agent.
    if not isinstance(info, dict) or not info.get("Id") or info.get("Archived"):
        _log(now, f"{agent_id[:7]}: archived or unknown, nothing sent")
        return False
    if idle and info.get("Status") == "running":
        _log(now, f"{agent_id[:7]}: running again, not reloaded")
        return False
    return True


def _reload(agent_id: str, entry: Entry, now: datetime, dry_run: bool) -> bool:
    if not _deliverable(agent_id, now, idle=True):
        return False  # refused: no reload spent, the next pass decides again
    entry.reloaded = True  # at most one per reset, failed or not: then it waits for the reset
    if dry_run:
        _log(now, f"{agent_id[:7]}: would reload")
        return True
    proc = cli.paseo("agent", "reload", agent_id, timeout=120)
    if proc.returncode != 0:
        _log(now, f"{agent_id[:7]}: RELOAD FAILED, waits for the reset: {_why(proc)}")
        return False
    _log(now, f"{agent_id[:7]}: reloaded, claude-rotate picks the account of its next process")
    return True


def _resume(agent_id: str, entry: Entry, now: datetime, dry_run: bool) -> None:
    if not _deliverable(agent_id, now):
        return
    attempt = f"attempt {entry.attempts + 1}/{MAX_TRIES}"
    if dry_run:
        _log(now, f"{agent_id[:7]}: would send the resume line ({attempt})")
        return
    # steer: cron has no PASEO_AGENT_ID, and the default would interrupt a turn someone started meanwhile.
    proc = cli.paseo("send", "--behavior", "steer", "--no-wait", agent_id, RESUME_LINE)
    if proc.returncode != 0:
        _log(now, f"{agent_id[:7]}: SEND FAILED, not counted: {_why(proc)}")
        return
    entry.attempts, entry.last = entry.attempts + 1, now
    _log(now, f"{agent_id[:7]}: resume line sent ({attempt})")
    # The resumed turn is running now, so a fresh `paseo wait` blocks again.
    for caller in notify.parked_callers(agent_id):
        notify.unpark(agent_id, caller)
        notify.arm(agent_id, caller)
        _log(now, f"{agent_id[:7]}: watch of {caller[:7]} re-armed")


def _give_up(agent_id: str, reason: str, now: datetime, dry_run: bool) -> None:
    # The marker goes with the message, so each parked caller is told once.
    for caller in notify.parked_callers(agent_id):
        _log(now, f"{agent_id[:7]}: {'would tell' if dry_run else 'telling'} {caller[:7]} it {reason}")
        if not dry_run:
            notify.send(caller, agent_id, reason)
            notify.unpark(agent_id, caller)


def _check(
    listed: dict,
    state: dict[str, Entry],
    now: datetime,
    *,
    dry_run: bool,
    reload: bool,
    status: Callable[[str | None], dict | None],
    codex: Callable[[], datetime | None],
) -> None:
    agent_id = listed["id"]
    if listed.get("status") == "running":
        # Not blocked by definition; a stale count would cut short the resumes after its next limit.
        state.pop(agent_id, None)
        return
    if claimed(agent_id):
        state.pop(agent_id, None)
        reason = (
            f"was taken over by another agent (~/.paseo/takeovers/{agent_id}.json); this watch ends here."
        )
        _give_up(agent_id, reason, now, dry_run)
        return
    # `paseo ls` entries carry no `persistence`; the record on disk names the session.
    record = next((rec for rec in agents.load_agents(agent_id) if rec.get("id") == agent_id), {})
    agent = {**listed, **record}
    message = agents.last_message_of(agent)
    blocker = limits.classify(message.text) if message else None
    if not message or not blocker:
        state.pop(agent_id, None)
        return
    text = repr(message.text.strip()[:200])
    stamp = message.written_at.isoformat() if message.written_at else "-"
    sighting = f"{stamp} {message.text}" if blocker.timed else TRANSIENT
    entry = state.get(agent_id)
    if entry is None or entry.message != sighting:
        # A resume or reload that did not cure the block makes Claude Code write the limit again, newly
        # stamped. The cap and the one reload hold per reset, so an entry already acted on carries over (it
        # survives only if no pass saw the agent running in between), and with the same text so does its
        # reset: written after its time, a date would lie in the past and a clock time roll to tomorrow.
        if entry and blocker.timed and (entry.last is not None or entry.reloaded):
            if entry.message.split(" ", 1)[1:] != [message.text]:
                entry.reset = reset_for(agent, blocker, message, now, status, codex)
            entry.message = sighting
        else:
            reset = reset_for(agent, blocker, message, now, status, codex) if blocker.timed else now
            entry = state[agent_id] = Entry(sighting, reset)
        _log(
            now,
            f"{agent_id[:7]}: blocked, reset {entry.reset.isoformat() if entry.reset else 'unknown'}: {text}",
        )
    elif entry.reset is None:
        # Asked again: `paseo usage` or claude-rotate may have failed or lagged at the first sighting.
        entry.reset = reset_for(agent, blocker, message, now, status, codex)
    if entry.attempts >= MAX_TRIES:
        _give_up(
            agent_id,
            f"is still blocked after {MAX_TRIES} resume attempts; nobody resumes it: {text}",
            now,
            dry_run,
        )
        return
    if reload and may_reload(agent, blocker, entry.reset, now, status, entry):
        if _reload(agent_id, entry, now, dry_run):
            _resume(agent_id, entry, now, dry_run)
        return
    if entry.reset is None:
        reason = f"is blocked and paseo-limit-watch cannot tell when it resets, so nobody resumes it: {text}"
        _give_up(agent_id, reason, now, dry_run)
    elif due(entry, now, transient=not blocker.timed):
        _resume(agent_id, entry, now, dry_run)
    elif dry_run:
        _log(now, f"{agent_id[:7]}: waits until {(entry.reset + GRACE).isoformat(timespec='seconds')}")


def run_once(now: datetime, *, dry_run: bool, reload: bool) -> None:
    """One pass over every paseo agent at `now` (aware). A dry run prints what it would do, writes nothing."""
    proc = cli.paseo("ls", "-g", "--background", "--json")
    listing = cli.json_out(proc)
    if not isinstance(listing, list):
        # Skipped, never fixed: the daemon belongs to systemd. Silence would read as "nothing to do".
        _log(now, f"paseo ls failed, pass skipped: {_why(proc)!r}")
        return
    listing = [a for a in listing if isinstance(a, dict) and a.get("id")]
    ids = {a["id"] for a in listing}
    state = {key: e for key, e in load_state().items() if key in ids}
    # One `paseo usage` call per pass at most, one claude-rotate call per distinct directory (plus the plain
    # one for model-limit resets), each only when an agent needs it.
    status, codex = functools.cache(rotate_status), functools.cache(codex_reset)
    for listed in listing:
        try:
            _check(listed, state, now, dry_run=dry_run, reload=reload, status=status, codex=codex)
        except Exception as exc:  # one odd record or reply must not cost every other agent its pass
            _log(now, f"{str(listed['id'])[:7]}: skipped, unexpected {exc!r}")
    if not dry_run:
        save_state(state)


def main(argv: list[str] | None = None) -> int:
    """The `paseo-limit-watch` command line: one pass, exit code 0."""
    parser = argparse.ArgumentParser(
        prog="paseo-limit-watch",
        description="Resume paseo agents that stopped on a usage limit once it resets (one pass, for cron).",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print what it would do; send and write nothing"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="restart a Claude agent launched through claude-rotate whose reset is over 45 minutes away "
        "when another account is free, then resume it",
    )
    args = parser.parse_args(argv)
    run_once(datetime.now().astimezone(), dry_run=args.dry_run, reload=args.reload)
    return 0
