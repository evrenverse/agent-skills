"""Tests for paseo_tools.limit_watch.

Nothing here reaches a real paseo or claude-rotate (`cli.run` is faked) or forks (`notify.arm` is patched).
Every agent has a record on disk and, unless it is a Codex agent, a synthetic Claude Code transcript.
"""

import io
import json
import os
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from paseo_tools import cli, limit_watch, notify
from paseo_tools.limit_watch import MAX_TRIES, RESUME_LINE, Entry
from tests.support import FakeRun, TempHome, write_json

AGENT = "11111111-1111-4111-8111-111111111111"
SESSION = "22222222-2222-4222-8222-222222222222"
CALLER = "33333333-3333-4333-8333-333333333333"
CALLER2 = "44444444-4444-4444-8444-444444444444"
OTHER_SESSION = "55555555-5555-4555-8555-555555555555"
UTC = timezone.utc
WRITTEN = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)  # when the agent wrote its limit message
RESET = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
SESSION_LIMIT = "You've hit your session limit · resets 8pm (Etc/UTC)"
FABLE_LIMIT = "You've reached your Fable limit. Switch to another model, or wait for it to reset."
OVERLOADED = (
    "API Error: 529 Overloaded. This is a server-side issue, usually temporary — try again in a moment."
)
CODEX_AT = (
    "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
    "https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 3:23 PM."
)
CODEX_LIMIT = (
    "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro) or try again later."
)
CODEX_SPEND_CAP = (
    "[System Error] You hit your spend cap set by the owner of your workspace. "
    "Ask an owner to increase your spend cap to continue."
)
BASH_START = '{"toolUseResult":{"stdout":"","backgroundTaskId":"b0000task1","timedOutAfterMs":120000}}'
LS = ("ls", "-g", "--background", "--json")
ROTATE = ("status", "--json")
USAGE = ("usage", "ls", "--json", "--refresh")


def done(stdout: str = "", code: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, stderr)


def minutes(n: int) -> timedelta:
    return timedelta(minutes=n)


def listed(agent: str = AGENT, **extra: Any) -> dict:
    """An entry of `paseo ls -g --background --json`."""
    return {
        "id": agent,
        "name": "Build the thing",
        "status": "idle",
        "provider": "claude/claude-opus-5-5",
        **extra,
    }


def account(name: str, sessions: list[str] | None, *, fable_secs: int = 3600, **extra: Any) -> dict:
    """An account of `claude-rotate status --json`; `sessions=None` leaves out `claude_sessions`."""
    acc = {
        "name": name,
        "status": "ok",
        "disabled": False,
        "h5_pct": 10.0,
        "w7_pct": 20.0,
        "w7_scoped": [{"label": "fable", "pct": 30.0, "reset_secs": fable_secs}],
        **extra,
    }
    if sessions is not None:
        acc["claude_sessions"] = sessions
    return acc


def rotate(*accounts: dict, chosen: str = "a") -> subprocess.CompletedProcess[str]:
    return done(json.dumps({"chosen": chosen, "accounts": list(accounts)}))


def said(text: str, written: datetime) -> str:
    """A main-chain assistant line of a Claude Code transcript."""
    return json.dumps(
        {
            "type": "assistant",
            "isSidechain": False,
            "timestamp": written.isoformat().replace("+00:00", "Z"),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }
    )


def resumes(fake: FakeRun) -> list[list[str]]:
    return [call for call in fake.calls if call[1] == "send" and call[5] == AGENT]


def told(fake: FakeRun, caller: str = CALLER) -> list[str]:
    """The bodies paseo-notify sent `caller`."""
    return [call[6] for call in fake.calls if call[1] == "send" and call[5] == caller]


def reloads(fake: FakeRun) -> list[list[str]]:
    return [call for call in fake.calls if call[1:3] == ["agent", "reload"]]


