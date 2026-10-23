"""Tests for paseo_tools.limits."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from paseo_tools import limits
from paseo_tools.limits import Blocker, classify, parse_reset

BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

GEMINI = (
    "API Error: Request rejected (429) · Individual quota reached. Please upgrade your subscription to "
    "increase your limits. Resets in 3h10m55s."
)
FABLE_REACHED = (
    "You've reached your Fable limit. Switch to another model, or manage usage credits at "
    "claude.ai/settings/usage?from=cc_cli_limit_message, to continue."
)
SPEND_CAP = "[System Error] You hit your spend cap set by the owner of your workspace."
OVERLOADED = (
    "API Error: 529 Overloaded. This is a server-side issue, usually temporary — try again in a moment."
)

# Real messages, verbatim. Every pattern in limits.BLOCKERS needs at least one sample here.
BLOCKED = {
    "You've hit your session limit · resets 4am": Blocker(timed=True),
    "You've hit your weekly limit · resets Sep 10, 9pm (Europe/Berlin)": Blocker(timed=True),
    "You've hit your Opus limit · resets 4:00pm": Blocker(timed=True, model="opus"),
    FABLE_REACHED: Blocker(timed=True, model="fable"),
    "You've hit your usage limit. ... try again at 3:23 PM.": Blocker(timed=True),
    SPEND_CAP: Blocker(timed=True, spend_cap=True),
    OVERLOADED: Blocker(timed=False),
    "API Error: 503 Service Unavailable": Blocker(timed=False),
    "API Error: Connection closed mid-response. The response above may be incomplete.": Blocker(timed=False),
    "Request timed out": Blocker(timed=False),
    GEMINI: Blocker(timed=True),
    "API Error: Request rejected (429) · Resource has been exhausted": Blocker(timed=False),
    # More samples, in Claude Code's own wording.
    "You've hit your Fable limit · resets Sep 8, 1:59am (Europe/Berlin)": Blocker(timed=True, model="fable"),
    "You've hit your Sonnet limit · resets 6:30pm (Europe/Berlin)": Blocker(timed=True, model="sonnet"),
    "[System Error] You hit your spend cap set by the owner of your workspace. Ask an owner to increase your "
    "spend cap to continue.": Blocker(timed=True, spend_cap=True),
    "You've hit your monthly spend limit. Switch to another model, or manage usage credits at "
    "claude.ai/settings/usage?from=cc_cli_limit_message, to continue.": Blocker(timed=True, spend_cap=True),
    "API Error: Repeated 529 Overloaded errors": Blocker(timed=False),
    "API Error: Connection error.": Blocker(timed=False),
    "API Error: Request timed out.": Blocker(timed=False),
    "API Error: Request rejected (429) · Resource has been exhausted (e.g. check quota).": Blocker(
        timed=False
    ),
}
NOT_BLOCKED = [
    "",
    "Ignored.",
    "The request timed out on the third try, so I retried with a smaller batch.",
    "x" * 401 + " You've hit your session limit · resets 4am",
]


def first_pattern(text: str) -> object:
    return next(pattern for pattern, _ in limits.BLOCKERS if pattern.search(text))


class ClassifyTests(unittest.TestCase):
    def test_blocked(self) -> None:
        for text, expected in BLOCKED.items():
            with self.subTest(text=text):
                self.assertEqual(classify(text), expected)

    def test_not_blocked(self) -> None:
        for text in NOT_BLOCKED:
            with self.subTest(text=text):
                self.assertIsNone(classify(text))

    def test_every_pattern_has_a_sample(self) -> None:
        recognized_by = {first_pattern(text) for text in BLOCKED}
        for pattern, _ in limits.BLOCKERS:
            with self.subTest(pattern=pattern.pattern):
                self.assertIn(pattern, recognized_by)

    def test_short_message_with_text_around_a_blocker_counts(self) -> None:
        self.assertEqual(classify("Short: API Error: 529 Overloaded, trying again."), Blocker(timed=False))

    def test_report_about_limits_is_not_a_blocker(self) -> None:
        """A report that lists the patterns must not count as blocked."""
        report = (
            "**Example note: how limit messages are handled.** All message patterns live in one table, "
            "and the other modules never compare against provider-specific strings:\n\n"
            "| Pattern | Action |\n|---|---|\n"
            "| `You've hit your session limit · resets 4am` | timed: wait for the reset |\n"
            "| `API Error: 5xx` | transient: resume at once |\n"
            "| `529/503 … Overloaded` | transient: resume at once |\n"
            "| `Request rejected (429) … Resets in` | timed: wait for the stated duration |\n"
            "| `Request timed out` | transient: resume at once, only as the whole message |\n\n"
            "Supporting a new provider means adding one row to the table. Transient blockers name no "
            "time, so they share one fixed state key."
        )
        self.assertGreaterEqual(len(report), 600)
        self.assertTrue(
            any(pattern.search(report) for pattern, _ in limits.BLOCKERS)
        )  # the patterns match ...
        self.assertIsNone(classify(report))  # ... the length guard keeps it from counting


def berlin(*args: int) -> datetime:
    return datetime(*args, tzinfo=BERLIN)


SESSION_4AM = "You've hit your session limit · resets 4am"

# (text, written_at, expected reset)
RESETS = [
    (SESSION_4AM, berlin(2026, 9, 24, 3, 50), berlin(2026, 9, 24, 4, 0)),
    (SESSION_4AM, berlin(2026, 9, 24, 10, 35), berlin(2026, 9, 25, 4, 0)),
    (
        "You've hit your session limit · resets 1:30pm (Europe/Berlin)",
        berlin(2026, 9, 24, 13, 30),
        berlin(2026, 9, 24, 13, 30),
    ),
    (
        "You've hit your Fable limit · resets Sep 8, 1:59am (Europe/Berlin)",
        berlin(2026, 9, 5, 12, 0),
        berlin(2026, 9, 8, 1, 59),
    ),
    (
        "You've hit your weekly limit · resets Jan 2, 1:59am (Europe/Berlin)",
        berlin(2026, 12, 30, 12, 0),
        berlin(2027, 1, 2, 1, 59),
    ),
    (
        "You've hit your weekly limit · resets Tue 1:59am (Europe/Berlin)",
        berlin(2026, 9, 5, 12, 0),  # a Saturday
        berlin(2026, 9, 8, 1, 59),
    ),
    (
        "You've hit your session limit · resets 6:30pm (America/New_York)",
        berlin(2026, 9, 24, 20, 0),
        datetime(2026, 9, 24, 18, 30, tzinfo=NEW_YORK),
    ),
    (
        "You've hit your usage limit. ... try again at 3:23 PM.",
        berlin(2026, 9, 24, 15, 0),
        berlin(2026, 9, 24, 15, 23),
    ),
    (GEMINI, berlin(2026, 9, 5, 10, 0, 0), berlin(2026, 9, 5, 13, 10, 55)),
    (FABLE_REACHED, berlin(2026, 9, 24, 12, 0), None),
    # More clock times, dates, weekdays and durations.
    (
        "You've hit your session limit · resets 1:30am (Europe/Berlin)",
        berlin(2026, 7, 24, 23, 50),
        berlin(2026, 7, 25, 1, 30),
    ),
    (
        "You've hit your session limit · resets 6:30pm (Europe/Berlin)",
        berlin(2026, 7, 24, 18, 30),
        berlin(2026, 7, 24, 18, 30),
    ),
    (
        "You've hit your Fable limit · resets Sep 12, 6:30pm (Europe/Berlin)",
        berlin(2026, 9, 7, 21, 50),
        berlin(2026, 9, 12, 18, 30),
    ),
    (
        "You've hit your Fable limit · resets Tue 1:59am (Europe/Berlin)",
        berlin(2026, 9, 7, 21, 50),
        berlin(2026, 9, 8, 1, 59),
    ),
    ("Resets in 45m3s.", berlin(2026, 9, 5, 10, 0), berlin(2026, 9, 5, 10, 45, 3)),
    ("Resets in 2h", berlin(2026, 9, 5, 10, 0), berlin(2026, 9, 5, 12, 0)),
    ("Resets in a moment.", berlin(2026, 9, 5, 10, 0), None),
    (SPEND_CAP, berlin(2026, 9, 5, 10, 0), None),
    ("", berlin(2026, 9, 5, 10, 0), None),
    ("[User] keep going\nTask 3 continues.", berlin(2026, 9, 5, 10, 0), None),
    ("You've hit your session limit · back soon", berlin(2026, 9, 5, 10, 0), None),
    # A date written just after it passed is still that date, not next year's.
    (
        "You've hit your weekly limit · resets Sep 8, 1:59am (Europe/Berlin)",
        berlin(2026, 9, 8, 3, 0),
        berlin(2026, 9, 8, 1, 59),
    ),
    (
        "You've hit your weekly limit · resets Sep 8, 2027, 1:59am (Europe/Berlin)",
        berlin(2026, 9, 5, 12, 0),
        berlin(2027, 9, 8, 1, 59),
    ),
    ("You've hit your session limit · resets 4am (Mars/Base_One)", berlin(2026, 9, 5, 12, 0), None),
    (
        "You've hit your session limit · resets 4am (America/Argentina/Buenos_Aires)",
        berlin(2026, 9, 24, 3, 50),
        datetime(2026, 9, 24, 4, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires")),
    ),
]


class ParseResetTests(unittest.TestCase):
    def test_resets(self) -> None:
        for text, written_at, expected in RESETS:
            with self.subTest(text=text, written_at=written_at):
                reset = parse_reset(text, written_at=written_at)
                # isoformat compares the instant and the offset, so the zone the result is in counts too.
                self.assertEqual(reset and reset.isoformat(), expected and expected.isoformat())

    def test_weekday_already_past_that_day_is_next_week(self) -> None:
        """A reset never lies before the message; a past one would make the watcher resume in a loop."""
        reset = parse_reset(
            "You've hit your weekly limit · resets Tue 1:59am (Europe/Berlin)",
            written_at=berlin(2026, 9, 8, 10, 0),  # a Tuesday, after 01:59
        )
        self.assertEqual(reset, berlin(2026, 9, 15, 1, 59))

    def test_duration_is_anchored_at_the_line_not_its_zone(self) -> None:
        written = datetime(2026, 9, 5, 19, 14, 31, tzinfo=UTC)
        self.assertEqual(parse_reset(GEMINI, written_at=written), berlin(2026, 9, 6, 0, 25, 26))

    def test_duration_counts_real_time_across_a_dst_change(self) -> None:
        # Berlin falls back from 03:00 to 02:00 on 2026-10-25: 3 h after 01:00 CEST is 03:00 CET, not 04:00.
        reset = parse_reset("Resets in 3h", written_at=berlin(2026, 10, 25, 1, 0))
        self.assertEqual(reset, datetime(2026, 10, 25, 2, 0, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
