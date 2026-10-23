"""Tests for the bin/ entry points and install.sh.

HOME is a temp dir throughout (`TempHome`): install.sh must never be pointed at the real one.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support import TempHome

REPO = Path(__file__).resolve().parent.parent
COMMANDS = ("paseo-limit-watch", "paseo-notify", "paseo-title")


def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run `argv` with the environment of this process (HOME is already the temp dir)."""
    return subprocess.run(argv, capture_output=True, text=True, check=False, env=os.environ.copy())


class EntryPointTests(TempHome):
    def test_entry_points_run_through_a_symlink(self) -> None:
        names = sorted(path.name for path in (REPO / "bin").iterdir() if path.is_file())
        self.assertEqual(names, list(COMMANDS))
        with tempfile.TemporaryDirectory() as tmp:
            for name in names:
                link = Path(tmp) / name
                link.symlink_to(REPO / "bin" / name)
                proc = run([str(link), "--help"])  # relies on the shebang
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(f"usage: {name}", proc.stdout)


class InstallTests(TempHome):
    def setUp(self) -> None:
        super().setUp()
        self.bin = self.home / ".local" / "bin"

    def install(self) -> subprocess.CompletedProcess[str]:
        return run([str(REPO / "install.sh")])

    def test_install_links_only_bin(self) -> None:
        proc = self.install()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(sorted(path.name for path in self.bin.iterdir()), list(COMMANDS))
        for name in COMMANDS:
            self.assertTrue((self.bin / name).is_symlink())
            self.assertEqual((self.bin / name).resolve(), REPO / "bin" / name)
            self.assertIn(name, proc.stdout)
        self.assertEqual(sorted(path.name for path in self.home.iterdir()), [".local"])  # no ~/.agents

    def test_install_refreshes_a_stale_link(self) -> None:
        self.bin.mkdir(parents=True)
        (self.bin / "paseo-title").symlink_to(self.home / "gone")
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual((self.bin / "paseo-title").resolve(), REPO / "bin" / "paseo-title")

    def test_install_keeps_a_regular_file(self) -> None:
        self.bin.mkdir(parents=True)
        mine = self.bin / "paseo-notify"
        mine.write_text("mine")
        proc = self.install()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(mine.is_symlink())
        self.assertEqual(mine.read_text(), "mine")
        self.assertIn("paseo-notify", proc.stderr)
        self.assertEqual(sorted(path.name for path in self.bin.iterdir()), list(COMMANDS))
        self.assertTrue((self.bin / "paseo-title").is_symlink())


if __name__ == "__main__":
    unittest.main()