class WatchCase(TempHome):
    """HOME in a temp dir, stdout captured, `notify.arm` patched; `watch` runs one pass on a faked paseo."""

    def setUp(self) -> None:
        super().setUp()
        self.out = io.StringIO()
        for patch in (mock.patch.object(sys, "stdout", self.out), mock.patch.object(notify, "arm")):
            patch.start()
            self.addCleanup(patch.stop)
        self.listing = [listed()]
        self.replies: dict[tuple[str, ...], subprocess.CompletedProcess[str]] = {}

    def agent(
        self,
        text: str,
        *,
        written: datetime = WRITTEN,
        before: tuple[str, ...] = (),
        provider: str = "claude",
        **extra: Any,
    ) -> None:
        """The agent's record and its last message `text` (a Claude transcript; for Codex, `paseo logs`)."""
        write_json(
            self.home / ".paseo" / "agents" / "ws" / f"{AGENT}.json",
            {"id": AGENT, "title": "Build the thing", "provider": provider, **extra}
            | {"persistence": {"sessionId": SESSION}},
        )
        if provider == "codex":
            self.replies[("logs", AGENT)] = done(text + "\n")
            return
        self.transcript().parent.mkdir(parents=True, exist_ok=True)
        self.transcript().write_text("\n".join([*before, said(text, written)]) + "\n")

    def transcript(self) -> Path:
        return self.home / ".claude" / "projects" / "-work" / f"{SESSION}.jsonl"

    def park(self, *callers: str) -> None:
        for caller in callers:
            notify.marker(AGENT, caller).parent.mkdir(parents=True, exist_ok=True)
            notify.marker(AGENT, caller).touch()

    def claim(self, body: str = '{"by": "' + CALLER + '"}') -> None:
        path = self.home / ".paseo" / "takeovers" / f"{AGENT}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)

    def watch(self, now: datetime, *, dry_run: bool = False, reload: bool = False) -> FakeRun:
        table = {
            LS: done(json.dumps(self.listing)),
            ("inspect",): done(json.dumps({"Id": AGENT, "Name": "Build the thing", "Archived": False})),
            ("send",): done(),
            ("agent", "reload"): done(),
            **self.replies,
        }
        fake = FakeRun(table)
        with mock.patch.object(cli, "run", fake):
            limit_watch.run_once(now, dry_run=dry_run, reload=reload)
        return fake

    def state(self) -> dict[str, Entry]:
        return limit_watch.load_state()


class ClaimTests(WatchCase):
    def test_claimed_agent_is_never_resumed(self) -> None:
        self.agent(SESSION_LIMIT)
        self.claim()
        self.assertEqual(resumes(self.watch(RESET + minutes(5))), [])
        self.assertNotIn(AGENT, self.state())

    def test_unclaimed_agent_is_still_resumed(self) -> None:
        """Without this the claimed test would pass on a watcher that never sends."""
        self.agent(SESSION_LIMIT)
        self.assertEqual(len(resumes(self.watch(RESET + minutes(5)))), 1)

    def test_a_corrupt_takeover_marker_still_counts_as_claimed(self) -> None:
        """Fail closed: half a marker still means someone is on this work."""
        self.agent(SESSION_LIMIT)
        self.claim("{partially written")
        self.assertTrue(limit_watch.claimed(AGENT))
        self.assertEqual(resumes(self.watch(RESET + minutes(5))), [])

    def test_send_time_check_refuses_a_corrupt_marker(self) -> None:
        """The last guard before `paseo send`: a claim made after `paseo ls` still stops the resume."""
        self.claim("{partially written")
        fake = FakeRun({("inspect",): done(json.dumps({"Id": AGENT})), ("send",): done()})
        with mock.patch.object(cli, "run", fake):
            limit_watch._resume(AGENT, Entry("m", RESET), RESET, False)
        self.assertEqual(fake.calls, [])

    def test_claimed_agent_releases_parked_watches_with_a_reason(self) -> None:
        self.agent(SESSION_LIMIT)
        self.claim()
        self.park(CALLER)
        (body,) = told(self.watch(RESET + minutes(5)))
        self.assertIn("was taken over by another agent", body)
        self.assertEqual(notify.parked_callers(AGENT), [])
        notify.arm.assert_not_called()
        self.assertEqual(told(self.watch(RESET + minutes(8))), [])  # told once


