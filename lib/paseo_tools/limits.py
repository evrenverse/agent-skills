"""Recognize usage-limit and transient-error messages and compute when a limit resets.

Provider-agnostic: `BLOCKERS` is the only place that knows a provider's wording. When a provider words its
limit differently, add a row there and a real sample message to the tests.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# Real blocker messages are short and stand alone (a limit ~60 characters, a 529 ~155). An agent that writes
# about limits (a report, documentation) must not count as blocked.
MAX_BLOCKER_LEN = 400


@dataclass(frozen=True)
class Blocker:
    """What stopped an agent.

    `timed`: a reset will come, wait for it; otherwise the error is transient, retry now. `spend_cap`: a cap a
    person set, not known to lift with a usage window. `model`: the model a Claude model limit names.
    """

    timed: bool
    spend_cap: bool = False
    model: str | None = None


# Words in "your <word> limit" that name a window or plan, not a model.
_NOT_MODELS = frozenset({"session", "weekly", "usage"})
_MODEL_RE = re.compile(r"your (\w+) limit", re.IGNORECASE)


def _limit(match: re.Match[str]) -> Blocker:
    word = _MODEL_RE.search(match.group(0))
    model = word.group(1).lower() if word else None
    return Blocker(timed=True, model=None if model in _NOT_MODELS else model)


def _always(blocker: Blocker) -> Callable[[re.Match[str]], Blocker]:
    return lambda _match: blocker


# The first matching row wins.
BLOCKERS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], Blocker]], ...] = (
    # Codex: "[System Error] You hit your spend cap set by the owner of your workspace. ..."
    # Claude: "You've hit your monthly spend limit. Switch to another model, ..." (before the limit row,
    # which would take it for a plain limit).
    (
        re.compile(r"hit your (?:[\w']+\s+)*spend (?:cap|limit)\b|spend cap set by the owner", re.IGNORECASE),
        _always(Blocker(timed=True, spend_cap=True)),
    ),
    # Claude: "You've hit your session limit · resets 4am", "... your weekly limit ...",
    # "... your Opus limit ...", "You've reached your Fable limit. Switch to another model, ..."
    # Codex: "You've hit your usage limit. ... try again at 3:23 PM."
    (re.compile(r"(?:hit|reached) your (?:[a-z0-9_-]+\s+)*limit", re.IGNORECASE), _limit),
    # Anthropic: "API Error: 529 Overloaded. This is a server-side issue, usually temporary — try again in a
    # moment."
    (re.compile(r"\bAPI Error:\s*5\d\d\b", re.IGNORECASE), _always(Blocker(timed=False))),
    # Claude Code after its own retries: "API Error: Repeated 529 Overloaded errors"
    (re.compile(r"\b(?:529|503)\b[^\n]{0,20}\boverloaded\b", re.IGNORECASE), _always(Blocker(timed=False))),
    # A drop without a status code: "API Error: Connection closed mid-response. ...",
    # "API Error: Connection error."
    (re.compile(r"API Error:\s*Connection\b", re.IGNORECASE), _always(Blocker(timed=False))),
    # Claude Code ends the turn with exactly this sentence. It is common enough in prose that, unlike the
    # other rows, it must be the whole message.
    (re.compile(r"^(?:API Error:\s*)?Request timed out\.?$", re.IGNORECASE), _always(Blocker(timed=False))),
    # Gemini through Claude Code: "API Error: Request rejected (429) · Individual quota reached. ... Resets in
    # 3h10m55s." A duration, not a clock time. Must precede the untimed row below.
    (re.compile(r"Request rejected \(429\)[^\n]*\bResets in\b", re.IGNORECASE), _always(Blocker(timed=True))),
    # Same provider, a per-minute quota without a reset:
    # "API Error: Request rejected (429) · Resource has been exhausted"
    (re.compile(r"Request rejected \(429\)", re.IGNORECASE), _always(Blocker(timed=False))),
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
# "resets 4am", "resets 1:30pm (Europe/Berlin)", "resets Sep 8, 1:59am (...)", "resets Tue 1:59am (...)",
# "try again at 3:23 PM."
_TIME_RE = re.compile(
    r"(?:resets|try again at)\s+"
    r"(?:(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*(?:,\s*|\s+))?"
    r"(?:([A-Za-z]{3,9})\s+(\d{1,2})(?:,\s+(\d{4}))?,\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?"
    r"(?:\s*\(([A-Za-z_]+(?:/[A-Za-z0-9_+-]+)+)\))?",
    re.IGNORECASE,
)
# Gemini: "Resets in 3h10m55s", "Resets in 45m3s", "Resets in 2h".
_DURATION_RE = re.compile(r"Resets in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?\b", re.IGNORECASE)


def classify(text: str) -> Blocker | None:
    """The blocker `text` reports, or None when it is no blocker or too long to be one on its own."""
    text = (text or "").strip()
    if not text or len(text) > MAX_BLOCKER_LEN:
        return None
    for pattern, blocker in BLOCKERS:
        if match := pattern.search(text):
            return blocker(match)
    return None


def parse_reset(text: str, *, written_at: datetime) -> datetime | None:
    """When the limit `text` names resets, or None when it names no usable time.

    Every form is anchored at `written_at` (aware: when the message was written), never at "now", so no day
    has to be guessed: a clock time is its first occurrence at or after `written_at`; a date without a year is
    its first occurrence on or after the day before `written_at`; a weekday with a time is its first
    occurrence at or after `written_at`; a duration is added to `written_at`. A zone named in the text wins,
    else `written_at`'s.
    """
    match = _TIME_RE.search(text or "")
    if not match:
        found = _DURATION_RE.search(text or "")
        if not found or not any(found.groups()):
            return None
        hours, minutes, seconds = (int(group or 0) for group in found.groups())
        # Elapsed time, not wall-clock time: across a DST change the two differ by an hour.
        reset = written_at.astimezone(timezone.utc) + timedelta(hours=hours, minutes=minutes, seconds=seconds)
        return reset.astimezone(written_at.tzinfo)
    weekday, month_name, day, year, hour, minute, half, zone = match.groups()
    try:
        tz = ZoneInfo(zone) if zone else written_at.tzinfo
        local = written_at.astimezone(tz)
        if month_name:
            month = _MONTHS[month_name[:3].lower()]
            if year:
                on = date(int(year), month, int(day))
            else:
                # One day of slack: a message read just after its reset means that date, not next year's.
                floor = local.date() - timedelta(days=1)
                on = date(floor.year, month, int(day))
                if on < floor:
                    on = date(floor.year + 1, month, int(day))
        elif weekday:
            on = local.date() + timedelta(days=(_WEEKDAYS[weekday[:3].lower()] - local.weekday()) % 7)
        else:
            on = local.date()
        clock = time(int(hour) % 12 + (12 if half.lower() == "p" else 0), int(minute or 0))
        reset = datetime.combine(on, clock, tzinfo=tz)
    # Unknown month or zone (ZoneInfoNotFoundError is a KeyError), or an invalid date.
    except (KeyError, ValueError):
        return None
    if not month_name and reset < local:
        # written_at is when the limit was hit, so its reset lies ahead: a time already past that day means
        # tomorrow, a weekday already past means next week.
        reset += timedelta(days=7 if weekday else 1)
    return reset
