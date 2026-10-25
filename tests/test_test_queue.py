"""Tests for paseo_tools.test_queue: slots, queue order, signals and status.

The queued calls are real `bin/test-queue run` processes; their commands are SCRIPT, which writes marker
files. HOME is a temp dir (`TempHome`), so the queue's state and configuration are too.
"""

import contextlib
import fcntl
import io
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from paseo_tools import test_queue
from tests.support import TempHome, write_json

TOOL = Path(__file__).resolve().parent.parent / "bin" / "test-queue"
# Writes <name>.start, sleeps, writes <name>.end; each holds "<time> <pid>" and appears through a rename.
SCRIPT = """
import os, sys, time
from pathlib import Path
marks, name, seconds = Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])
def mark(kind):
    tmp = marks / f".{name}.{kind}"
    tmp.write_text(f"{time.time()} {os.getpid()}")
    tmp.replace(marks / f"{name}.{kind}")
mark("start")
time.sleep(seconds)
mark("end")
"""


class QueueCase(TempHome):
    def setUp(self) -> None:
        super().setUp()
        # The suite may itself run inside a held slot; patch.dict in TempHome puts them back.
        for name in ("TEST_QUEUE_DIR", "TEST_QUEUE_SLOT", "TEST_QUEUE_SLOTS"):
            os.environ.pop(name, None)
        self.state = self.home / ".local" / "state" / "test-queue"
        self.marks = self.home / "marks"
        self.marks.mkdir()

    def start(self, name: str, *flags: str, seconds: float = 0) -> subprocess.Popen[str]:
        """Start `test-queue run --label <name>` of SCRIPT in HOME; cleanup stops it and its command."""
        command = [sys.executable, "-c", SCRIPT, str(self.marks), name, str(seconds)]
        proc = subprocess.Popen(
            [sys.executable, str(TOOL), "run", "--label", name, *flags, "--", *command],
            cwd=self.home,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop, proc, name)
        return proc

    def stop(self, proc: subprocess.Popen[str], name: str) -> None:
        """End a call: SIGTERM, which it passes on to its command; then kill a command that outlived it."""
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        assert proc.stderr is not None
        proc.stderr.close()
        if (self.marks / f"{name}.start").exists() and not (self.marks / f"{name}.end").exists():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.pid(name), signal.SIGKILL)

    def appear(self, path: Path, timeout: float = 10) -> Path:
        """Wait until `path` exists."""
        deadline = time.monotonic() + timeout
        while not path.exists():
            if time.monotonic() > deadline:
                self.fail(f"{path.name} did not appear within {timeout} s")
            time.sleep(0.02)
        return path

    def when(self, name: str, kind: str = "start") -> float:
        """The time in marker <name>.<kind>, once it is there."""
        return float(self.appear(self.marks / f"{name}.{kind}").read_text().split()[0])

    def pid(self, name: str) -> int:
        """The pid of <name>'s command."""
        return int((self.marks / f"{name}.start").read_text().split()[1])

    def ticket(self, proc: subprocess.Popen[str]) -> None:
        """Wait until `proc` waits in the queue."""
        self.appear(self.state / "waiting" / f"{proc.pid}.json")

    def hold_slot(self) -> Callable[[], None]:
        """Hold slot 0 from the test process, as a call does; returns the release, which cleanup calls too."""
        self.state.mkdir(parents=True, exist_ok=True)
        fds = [os.open(self.state / "slot-0.lock", os.O_RDWR | os.O_CREAT)]
        fcntl.flock(fds[0], fcntl.LOCK_EX)

        def release() -> None:
            while fds:
                os.close(fds.pop())

        self.addCleanup(release)
        return release