class ResumeTests(WatchCase):
    def test_resumes_one_minute_after_the_reset_not_before(self) -> None:
        self.agent(SESSION_LIMIT)
        self.assertEqual(resumes(self.watch(RESET)), [])
        self.assertEqual(len(resumes(self.watch(RESET + minutes(1)))), 1)

    def test_resume_steers_and_does_not_wait(self) -> None:
        """From cron `paseo send` defaults to interrupting: a turn started meanwhile would die."""
        self.agent(SESSION_LIMIT)
        (call,) = resumes(self.watch(RESET + minutes(1)))
        self.assertEqual(call[1:], ["send", "--behavior", "steer", "--no-wait", AGENT, RESUME_LINE])

    def test_retries_every_30_minutes_at_most_three_times(self) -> None:
        self.agent(SESSION_LIMIT)
        sent = [len(resumes(self.watch(RESET + minutes(m)))) for m in (1, 20, 31, 45, 61, 91, 200)]
        self.assertEqual(sent, [1, 0, 1, 0, 1, 0, 0])
        self.assertEqual(self.state()[AGENT].attempts, MAX_TRIES)

    def test_transient_error_is_resumed_at_once_under_the_same_cap(self) -> None:
        self.agent(OVERLOADED)
        self.assertEqual([len(resumes(self.watch(WRITTEN + minutes(m)))) for m in (0, 3, 30)], [1, 0, 1])
        # A fresh 529 is the same sighting: the count goes on, or a lasting outage is resumed forever.
        self.agent(OVERLOADED, written=WRITTEN + minutes(40))
        self.assertEqual([len(resumes(self.watch(WRITTEN + minutes(m)))) for m in (60, 90, 200)], [1, 0, 0])

    def test_never_sends_to_an_agent_archived_since_listing(self) -> None:
        self.agent(SESSION_LIMIT)
        self.replies[("inspect", AGENT, "--json")] = done(json.dumps({"Id": AGENT, "Archived": True}))
        fake = self.watch(RESET + minutes(1))
        self.assertIn(["inspect", AGENT, "--json"], [call[1:] for call in fake.calls])
        self.assertEqual(resumes(fake), [])
        self.assertEqual(self.state()[AGENT].attempts, 0)

    def test_failed_send_is_not_an_attempt(self) -> None:
        self.agent(SESSION_LIMIT)
        self.replies[("send",)] = done("", 1, "connection refused")
        self.assertEqual(len(resumes(self.watch(RESET + minutes(1)))), 1)
        self.assertEqual(self.state()[AGENT].attempts, 0)
        del self.replies[("send",)]
        self.assertEqual(len(resumes(self.watch(RESET + minutes(4)))), 1)  # the next pass, not 30 min later
        self.assertEqual(self.state()[AGENT].attempts, 1)

    def test_parked_watches_are_rearmed_after_a_resume(self) -> None:
        self.agent(SESSION_LIMIT)
        self.park(CALLER, CALLER2)
        self.watch(RESET + minutes(1))
        self.assertEqual(notify.arm.call_args_list, [mock.call(AGENT, CALLER), mock.call(AGENT, CALLER2)])
        self.assertEqual(notify.parked_callers(AGENT), [])

    def test_loop_agent_is_resumed(self) -> None:
        """A nightly loop: a plain agent a cron script started, no parent, `closed` after a daemon restart."""
        self.listing = [listed(name="NIGHTLY: loop", status="closed")]
        self.agent(SESSION_LIMIT, title="NIGHTLY: loop")
        self.assertEqual(len(resumes(self.watch(RESET + minutes(1)))), 1)


