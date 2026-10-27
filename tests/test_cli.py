"""Tests for paseo_tools.cli."""

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from paseo_tools import cli
from tests.support import FakeRun, TempHome


def make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["x"], returncode, stdout, "")


class RunTests(TempHome):
    def test_captures_text_output(self) -> None:
        proc = cli.run(["echo", "hi"])
        self.assertEqual((proc.returncode, proc.stdout, proc.args), (0, "hi\n", ["echo", "hi"]))

    def test_missing_binary_returns_127(self) -> None:
        proc = cli.run(["no-such-binary-x"])
        self.assertEqual(proc.returncode, 127)
        self.assertIn("no-such-binary-x", proc.stderr)
        self.assertEqual(proc.args, ["no-such-binary-x"])

    def test_non_executable_file_returns_126(self) -> None:
        script = self.home / "script"
        script.write_text("#!/bin/sh\n")
        proc = cli.run([str(script)])
        self.assertEqual(proc.returncode, 126)
        self.assertTrue(proc.stderr)

    def test_timeout_returns_124(self) -> None:
        start = time.monotonic()
        proc = cli.run(["sleep", "5"], timeout=0.2)
        self.assertEqual(proc.returncode, 124)
        self.assertIn("timed out", proc.stderr)
        self.assertLess(time.monotonic() - start, 4)

    def test_undecodable_output_does_not_raise(self) -> None:
        proc = cli.run(["printf", r"a\377b"])
        self.assertEqual((proc.returncode, proc.stdout), (0, "a\ufffdb"))

    def test_cwd(self) -> None:
        proc = cli.run(["pwd"], cwd=self.home)
        self.assertEqual(Path(proc.stdout.strip()).resolve(), self.home.resolve())

    def test_input_reaches_stdin(self) -> None:
        self.assertEqual(cli.run(["cat"], input="hello").stdout, "hello")

    def test_env_is_the_childs_environment(self) -> None:
        show = "import os; print(os.environ.get('PASEO_AGENT_ID'), 'HOME' in os.environ)"
        proc = cli.run([sys.executable, "-c", show], env={"PASEO_AGENT_ID": "sender"})
        self.assertEqual(proc.stdout, "sender False\n")

    def test_stdin_is_closed_without_input(self) -> None:
        # Without a closed stdin `cat` would wait for the terminal; cron has none.
        proc = cli.run(["cat"], timeout=5)
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))


class JsonOutTests(TempHome):
    def test_json_out_none_on_failure(self) -> None:
        self.assertIsNone(cli.json_out(completed(1, '{"a": 1}')))

    def test_json_out_parses(self) -> None:
        self.assertEqual(cli.json_out(completed(0, '{"a": 1}')), {"a": 1})

    def test_json_out_accepts_the_listed_exit_codes_only(self) -> None:
        self.assertEqual(cli.json_out(completed(2, '{"a": 1}'), ok=(0, 2)), {"a": 1})
        self.assertIsNone(cli.json_out(completed(1, '{"a": 1}'), ok=(0, 2)))
        self.assertIsNone(cli.json_out(completed(2, '{"a": 1}')))

    def test_json_out_none_on_bad_json(self) -> None:
        self.assertIsNone(cli.json_out(completed(0, "not json")))
        self.assertIsNone(cli.json_out(completed(0, "")))


class ResolveTests(TempHome):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"PATH": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prefers_path(self) -> None:
        exe = make_executable(self.home / "elsewhere" / "paseo")
        make_executable(self.home / ".local" / "bin" / "paseo")
        os.environ["PATH"] = str(exe.parent)
        self.assertEqual(cli.resolve("paseo"), str(exe))

    def test_resolve_falls_back_to_local_bin(self) -> None:
        exe = make_executable(self.home / ".local" / "bin" / "paseo")
        self.assertEqual(cli.resolve("paseo"), str(exe))

    def test_picks_newest_nvm_version_numerically(self) -> None:
        node = self.home / ".nvm" / "versions" / "node"
        for version in ("v9.0.0", "v20.1.0", "v18.19.1", "vnext"):
            make_executable(node / version / "bin" / "paseo")
        self.assertEqual(cli.resolve("paseo"), str(node / "v20.1.0" / "bin" / "paseo"))

    def test_local_bin_beats_nvm(self) -> None:
        exe = make_executable(self.home / ".local" / "bin" / "paseo")
        make_executable(self.home / ".nvm" / "versions" / "node" / "v20.1.0" / "bin" / "paseo")
        self.assertEqual(cli.resolve("paseo"), str(exe))

    def test_returns_bare_name_when_nothing_found(self) -> None:
        self.assertEqual(cli.resolve("paseo"), "paseo")

    def test_ignores_non_executable_file(self) -> None:
        (self.home / ".local" / "bin").mkdir(parents=True)
        (self.home / ".local" / "bin" / "paseo").write_text("")
        self.assertEqual(cli.resolve("paseo"), "paseo")


class PaseoTests(TempHome):
    def test_runs_resolved_binary_with_args_and_timeout(self) -> None:
        fake = FakeRun({("ls", "--json"): completed(0, "[]")})
        with mock.patch.object(cli, "run", fake):
            proc = cli.paseo("ls", "--json", timeout=7)
        self.assertEqual(proc.stdout, "[]")
        self.assertEqual(fake.calls, [[cli.resolve("paseo"), "ls", "--json"]])
        self.assertEqual(fake.kwargs[0]["timeout"], 7)