class QueueTests(QueueCase):
    def test_second_run_waits_for_the_first(self) -> None:
        first = self.start("first", seconds=0.5)
        self.when("first")
        second = self.start("second")
        self.assertEqual(first.wait(10), 0)
        self.assertEqual(second.wait(10), 0)
        self.assertGreaterEqual(self.when("second"), self.when("first", "end"))
        assert second.stderr is not None
        held = f"held by first ({self.home.resolve()}) for 0 min"
        self.assertIn(f"test-queue: waiting; {held}; 0 ahead", second.stderr.read())

    def test_two_slots_run_two_at_once(self) -> None:
        os.environ["TEST_QUEUE_SLOTS"] = "2"
        self.start("first", seconds=30)
        self.when("first")
        self.start("second")
        self.when("second")
        self.assertFalse((self.marks / "first.end").exists())  # the first still runs

    def test_exit_code_passes_through(self) -> None:
        seven = "import os; raise SystemExit(7 if os.environ['TEST_QUEUE_SLOT'] == 'seven' else 1)"
        self.assertEqual(test_queue.main(["run", "--label", "seven", "--", sys.executable, "-c", seven]), 7)
        killed = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
        self.assertEqual(test_queue.main(["run", "--", sys.executable, "-c", killed]), 128 + signal.SIGTERM)

    def test_default_label(self) -> None:
        self.assertEqual(test_queue.default_label("/work/repo", "x" * 70), "repo: " + "x" * 60)

    def test_last_lets_a_later_waiter_go_first(self) -> None:
        release = self.hold_slot()
        self.ticket(self.start("last", "--last"))
        self.ticket(self.start("plain", seconds=30))  # keeps the only slot, so "last" cannot start after it
        release()
        self.when("plain")
        self.assertFalse((self.marks / "last.start").exists())

    def test_last_queues_normally_after_its_wait(self) -> None:
        write_json(self.home / ".config" / "test-queue" / "config.json", {"last_wait_max_s": 0})
        release = self.hold_slot()
        self.ticket(self.start("last", "--last", seconds=30))  # keeps the only slot once it has it
        self.ticket(self.start("plain"))
        release()
        self.when("last")
        self.assertFalse((self.marks / "plain.start").exists())

    def test_killed_holder_frees_its_slot(self) -> None:
        # The holder's command runs on after the kill: the slot must free with the call, not with the command.
        holder = self.start("holder", seconds=30)
        self.when("holder")
        self.ticket(self.start("waiter"))
        holder.kill()
        holder.wait()
        killed = time.time()
        self.assertLess(self.when("waiter") - killed, 3)

    def test_dead_waiter_is_dropped(self) -> None:
        gone = subprocess.Popen([sys.executable, "-c", ""])
        gone.wait()
        waiting = self.state / "waiting"
        entry = {"label": "gone", "pid": gone.pid, "cwd": "/", "since": time.time(), "last": False}
        write_json(waiting / f"{gone.pid}.json", entry)
        # What a SIGKILLed waiter leaves, its pid now taken by a live process (this one): a lock nobody holds.
        write_json(waiting / f"{os.getpid()}.json", {**entry, "pid": os.getpid()})
        (waiting / f"{os.getpid()}.lock").touch()
        self.assertEqual(test_queue.status(), ([], []))
        self.assertEqual(list(waiting.iterdir()), [])

    def test_a_signal_while_waiting_removes_the_ticket(self) -> None:
        self.hold_slot()
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum.name):
                call = self.start(f"waiter-{signum}")
                self.ticket(call)
                call.send_signal(signum)
                self.assertEqual(call.wait(5), 128 + signum)
                self.assertEqual(list((self.state / "waiting").iterdir()), [])

    def test_holder_file_without_its_lock_is_no_holder(self) -> None:
        # What a SIGKILLed holder leaves: the kernel freed its lock, its file stayed.
        entry = {"label": "gone", "pid": os.getpid(), "cwd": "/", "since": time.time(), "last": False}
        write_json(self.state / "slot-0.json", entry)
        self.assertEqual(test_queue.status(), ([], []))

    def test_signals_reach_the_command(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=signum.name):
                name = f"victim-{signum}"
                call = self.start(name, seconds=30)
                self.when(name)
                call.send_signal(signum)
                self.assertEqual(call.wait(3), 128 + signum)
                with self.assertRaises(ProcessLookupError):
                    os.kill(self.pid(name), 0)

    def test_nested_run_runs_at_once(self) -> None:
        self.hold_slot()
        os.environ["TEST_QUEUE_SLOT"] = "outer"
        show = "import os; print(os.environ['TEST_QUEUE_SLOT'])"
        proc = subprocess.run(
            [sys.executable, str(TOOL), "run", "--label", "inner", "--", sys.executable, "-c", show],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "inner\n", ""))
        self.assertEqual(list(self.state.glob("waiting/*")), [])

    def test_status_prints_holders_and_waiters(self) -> None:
        self.start("holder", seconds=30)
        self.when("holder")
        self.ticket(self.start("waiter", "--last"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(test_queue.main(["status"]), 0)
        cwd = self.home.resolve()
        self.assertEqual(out.getvalue(), f"holds  0 min  holder  ({cwd})\nwaits  0 min  waiter  ({cwd})\n")

    def test_broken_config_falls_back_to_defaults(self) -> None:
        path = self.home / ".config" / "test-queue" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text("{")
        self.assertEqual(test_queue.load_config(), test_queue.Config())

    def test_slots_come_from_the_config_and_the_environment(self) -> None:
        write_json(self.home / ".config" / "test-queue" / "config.json", {"slots": 3})
        self.assertEqual(test_queue.load_config().slots, 3)
        os.environ["TEST_QUEUE_SLOTS"] = "2"
        self.assertEqual(test_queue.load_config().slots, 2)
        os.environ["TEST_QUEUE_SLOTS"] = "0"
        self.assertEqual(test_queue.load_config().slots, 1)