class StateTests(WatchCase):
    def test_running_agent_drops_its_state(self) -> None:
        self.agent(SESSION_LIMIT)
        self.watch(RESET + minutes(1))
        self.assertIn(AGENT, self.state())
        self.listing = [listed(status="running")]
        self.watch(RESET + minutes(4))
        self.assertNotIn(AGENT, self.state())

    def test_vanished_agent_drops_its_state(self) -> None:
        self.agent(SESSION_LIMIT)
        self.watch(RESET + minutes(1))
        self.listing = []
        self.watch(RESET + minutes(4))
        self.assertNotIn(AGENT, self.state())

    def test_recovered_agent_drops_its_state(self) -> None:
        self.agent(SESSION_LIMIT)
        self.watch(RESET + minutes(1))
        self.agent("Done: the upload retries now.", written=RESET + minutes(2))
        self.watch(RESET + minutes(4))
        self.assertNotIn(AGENT, self.state())

    def test_dry_run_sends_nothing_and_writes_no_state(self) -> None:
        write_json(
            self.home / ".paseo" / "config.json",
            {"agents": {"providers": {"claude": {"command": ["claude-rotate", "run"]}}}},
        )
        self.replies[ROTATE] = rotate(account("a", [OTHER_SESSION]), account("b", [SESSION]))
        self.agent(SESSION_LIMIT)
        self.park(CALLER)
        fake = self.watch(WRITTEN + minutes(5), dry_run=True, reload=True)
        self.assertEqual(resumes(fake) + reloads(fake), [])
        self.assertFalse(limit_watch.state_path().exists())
        self.assertEqual(notify.parked_callers(AGENT), [CALLER])
        notify.arm.assert_not_called()
        self.assertIn("would reload", self.out.getvalue())
        self.assertIn("would send", self.out.getvalue())

    def test_state_path_follows_xdg_state_home(self) -> None:
        self.assertEqual(
            limit_watch.state_path(), self.home / ".local" / "state" / "paseo-limit-watch" / "state.json"
        )
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.home / "xdg")}):
            self.assertEqual(limit_watch.state_path(), self.home / "xdg" / "paseo-limit-watch" / "state.json")


class ListingTests(WatchCase):
    def saved_state(self) -> bytes:
        self.agent(SESSION_LIMIT)
        self.watch(RESET + minutes(1))
        return limit_watch.state_path().read_bytes()

    def test_unparseable_paseo_listing_skips_the_pass_and_keeps_state(self) -> None:
        before = self.saved_state()
        for label, reply in {
            "not JSON": done("<html>502</html>"),
            "not a list": done('{"error": 1}'),
        }.items():
            with self.subTest(label):
                self.replies[LS] = reply
                fake = self.watch(RESET + minutes(31))
                self.assertEqual([call[1:] for call in fake.calls], [list(LS)])
                self.assertEqual(limit_watch.state_path().read_bytes(), before)
                self.assertIn("pass skipped", self.out.getvalue())

    def test_daemon_down_never_starts_the_daemon(self) -> None:
        before = self.saved_state()
        failures = {
            "refused": done("", 1, "Error: connect ECONNREFUSED 127.0.0.1:6767"),
            "timed out": done("", 124, "timed out after 60s: paseo"),
            "not JSON": done("Daemon is not running"),
        }
        for label, reply in failures.items():
            with self.subTest(label):
                self.out.seek(0)
                self.out.truncate()
                self.replies[LS] = reply
                now = RESET + minutes(31)
                fake = self.watch(now)
                self.assertEqual(limit_watch.state_path().read_bytes(), before)
                # The daemon belongs to systemd: a failed listing is logged, never fixed.
                self.assertFalse(
                    [c for c in fake.calls if "daemon" in " ".join(c) or "restart" in " ".join(c)]
                )
                (line,) = self.out.getvalue().splitlines()
                self.assertTrue(line.startswith(now.isoformat(timespec="seconds")))


