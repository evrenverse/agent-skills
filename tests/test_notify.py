"""Tests for paseo_tools.notify. The test process never forks: `_detach` is patched wherever `arm` runs,
and the tests of the real double fork run it in a subprocess."""

import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from paseo_tools import cli, notify
from tests.support import FakeRun, TempHome, write_json

CHILD = "11111111-1111-4111-8111-111111111111"
CALLER = "33333333-3333-4333-8333-333333333333"
SESSION = "22222222-2222-4222-8222-222222222222"
LIMIT = "You've hit your session limit · resets 6:30pm (Europe/Berlin)"
IDLE = json.dumps({"agentId": CHILD, "status": "idle", "message": "Agent is idle.\nLast 5 items:\nDone."})
BASH_START = '{"toolUseResult":{"stdout":"","backgroundTaskId":"b0000task1","timedOutAfterMs":120000}}'


def done(stdout: str = "", code: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, stderr)


def info(agent: str, **extra: Any) -> subprocess.CompletedProcess[str]:
    return done(json.dumps({"Id": agent, "Name": "Build the thing", **extra}))


class OutcomeTests(unittest.TestCase):
    def test_idle_is_sent_with_the_last_activity(self) -> None:
        action, body = notify.outcome(IDLE, "", "Merged to main.")
        self.assertEqual(action, "send")
        self.assertIn("finished its turn", body)
        self.assertIn("Done.", body)

    def test_a_limit_parks_an_idle_agent(self) -> None:
        self.assertEqual(notify.outcome(IDLE, "", LIMIT), ("park", None))

    def test_a_limit_text_does_not_hold_back_a_permission_or_an_error(self) -> None:
        perm = json.dumps({"agentId": CHILD, "status": "permission", "message": "Bash: rm -rf build"})
        self.assertTrue(notify.outcome(perm, "", LIMIT)[1].startswith("is waiting for a permission"))
        err = json.dumps({"agentId": CHILD, "status": "error", "message": "Agent not found"})
        self.assertIn("stopped with an error", notify.outcome(err, "", "")[1])

    def test_an_unknown_status_is_named(self) -> None:
        odd = json.dumps({"agentId": CHILD, "status": "timeout", "message": ""})
        self.assertIn("returned status 'timeout'", notify.outcome(odd, "", "")[1])

    def test_no_json_means_the_caller_hears_the_watch_is_lost(self) -> None:
        action, body = notify.outcome("", "Error: connect ECONNREFUSED 127.0.0.1:6767", "")
        self.assertEqual(action, "send")
        self.assertIn("ECONNREFUSED", body)
        self.assertIn("Re-arm with `paseo-notify <id>`", body)

    def test_long_activity_is_trimmed_to_its_end(self) -> None:
        long = json.dumps({"agentId": CHILD, "status": "idle", "message": "x" * 10_000 + "END"})
        body = notify.outcome(long, "", "")[1]
        self.assertTrue(body.endswith("END"))
        self.assertLess(len(body), notify.MAX_ACTIVITY + 200)


