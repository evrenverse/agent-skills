"""Tests for the hook of paseo_tools.test_queue: which commands are heavy, how a heavy one is wrapped and when
the hook leaves a call alone.

The hook is called directly and through `main(["hook"])` with a patched stdin and stdout. HOME is a temp dir
(`TempHome`), so the configuration is too.
"""

import contextlib
import io
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest import mock

from paseo_tools import test_queue
from tests.support import TempHome, write_json

ROOT = Path(__file__).resolve().parent.parent
EXECUTABLE = "/opt/tools/bin/test-queue"
CWD = "/work/repo"

HEAVY = [
    "bun run check",
    "mise exec -- bun run check > /tmp/x.log 2>&1",
    "bun run test",
    "bun run test:changed",
    "bun run ci",
    "bun run e2e",
    "npm test",
    "pnpm test",
    "turbo run test",
    "turbo run typecheck test --affected",
    "bun run --cwd apps/web test",
    "npx vitest run",
    "bunx playwright test",
    "pytest",
    # Matching is textual, and a false positive only queues.
    'echo "bun run check"',
    # A directory or a flag's value is no test file: these run the whole suite.
    "pytest tests/",
    "pytest tests",
    "python -m pytest tests",
    "pytest -n auto",
    "playwright test --project chromium",
    "vitest run --reporter dot",
    "bun test",
    "bun test --coverage",
]
LIGHT = [
    "git status",
    "bun run lint",
    "bun run typecheck",
    "vitest run src/a.test.ts",
    "bunx vitest run src/a.test.ts src/b.test.ts",
    "playwright test e2e/one.spec.ts",
    "pytest tests/test_one.py",
    "bun test src/a.test.ts",
]
# Commands whose quoting a wrapped call must keep: wrapped, `bash -c` does what it does without.
QUOTING = [
    """echo "it's" """,
    """echo 'say "hi"'""",
    """echo '$HOME is' "$HOME" """,
    "echo `echo tick`",
    "echo one\necho two",
    "cat <<'EOF'\nraw $HOME `x`\nEOF",
    "cat <<EOF\nhome $HOME\nEOF",
    "cd sub && pwd",
    """printf '%s\\n' "a b" 'c  d'""",
    "echo to-stderr >&2; exit 3",
]
# Stands in for test-queue: drops "run --label <label> --" and runs the rest.
STAND_IN = """#!/bin/sh
shift 4
exec "$@"
"""


def payload(command: str = "bun run check", **changes: Any) -> dict[str, Any]:
    """A PreToolUse payload of a Bash call in a bypassPermissions session; `changes` replace its keys."""
    base = {
        "cwd": CWD,
        "permission_mode": "bypassPermissions",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "d", "timeout": 120000, "run_in_background": True},
    }
    return {**base, **changes}


def without(key: str) -> dict[str, Any]:
    """The default payload without `key`."""
    return {name: value for name, value in payload().items() if name != key}


class HookCase(TempHome):
    def setUp(self) -> None:
        super().setUp()
        # The suite may itself run inside a held slot; patch.dict in TempHome puts them back.
        for name in ("TEST_QUEUE_DIR", "TEST_QUEUE_SLOT", "TEST_QUEUE_SLOTS"):
            os.environ.pop(name, None)

    def decide(self, call: Any, executable: str = EXECUTABLE) -> dict[str, Any] | None:
        """The hook's answer to `call` with the default configuration."""
        return test_queue.hook(call, test_queue.Config(), executable)

    def through_main(self, stdin: str) -> tuple[int, str, str]:
        """Run `main(["hook"])` with `stdin`: its exit code, stdout and stderr."""
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO(stdin)),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = test_queue.main(["hook"])
        return code, out.getvalue(), err.getvalue()


class PatternTests(HookCase):
    def test_heavy_patterns(self) -> None:
        for command in HEAVY:
            with self.subTest(heavy=command):
                self.assertTrue(test_queue.is_heavy(command, test_queue.DEFAULT_PATTERNS))
        for command in LIGHT:
            with self.subTest(light=command):
                self.assertFalse(test_queue.is_heavy(command, test_queue.DEFAULT_PATTERNS))

    def test_patterns_do_not_backtrack_exponentially(self) -> None:
        # The hook runs before every Bash call: a long run of flags, matched or not, must stay cheap. A slow
        # pattern takes minutes here, not milliseconds.
        flags = " ".join(f"--flag{n}" for n in range(40))
        for command in (
            f"bun run {flags} x",
            f"bun run {flags} check",
            f"turbo run {flags} lint",
            f"pytest {flags} tests/test_one.py",
            f"pytest {flags}",
            f"bun test {flags} src/a.test.ts",
            f"vitest run {flags} run x",
        ):
            with self.subTest(command=command[:20] + "…" + command[-20:]):
                took = []
                for _ in range(5):
                    start = time.perf_counter()
                    test_queue.is_heavy(command, test_queue.DEFAULT_PATTERNS)
                    took.append(time.perf_counter() - start)
                self.assertLess(min(took), 0.01)