class ZoneTests(WatchCase):
    def system_zone(self, name: str) -> None:
        self.addCleanup(time.tzset)
        patch = mock.patch.dict(os.environ, {"TZ": name})
        patch.start()
        self.addCleanup(patch.stop)
        time.tzset()

    def test_reset_uses_the_system_zone_when_the_message_names_none(self) -> None:
        self.system_zone("Europe/Berlin")
        self.agent("You've hit your session limit · resets 11pm")  # written 18:00 UTC, 20:00 in Berlin
        self.watch(WRITTEN + minutes(5))
        self.assertEqual(self.state()[AGENT].reset, datetime(2026, 10, 20, 21, 0, tzinfo=UTC))

    def test_message_zone_wins_over_system_zone(self) -> None:
        self.system_zone("Europe/Berlin")
        self.agent("You've hit your session limit · resets 4am (America/New_York)")
        self.watch(WRITTEN + minutes(5))
        self.assertEqual(self.state()[AGENT].reset, datetime(2026, 10, 21, 8, 0, tzinfo=UTC))

    def test_codex_clock_time_is_fixed_at_first_sighting(self) -> None:
        self.listing = [listed(provider="codex/gpt-5.5")]
        self.agent(CODEX_AT, provider="codex")  # through `paseo logs`: no timestamp
        self.assertEqual(resumes(self.watch(datetime(2026, 10, 20, 15, 0, tzinfo=UTC))), [])
        self.assertEqual(self.state()[AGENT].reset, datetime(2026, 10, 20, 15, 23, tzinfo=UTC))
        # Read afresh at 15:30, "3:23 PM" would be tomorrow's.
        self.assertEqual(len(resumes(self.watch(datetime(2026, 10, 20, 15, 30, tzinfo=UTC)))), 1)


class ResetSourceTests(WatchCase):
    def test_codex_usage_limit_uses_the_exhausted_window(self) -> None:
        self.listing = [listed(provider="codex/gpt-5.5")]
        self.agent(CODEX_LIMIT, provider="codex")
        windows = [
            {
                "sourceId": "copilot",
                "report": {"windows": [{"usedPct": 100, "resetsAt": "2026-10-21T00:00:00Z"}]},
            },
            {
                "sourceId": "codex",
                "report": {
                    "windows": [
                        {"id": "five_hour", "usedPct": 40, "resetsAt": "2026-10-20T21:33:17.000Z"},
                        {"id": "weekly", "usedPct": 100, "resetsAt": "2026-10-27T07:03:47.000Z"},
                    ]
                },
            },
        ]
        self.replies[USAGE] = done(json.dumps(windows))
        weekly = datetime(2026, 10, 27, 7, 3, 47, tzinfo=UTC)
        self.assertEqual(resumes(self.watch(RESET)), [])
        self.assertEqual(self.state()[AGENT].reset, weekly)
        self.assertEqual(len(resumes(self.watch(weekly + minutes(1)))), 1)

    def test_model_limit_uses_the_agents_own_account(self) -> None:
        self.agent(FABLE_LIMIT)
        self.replies[ROTATE] = rotate(
            account("a", [OTHER_SESSION], fable_secs=600), account("b", [SESSION], fable_secs=7200)
        )
        now = WRITTEN + minutes(5)
        self.watch(now)
        self.assertEqual(self.state()[AGENT].reset, now + timedelta(hours=2))

    def test_model_limit_without_rotate_or_session_field_is_unknown(self) -> None:
        self.agent(FABLE_LIMIT)
        variants = {
            "binary missing": done("", 127, "No such file or directory: 'claude-rotate'"),
            "non-zero exit": done("", 1, "boom"),
            "no claude_sessions key": rotate(account("b", None), chosen="b"),
        }
        for label, reply in variants.items():
            with self.subTest(label):
                limit_watch.state_path().unlink(missing_ok=True)
                self.park(CALLER)
                self.replies[ROTATE] = reply
                fake = self.watch(WRITTEN + minutes(5))
                self.assertIsNone(self.state()[AGENT].reset)
                self.assertEqual(resumes(fake), [])
                (body,) = told(fake)
                self.assertIn("cannot tell when it resets", body)

    def test_spend_cap_reports_unknown_reset_to_parked_callers(self) -> None:
        self.listing = [listed(provider="codex/gpt-5.5")]
        self.agent(CODEX_SPEND_CAP, provider="codex")
        self.park(CALLER)
        fake = self.watch(RESET)
        self.assertEqual(resumes(fake), [])
        # A cap a person set is not known to lift with a usage window.
        self.assertNotIn(list(USAGE), [call[1:] for call in fake.calls])
        self.assertIsNone(self.state()[AGENT].reset)
        (body,) = told(fake)
        self.assertIn("cannot tell when it resets", body)
        self.assertIn("spend cap", body)
        self.assertEqual(notify.parked_callers(AGENT), [])

    def test_exhausted_retries_report_to_parked_callers(self) -> None:
        self.agent(SESSION_LIMIT)
        for m in (1, 31, 61):
            self.watch(RESET + minutes(m))
        self.park(CALLER)  # the third resume stopped on the limit again and its watch parked
        (body,) = told(self.watch(RESET + minutes(91)))
        self.assertIn(f"still blocked after {MAX_TRIES} resume attempts", body)
        self.assertEqual(notify.parked_callers(AGENT), [])
        self.assertEqual(told(self.watch(RESET + minutes(94))), [])