class NotifyCase(TempHome):
    """HOME in a temp dir, `cli.run` faked, stdout and stderr captured, `time.sleep` never really sleeping."""

    def setUp(self) -> None:
        super().setUp()
        self.out, self.err = io.StringIO(), io.StringIO()
        for patch in (
            mock.patch.object(sys, "stdout", self.out),
            mock.patch.object(sys, "stderr", self.err),
            mock.patch.object(notify.time, "sleep"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def paseo(self, **replies: subprocess.CompletedProcess[str]) -> FakeRun:
        """Fake `cli.run`: both agents exist and `send` works; `replies` override one answer by name.

        `caller` and `child` stand for `paseo inspect <that agent>`, any other name for its subcommand.
        """
        table = {
            ("inspect", CALLER, "--json"): info(CALLER),
            ("inspect", CHILD, "--json"): info(CHILD),
            ("send",): done(),
        }
        keys = {"caller": ("inspect", CALLER, "--json"), "child": ("inspect", CHILD, "--json")}
        table.update({keys.get(name, (name,)): reply for name, reply in replies.items()})
        fake = FakeRun(table)
        patch = mock.patch.object(cli, "run", fake)
        patch.start()
        self.addCleanup(patch.stop)
        return fake

    @staticmethod
    def subcommands(fake: FakeRun) -> list[str]:
        return [call[1] for call in fake.calls]

    @staticmethod
    def sent(fake: FakeRun) -> list[list[str]]:
        return [call for call in fake.calls if call[1] == "send"]


class PathTests(NotifyCase):
    def test_paths_follow_home_at_call_time(self) -> None:
        self.assertEqual(notify.notify_dir(), self.home / ".paseo" / "notify")
        self.assertEqual(notify.marker("a", "b"), self.home / ".paseo" / "notify" / "a--b")
        self.assertEqual(notify.marker("a", "b").name, "a--b")

    def test_parked_callers_are_the_markers_of_one_child(self) -> None:
        self.assertEqual(notify.parked_callers(CHILD), [])  # no marker directory yet
        for child, caller in (
            (CHILD, CALLER),
            (CHILD, "44444444-4444-4444-8444-444444444444"),
            ("other", "x"),
        ):
            notify.marker(child, caller).parent.mkdir(parents=True, exist_ok=True)
            notify.marker(child, caller).touch()
        self.assertEqual(notify.parked_callers(CHILD), [CALLER, "44444444-4444-4444-8444-444444444444"])
        self.assertEqual(notify.parked_callers("nobody"), [])
        notify.unpark(CHILD, CALLER)
        notify.unpark(CHILD, CALLER)  # a marker that is already gone is fine
        self.assertEqual(notify.parked_callers(CHILD), ["44444444-4444-4444-8444-444444444444"])


class SendTests(NotifyCase):
    def test_send_steers_the_message_into_the_callers_turn(self) -> None:
        fake = self.paseo()
        self.assertTrue(notify.send(CALLER, CHILD, "finished its turn."))
        (call,) = self.sent(fake)
        self.assertEqual(call[2:6], ["--behavior", "steer", "--no-wait", CALLER])
        self.assertIn(f"[paseo-notify] Agent {CHILD} (Build the thing) finished its turn.", call[6])
        self.assertIn(f"arm `paseo-notify {CHILD}` again", call[6])

    def test_send_skips_an_archived_caller(self) -> None:
        fake = self.paseo(caller=info(CALLER, Archived=True))
        self.assertFalse(notify.send(CALLER, CHILD, "finished its turn."))
        self.assertEqual(self.sent(fake), [])

    def test_send_skips_an_unknown_caller(self) -> None:
        fake = self.paseo(caller=done("", 1, "no such agent"))
        self.assertFalse(notify.send(CALLER, CHILD, "finished its turn."))
        self.assertEqual(self.sent(fake), [])

    def test_a_failed_send_is_not_delivered(self) -> None:
        self.paseo(send=done("", 1, "connection refused"))
        self.assertFalse(notify.send(CALLER, CHILD, "finished its turn."))


class PendingOfTests(NotifyCase):
    def test_reads_the_background_tasks_out_of_the_transcript(self) -> None:
        write_json(
            self.home / ".paseo" / "agents" / "ws" / f"{CHILD}.json",
            {"id": CHILD, "persistence": {"sessionId": SESSION}},
        )
        transcript = self.home / ".claude" / "projects" / "-work" / f"{SESSION}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(BASH_START + "\n")
        self.assertEqual(notify._pending_of(CHILD), {"b0000task1"})

    def test_an_agent_without_a_transcript_has_nothing_pending(self) -> None:
        self.assertEqual(notify._pending_of(CHILD), set())


class WatchTests(NotifyCase):
    def test_the_wait_timeout_fits_a_poll_call(self) -> None:
        self.assertLess(notify.WAIT_TIMEOUT * 1000, 2**31)

    def test_idle_sends_the_caller_exactly_one_message(self) -> None:
        fake = self.paseo(wait=done(IDLE))
        with mock.patch.object(notify, "_pending_of", return_value=set()):
            notify.watch(CHILD, CALLER)
        self.assertEqual(self.subcommands(fake).count("wait"), 1)
        self.assertEqual(fake.calls[0][1:], ["wait", CHILD, "--json"])
        (call,) = self.sent(fake)
        self.assertIn("finished its turn", call[6])

    def test_a_limit_parks_the_watch_instead_of_sending(self) -> None:
        fake = self.paseo(wait=done(IDLE), logs=done(LIMIT + "\n"))
        with mock.patch.object(notify, "_pending_of", return_value=set()):
            notify.watch(CHILD, CALLER)
        self.assertEqual(self.sent(fake), [])
        self.assertTrue(notify.marker(CHILD, CALLER).exists())

    def test_it_keeps_waiting_while_background_tasks_are_out(self) -> None:
        fake = self.paseo(wait=done(IDLE))
        with mock.patch.object(notify, "_pending_of", side_effect=[{"b0000task1"}, set()]):
            notify.watch(CHILD, CALLER)
        self.assertEqual(self.subcommands(fake).count("wait"), 2)
        self.assertEqual(len(self.sent(fake)), 1)
        notify.time.sleep.assert_called_once_with(notify.PENDING_RECHECK)

    def test_it_gives_up_on_tasks_that_never_come_back(self) -> None:
        fake = self.paseo(wait=done(IDLE))
        with (
            mock.patch.object(notify, "MAX_PENDING", 0),
            mock.patch.object(notify, "_pending_of", return_value={"b0000task1"}),
        ):
            notify.watch(CHILD, CALLER)
        (call,) = self.sent(fake)
        self.assertIn("still has background tasks out", call[6])

    def test_a_failed_paseo_wait_tells_the_caller_and_starts_nothing(self) -> None:
        failures = {
            "non-zero exit": done("", 1, "Error: connect ECONNREFUSED 127.0.0.1:6767"),
            "not JSON": done("<html>502</html>", 0, ""),
            "JSON but non-zero": done(IDLE, 1, "boom"),
        }
        for label, reply in failures.items():
            with self.subTest(label):
                fake = self.paseo(wait=reply)
                with mock.patch.object(notify, "_pending_of", return_value={"b0000task1"}):
                    notify.watch(CHILD, CALLER)
                (call,) = self.sent(fake)
                self.assertIn("can no longer be watched", call[6])
                self.assertIn("Re-arm with `paseo-notify <id>`", call[6])
                # The daemon belongs to systemd: paseo-notify only reports, it never starts or stops anything.
                self.assertTrue(set(self.subcommands(fake)) <= {"wait", "inspect", "send"})
                self.assertFalse(
                    [c for c in fake.calls if "daemon" in " ".join(c) or "restart" in " ".join(c)]
                )


class ArmTests(NotifyCase):
    def test_the_original_process_returns_at_once(self) -> None:
        with (
            mock.patch.object(notify, "_detach", return_value=False),
            mock.patch.object(notify, "watch") as watch,
        ):
            notify.arm(CHILD, CALLER)
        watch.assert_not_called()

    def test_the_detached_process_watches_and_exits(self) -> None:
        with (
            mock.patch.object(notify, "_detach", return_value=True),
            mock.patch.object(notify, "watch") as watch,
            mock.patch.object(notify.os, "_exit") as leave,
        ):
            notify.arm(CHILD, CALLER)
        watch.assert_called_once_with(CHILD, CALLER)
        leave.assert_called_once_with(0)

    def test_a_second_watch_on_the_same_pair_steps_aside(self) -> None:
        """The skills re-arm after every send, often while the last watch still waits: one message only."""
        second, other_caller = mock.Mock(), mock.Mock()

        def first(child: str, caller: str) -> None:
            with mock.patch.object(notify, "watch", second):
                notify.arm(child, caller)
            with mock.patch.object(notify, "watch", other_caller):
                notify.arm(child, "44444444-4444-4444-8444-444444444444")

        with (
            mock.patch.object(notify, "_detach", return_value=True),
            mock.patch.object(notify, "watch", side_effect=first),
            mock.patch.object(notify.os, "_exit"),
        ):
            notify.arm(CHILD, CALLER)
        second.assert_not_called()
        other_caller.assert_called_once()
        self.assertEqual(list((notify.notify_dir() / "locks").iterdir()), [])

    def test_a_finished_watch_can_be_armed_again(self) -> None:
        with (
            mock.patch.object(notify, "_detach", return_value=True),
            mock.patch.object(notify, "watch") as watch,
            mock.patch.object(notify.os, "_exit"),
        ):
            notify.arm(CHILD, CALLER)
            notify.arm(CHILD, CALLER)
        self.assertEqual(watch.call_count, 2)

    def test_a_crashing_watch_still_tells_the_caller(self) -> None:
        fake = self.paseo()
        with (
            mock.patch.object(notify, "_detach", return_value=True),
            mock.patch.object(notify, "watch", side_effect=RuntimeError("boom")),
            mock.patch.object(notify.os, "_exit"),
        ):
            notify.arm(CHILD, CALLER)
        (call,) = self.sent(fake)
        self.assertIn("paseo-notify crashed", call[6])
        self.assertIn("boom", call[6])


LIB = Path(__file__).resolve().parent.parent / "lib"
# Arms a watch whose stand-in blocks for 2 s, and returns once the detached process is watching.
ARM_UNDER_FLOCK = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from paseo_tools import notify
ready = Path.home() / "watching"
def watch(child, caller):
    ready.touch()
    time.sleep(2)
notify.watch = watch
notify.send = lambda *args: False  # never the real paseo, even if the stand-in fails
notify.arm("child", "caller")
deadline = time.monotonic() + 10
while not ready.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
"""


# Arms a watch whose detach fails in a forked copy (argv[2]: "log-is-a-directory" or "second-fork-fails"),
# under a guard like the limit watcher's per-agent one, then appends one line per process that gets past it.
ARM_DETACH_FAILS = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from paseo_tools import notify
notify.watch = lambda child, caller: None
notify.send = lambda *args: False  # never the real paseo
if sys.argv[2] == "log-is-a-directory":
    notify.log_path().mkdir()
else:
    real_fork, forks = os.fork, []
    def fork():
        forks.append(1)
        if len(forks) == 2:  # in the middle child
            raise BlockingIOError(11, "Resource temporarily unavailable")
        return real_fork()
    os.fork = fork
try:
    notify.arm("child", "caller")
except Exception:
    pass
with open(Path.home() / "after-arm", "a") as f:
    f.write(f"{os.getpid()}\\n")
"""


class DetachTests(TempHome):
    """The real double fork, in a subprocess: this test process itself never forks."""

    def test_a_failed_detach_never_returns_into_the_caller(self) -> None:
        """A forked copy that returned would run the rest of the caller twice: the limit watcher would send
        its resume lines again and save its state a second time."""
        for case in ("log-is-a-directory", "second-fork-fails"):
            with self.subTest(case):
                home = self.home / case
                home.mkdir()
                # capture_output waits for every process that still holds the pipes, the forked ones too.
                armed = subprocess.run(
                    [sys.executable, "-c", ARM_DETACH_FAILS, str(LIB), case],
                    env={**os.environ, "HOME": str(home)},
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(armed.returncode, 0, armed.stderr)
                lines = (home / "after-arm").read_text().splitlines()
                self.assertEqual(len(lines), 1, "a forked copy ran the caller's code")

    @unittest.skipUnless(shutil.which("flock"), "flock is not installed")
    def test_the_detached_watch_does_not_keep_crons_lock(self) -> None:
        """README's cron line runs the limit watcher under `flock -n`, which hands its lock descriptor down;
        a watch the watcher re-arms must not hold it, or every later cron run is locked out."""
        lock = str(self.home / "watch.lock")
        env = {**os.environ, "HOME": str(self.home)}
        armed = subprocess.run(
            ["flock", "-n", lock, sys.executable, "-c", ARM_UNDER_FLOCK, str(LIB)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(armed.returncode, 0, armed.stderr)
        self.assertTrue((self.home / "watching").exists(), "the detached watch never started")
        again = subprocess.run(["flock", "-n", lock, "true"], timeout=30)
        self.assertEqual(again.returncode, 0, "the detached watch still holds the cron lock")


class MainTests(NotifyCase):
    def run_main(self, *argv: str, caller: str | None = CALLER) -> tuple[int, mock.Mock]:
        env = {"PASEO_AGENT_ID": caller} if caller else {}
        with (
            mock.patch.dict(os.environ, env),
            mock.patch.object(notify, "arm") as arm,
        ):
            if not caller:
                os.environ.pop("PASEO_AGENT_ID", None)
            return notify.main(list(argv)), arm

    def test_main_arms_for_the_caller_and_says_so(self) -> None:
        self.paseo()
        code, arm = self.run_main(CHILD)
        self.assertEqual(code, 0)
        arm.assert_called_once_with(CHILD, CALLER)
        self.assertIn(
            "armed: 3333333 gets one message when 1111111 (Build the thing) stops.", self.out.getvalue()
        )

    def test_main_arms_for_someone_else_with_to(self) -> None:
        self.paseo()
        code, arm = self.run_main(CHILD, "--to", CALLER, caller=None)
        self.assertEqual(code, 0)
        arm.assert_called_once_with(CHILD, CALLER)

    def test_main_refuses_waiting_for_itself(self) -> None:
        self.paseo()
        code, arm = self.run_main(CHILD, "--to", CHILD)
        self.assertNotEqual(code, 0)
        self.assertIn("an agent cannot wait for itself", self.err.getvalue())
        arm.assert_not_called()

    def test_main_without_a_caller_says_how_to_wait_outside_paseo(self) -> None:
        self.paseo()
        code, arm = self.run_main(CHILD, caller=None)
        self.assertNotEqual(code, 0)
        self.assertIn("paseo wait <id> --json", self.err.getvalue())
        arm.assert_not_called()

    def test_main_refuses_an_unknown_agent(self) -> None:
        self.paseo(child=done("", 1, "no such agent"))
        code, arm = self.run_main(CHILD)
        self.assertNotEqual(code, 0)
        self.assertIn(f"paseo-notify: no agent '{CHILD}'", self.err.getvalue())
        arm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