class WrapTests(HookCase):
    def test_wrapped_command_runs_as_written(self) -> None:
        stand_in = self.home / "stand in" / "test-queue"  # with a space: the executable is quoted too
        stand_in.parent.mkdir()
        stand_in.write_text(STAND_IN)
        stand_in.chmod(0o755)
        (self.home / "sub").mkdir()

        def run(command: str) -> tuple[int, str, str]:
            done = subprocess.run(
                ["bash", "-c", command], cwd=self.home, capture_output=True, text=True, check=False
            )
            return done.returncode, done.stdout, done.stderr

        for command in QUOTING:
            with self.subTest(command=command):
                wrapped = test_queue.wrap(command, "it's: a label", str(stand_in))
                plain = run(command)
                self.assertNotEqual(plain[1] + plain[2], "")
                self.assertEqual(run(wrapped), plain)


class HookTests(HookCase):
    def test_hook_wraps_a_heavy_command(self) -> None:
        label = test_queue.default_label(CWD, "bun run check")
        wrapped = test_queue.wrap("bun run check", label, EXECUTABLE)
        self.assertEqual(
            self.decide(payload()),
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": {
                        "command": wrapped,
                        "description": "d",
                        "timeout": 120000,
                        "run_in_background": True,
                    },
                }
            },
        )

    def test_foreground_call_gets_the_longest_timeout(self) -> None:
        # A wait in the queue counts against the Bash tool's timeout, so a foreground call gets its maximum.
        for timeout in ({}, {"timeout": 120000}, {"timeout": 600000}):
            with self.subTest(timeout=timeout):
                tool_input = {"command": "bun run check", "run_in_background": False, **timeout}
                result = self.decide(payload(tool_input=tool_input))
                assert result is not None
                updated = result["hookSpecificOutput"]["updatedInput"]
                self.assertEqual(updated["timeout"], 600000)
                self.assertFalse(updated["run_in_background"])

    def test_call_without_run_in_background_is_a_foreground_call(self) -> None:
        result = self.decide(payload(tool_input={"command": "bun run check"}))
        assert result is not None
        self.assertEqual(result["hookSpecificOutput"]["updatedInput"]["timeout"], 600000)

    def test_hook_leaves_alone(self) -> None:
        spaced = "/opt/my tools/bin/test-queue"
        cases = {
            "light command": payload("git status"),
            "already wrapped": payload(f"{EXECUTABLE} run -- bun run check"),
            "already wrapped, quoted": payload(f"'{spaced}' run -- bun run check"),
            "mode default": payload(permission_mode="default"),
            "mode acceptEdits": payload(permission_mode="acceptEdits"),
            "mode plan": payload(permission_mode="plan"),
            "mode auto": payload(permission_mode="auto"),
            "mode dontAsk": payload(permission_mode="dontAsk"),
            "mode missing": without("permission_mode"),
            "tool Read": payload(tool_name="Read"),
            "tool missing": without("tool_name"),
            "tool_input missing": without("tool_input"),
            "tool_input not an object": payload(tool_input="bun run check"),
            "command missing": payload(tool_input={"description": "d"}),
            "command not a string": payload(tool_input={"command": 5}),
            "timeout not a number": payload(tool_input={"command": "bun run check", "timeout": "x"}),
            "cwd missing": without("cwd"),
            "payload not an object": ["bun run check"],
        }
        for name, call in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(self.decide(call, spaced if "quoted" in name else EXECUTABLE))

    def test_hook_leaves_alone_on_a_broken_pattern(self) -> None:
        self.assertIsNone(test_queue.hook(payload(), test_queue.Config(patterns=["("]), EXECUTABLE))
        self.assertIsNone(test_queue.hook(payload(), test_queue.Config(patterns=[5]), EXECUTABLE))  # type: ignore[list-item]


class MainHookTests(HookCase):
    def test_main_prints_the_result(self) -> None:
        code, out, err = self.through_main(json.dumps(payload()))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.count("\n"), 1)
        executable = str(ROOT / "bin" / "test-queue")
        self.assertEqual(json.loads(out), self.decide(payload(), executable))

    def test_main_is_silent_on_bad_input(self) -> None:
        for stdin in ("", "not json", "null", "[]", json.dumps(payload("git status"))):
            with self.subTest(stdin=stdin):
                self.assertEqual(self.through_main(stdin), (0, "", ""))

    def test_config_patterns_replace_the_defaults(self) -> None:
        write_json(self.home / ".config" / "test-queue" / "config.json", {"patterns": ["^make check$"]})
        _, wrapped, _ = self.through_main(json.dumps(payload("make check")))
        self.assertIn("make check", json.loads(wrapped)["hookSpecificOutput"]["updatedInput"]["command"])
        self.assertEqual(self.through_main(json.dumps(payload("bun run check"))), (0, "", ""))

    def test_declared_hook_runs(self) -> None:
        declared = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
        self.assertEqual([entry["matcher"] for entry in declared], ["Bash"])
        (command,) = declared[0]["hooks"]
        self.assertEqual(command["type"], "command")
        self.assertGreater(command["timeout"], 0)
        args = [arg.replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT)) for arg in command["args"]]
        argv = [command["command"], *args]
        done = subprocess.run(argv, input=json.dumps(payload()), capture_output=True, text=True, check=False)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        updated = json.loads(done.stdout)["hookSpecificOutput"]["updatedInput"]
        self.assertIn(" run --label ", updated["command"])