class PassTests(WatchCase):
    """Several agents in one pass."""

    def codex_agent(self, agent: str) -> None:
        """A second Codex agent, blocked on a usage limit without a time."""
        self.listing.append(listed(agent, provider="codex/gpt-5.5"))
        write_json(
            self.home / ".paseo" / "agents" / "ws" / f"{agent}.json",
            {"id": agent, "title": "Other", "provider": "codex", "persistence": {"sessionId": agent}},
        )
        self.replies[("logs", agent)] = done(CODEX_LIMIT + "\n")

    def test_one_failing_agent_does_not_end_the_pass(self) -> None:
        self.codex_agent(CALLER)
        self.listing.reverse()  # the failing agent first
        self.agent(SESSION_LIMIT)
        self.replies[USAGE] = done(json.dumps([{"sourceId": "codex", "report": "not an object"}]))
        fake = self.watch(RESET + minutes(1))
        self.assertEqual(len(resumes(fake)), 1)
        self.assertEqual(self.state()[AGENT].attempts, 1)
        self.assertNotIn(CALLER, self.state())
        (line,) = [line for line in self.out.getvalue().splitlines() if CALLER[:7] in line]
        self.assertIn("AttributeError", line)

    def test_paseo_usage_is_asked_once_per_pass(self) -> None:
        self.listing = []
        self.codex_agent(CALLER)
        self.codex_agent(CALLER2)
        self.replies[USAGE] = done("[]")
        fake = self.watch(RESET)
        self.assertEqual([call[1:] for call in fake.calls].count(list(USAGE)), 1)


class ReloadTests(WatchCase):
    FAR = WRITTEN + minutes(5)  # the reset is almost two hours away

    def setUp(self) -> None:
        super().setUp()
        self.config = self.home / ".paseo" / "config.json"
        write_json(
            self.config, {"agents": {"providers": {"claude": {"command": ["claude-rotate", "run", "--"]}}}}
        )
        self.replies[ROTATE] = rotate(account("a", [OTHER_SESSION]), account("b", [SESSION]))
        self.agent(SESSION_LIMIT)

    def test_reload_when_reset_is_far_and_another_account_is_free(self) -> None:
        variants = {
            "another account is free": rotate(account("a", [OTHER_SESSION]), account("b", [SESSION])),
            "no session mapping, the chosen account is free": rotate(account("a", None), chosen="a"),
        }
        for label, status in variants.items():
            with self.subTest(label):
                limit_watch.state_path().unlink(missing_ok=True)
                self.replies[ROTATE] = status
                fake = self.watch(self.FAR, reload=True)
                self.assertEqual(
                    [call[1:3] for call in fake.calls if call[1] in ("agent", "send")],
                    [["agent", "reload"], ["send", "--behavior"]],
                )
                self.assertEqual(reloads(fake)[0][1:], ["agent", "reload", AGENT])
                entry = self.state()[AGENT]
                self.assertEqual((entry.reloaded, entry.attempts), (True, 1))

    def test_no_reload_without_a_usable_account_to_move_to(self) -> None:
        variants = {
            "only the agent's own account": rotate(
                account("a", [OTHER_SESSION], h5_pct=100.0), account("b", [SESSION])
            ),
            "the other one needs a login": rotate(
                account("a", [OTHER_SESSION], status="relogin"), account("b", [SESSION])
            ),
            "chosen is used up": rotate(account("a", None, w7_pct=100.0), chosen="a"),
        }
        for label, status in variants.items():
            with self.subTest(label):
                self.replies[ROTATE] = status
                fake = self.watch(self.FAR, reload=True)
                self.assertEqual(reloads(fake) + resumes(fake), [])

    def test_no_reload_when_reset_is_near(self) -> None:
        fake = self.watch(RESET - minutes(40), reload=True)
        self.assertEqual(reloads(fake) + resumes(fake), [])

    def test_no_reload_when_reset_is_unknown(self) -> None:
        """An unknown reset is not "far away": it degrades to telling the callers, never to a guess."""
        self.agent(FABLE_LIMIT)
        self.replies[ROTATE] = rotate(
            account("a", None), chosen="a"
        )  # a free account, but no session mapping
        self.park(CALLER)
        fake = self.watch(self.FAR, reload=True)
        self.assertEqual(reloads(fake) + resumes(fake), [])
        self.assertIsNone(self.state()[AGENT].reset)
        (body,) = told(fake)
        self.assertIn("cannot tell when it resets", body)

    def test_no_reload_with_background_work_pending(self) -> None:
        self.agent(SESSION_LIMIT, before=(BASH_START,))
        fake = self.watch(self.FAR, reload=True)
        self.assertEqual(reloads(fake) + resumes(fake), [])

    def test_no_reload_when_claude_is_not_launched_through_claude_rotate(self) -> None:
        for label, config in {"plain claude": ["claude"], "no command": None, "no config": "absent"}.items():
            with self.subTest(label):
                if config == "absent":
                    self.config.unlink()
                else:
                    write_json(self.config, {"agents": {"providers": {"claude": {"command": config}}}})
                fake = self.watch(self.FAR, reload=True)
                self.assertEqual(reloads(fake) + resumes(fake), [])

    def test_reload_only_once_per_reset(self) -> None:
        self.assertEqual(len(reloads(self.watch(self.FAR, reload=True))), 1)
        fake = self.watch(self.FAR + minutes(40), reload=True)  # still blocked by the same message
        self.assertEqual(reloads(fake) + resumes(fake), [])
        fake = self.watch(RESET + minutes(1), reload=True)  # back to waiting for the reset
        self.assertEqual((len(reloads(fake)), len(resumes(fake))), (0, 1))

    def test_failed_reload_falls_back_to_waiting(self) -> None:
        self.replies[("agent", "reload")] = done("", 1, "reload failed")
        fake = self.watch(self.FAR, reload=True)
        self.assertEqual((len(reloads(fake)), resumes(fake)), (1, []))
        entry = self.state()[AGENT]
        self.assertEqual((entry.reloaded, entry.attempts), (True, 0))
        fake = self.watch(self.FAR + minutes(3), reload=True)
        self.assertEqual(reloads(fake) + resumes(fake), [])
        self.assertEqual(len(resumes(self.watch(RESET + minutes(1), reload=True))), 1)

    def test_no_reload_without_the_flag(self) -> None:
        fake = self.watch(self.FAR)
        self.assertEqual(reloads(fake) + resumes(fake), [])
        self.assertNotIn(list(ROTATE), [call[1:] for call in fake.calls])

    def test_no_reload_of_a_turn_started_since_the_listing(self) -> None:
        """A reload kills the running turn: the refusal spends neither an attempt nor the one reload."""
        running = {"Id": AGENT, "Archived": False, "Status": "running"}
        self.replies[("inspect", AGENT, "--json")] = done(json.dumps(running))
        fake = self.watch(self.FAR, reload=True)
        self.assertEqual(reloads(fake) + resumes(fake), [])
        entry = self.state()[AGENT]
        self.assertEqual((entry.reloaded, entry.attempts), (False, 0))
        del self.replies[("inspect", AGENT, "--json")]  # the turn ended on the same limit
        self.assertEqual(len(reloads(self.watch(self.FAR + minutes(3), reload=True))), 1)

    def test_transient_and_codex_blockers_never_ask_claude_rotate(self) -> None:
        """claude-rotate may probe its accounts: it is asked only when a reload can follow."""
        weekly = {"usedPct": 100, "resetsAt": "2026-10-27T07:03:47.000Z"}  # far away, known
        self.replies[USAGE] = done(json.dumps([{"sourceId": "codex", "report": {"windows": [weekly]}}]))
        variants = {
            "transient": (OVERLOADED, "claude", {}),
            "codex": (CODEX_LIMIT, "codex", {"provider": "codex/gpt-5.5"}),
        }
        for label, (text, provider, extra) in variants.items():
            with self.subTest(label):
                limit_watch.state_path().unlink(missing_ok=True)
                self.transcript().unlink(missing_ok=True)
                self.listing = [listed(**extra)]
                self.agent(text, provider=provider)
                fake = self.watch(self.FAR, reload=True)
                self.assertNotIn(list(ROTATE), [call[1:] for call in fake.calls])
                self.assertIsNotNone(self.state()[AGENT].reset)

    def test_a_limit_written_again_keeps_the_caps_of_its_reset(self) -> None:
        """A resume or reload that does not cure the block makes Claude Code write the limit again, newly
        stamped: the three attempts and the one reload still hold per reset, not per message."""
        after = RESET + minutes(1)
        dated = "You've hit your session limit · resets Oct 20, 8pm (Etc/UTC)"
        variants = {
            # (a) without the cap, a reload and a resume line on every pass until 45 min before the reset
            "reloaded onto a limited account": (SESSION_LIMIT, self.FAR, True, [0, 117, 147]),
            # (b) written again after its time, a date parses into the past: a resume line on every pass
            "a date": (dated, after, False, [0, 30, 60]),
            # (c) written again after its time, a clock time rolls to tomorrow: no retry for a day
            "a clock time": (SESSION_LIMIT, after, False, [0, 30, 60]),
        }
        for label, (text, start, reload, expected) in variants.items():
            with self.subTest(label):
                limit_watch.state_path().unlink(missing_ok=True)
                self.agent(text)
                sent, reloaded, now = [], 0, start
                while now <= RESET + minutes(150):
                    fake = self.watch(now, reload=reload)
                    reloaded += len(reloads(fake))
                    if resumes(fake):
                        sent.append(now)
                        with self.transcript().open("a") as f:  # still blocked: the limit, 30 s later
                            f.write(said(text, now + timedelta(seconds=30)) + "\n")
                    now += minutes(3)
                self.assertEqual(sent, [start + minutes(m) for m in expected])
                self.assertEqual(reloaded, int(reload))


class MainTests(TempHome):
    def test_main_runs_one_pass_with_its_flags(self) -> None:
        with mock.patch.object(limit_watch, "run_once") as run_once:
            self.assertEqual(limit_watch.main(["--dry-run", "--reload"]), 0)
        (now,), flags = run_once.call_args
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(flags, {"dry_run": True, "reload": True})

    def test_help_works(self) -> None:
        with mock.patch.object(sys, "stdout", io.StringIO()) as out, self.assertRaises(SystemExit) as leave:
            limit_watch.main(["--help"])
        self.assertEqual(leave.exception.code, 0)
        self.assertIn("--reload", out.getvalue())


if __name__ == "__main__":
    unittest.main()
