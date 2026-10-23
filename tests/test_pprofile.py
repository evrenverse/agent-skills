"""Tests for skills/pprofile/pprofile.py as a command line: the script is run, never imported."""

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.support import TempHome, run_script

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "pprofile" / "pprofile.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "profile"
SESSION = "33333333-3333-4333-8333-333333333333"
LEAD = FIXTURES / f"{SESSION}.jsonl"
ROWS = (
    "Duration (wall-clock)",
    "Gaps > 5 min",
    "API calls (lead)",
    "Cache read (lead)",
    "Subagents",
    "Polled calls",
    "Whole-file reads of large files",
    "Tokens total",
)


def rows(stdout: str) -> dict[str, str]:
    """The completion-report rows of a pprofile run, label to value, in the order printed."""
    section = stdout.split("## Completion-report rows", 1)[1]
    return dict(re.findall(r"^\| (.+?) \| (.+) \|$", section, re.M))


def record(kind: str, stamp: str, content: Any, cwd: str, **extra: Any) -> str:
    return json.dumps(
        {"type": kind, "timestamp": f"2026-09-24T{stamp}.000Z", "cwd": cwd, "message": {"content": content}}
        | extra
    )


class PprofileCliTests(TempHome):
    def profile(self, transcript: Path):
        proc = run_script(SCRIPT, str(transcript), home=self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def test_pprofile_report_rows_are_english(self) -> None:
        proc = self.profile(LEAD)
        report = rows(proc.stdout)
        self.assertEqual(tuple(report), ROWS)
        self.assertTrue(all(label.isascii() and value.isascii() for label, value in report.items()), report)
        # Measured, not estimated: the fixture has 7 lead calls, one 20-minute gap and one subagent.
        self.assertEqual(report["API calls (lead)"], "7")
        self.assertEqual(report["Cache read (lead)"], "140k")
        self.assertEqual(report["Gaps > 5 min"], "20 min in 1 gap(s)")
        self.assertEqual(report["Subagents"], "1 (1 implementing, 0 reviewing, 0 other)")
        self.assertEqual(report["Tokens total"], "13k output, 170k cache read")
        self.assertIn("(2026-09-24 08:00 → 2026-09-24 08:21 UTC)", proc.stdout)

    def test_pprofile_counts_polled_calls_from_the_third_repeat(self) -> None:
        """Five identical `git log` calls are three polled ones; the first two are ordinary use."""
        proc = self.profile(LEAD)
        self.assertEqual(rows(proc.stdout)["Polled calls"], "3")
        self.assertIn("5x Bash", proc.stdout)

    def test_pprofile_flags_whole_file_reads_over_100_kb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "big.md").write_text("x" * (100 * 1024 + 1))
            (work / "small.md").write_text("small")
            calls = [
                ("Read", {"file_path": str(work / "big.md")}),  # whole file: counted
                ("Read", {"file_path": str(work / "big.md"), "limit": 50}),  # a slice: fine
                ("Read", {"file_path": str(work / "small.md")}),  # whole, but small: fine
                ("Bash", {"command": "cat big.md"}),  # relative to the transcript's cwd: counted
                ("Bash", {"command": "cat small.md"}),
            ]
            lines = []
            for n, (name, tool_input) in enumerate(calls):
                tool = {"type": "tool_use", "id": f"toolu_{n}", "name": name, "input": tool_input}
                lines.append(record("assistant", f"08:00:{2 * n:02d}", [tool], str(work)))
                result = [{"type": "tool_result", "tool_use_id": f"toolu_{n}", "content": "ok"}]
                lines.append(record("user", f"08:00:{2 * n + 1:02d}", result, str(work)))
            transcript = work / "session.jsonl"
            transcript.write_text("\n".join(lines))
            report = rows(self.profile(transcript).stdout)
        self.assertEqual(report["Whole-file reads of large files"], "2")

    def test_pprofile_counts_limits_the_way_the_limit_watcher_sees_them(self) -> None:
        """Any limit message counts, the session limit or not; prose about a limit does not."""
        texts = (
            "You've hit your weekly limit · resets 11pm (Etc/UTC)",
            "You've reached your Fable limit. Switch to another model, or wait for it to reset.",
            "The session limit resets at four, so the review waits until then and picks up the open tasks.",
        )
        lines = [
            record("assistant", f"08:00:{n:02d}", [{"type": "text", "text": text}], "/work")
            for n, text in enumerate(texts)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text("\n".join(lines))
            self.assertIn("limit hits 2 ", self.profile(transcript).stdout)

    def test_pprofile_reads_past_broken_lines(self) -> None:
        """A running agent's transcript can end inside a character; a stray non-object line is skipped too."""
        lines = [record("assistant", "08:00:00", [{"type": "text", "text": "Reading."}], "/work"), "[]"]
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_bytes("\n".join(lines).encode() + b'\n{"type": "user", "text": "\xc3')
            self.assertIn("## Completion-report rows", self.profile(transcript).stdout)

    def test_pprofile_finds_an_agent_by_id_prefix(self) -> None:
        """Lookup is the library's: agent record, then the transcript under the Claude config directory."""
        agent = "55555555-5555-4555-8555-555555555555"
        agent_file = self.home / ".paseo" / "agents" / "ws" / f"{agent}.json"
        agent_file.parent.mkdir(parents=True)
        agent_file.write_text(json.dumps({"id": agent, "persistence": {"sessionId": SESSION}}))
        projects = self.home / ".claude" / "projects" / "-work"
        projects.mkdir(parents=True)
        shutil.copy(LEAD, projects / LEAD.name)
        shutil.copytree(FIXTURES / SESSION, projects / SESSION)  # the subagents sit beside the session
        proc = run_script(SCRIPT, agent[:8], home=self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(rows(proc.stdout)["Subagents"], "1 (1 implementing, 0 reviewing, 0 other)")

    def test_pprofile_unknown_agent_exits_non_zero(self) -> None:
        proc = run_script(SCRIPT, "no-such-agent", home=self.home)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("0 agents match", proc.stderr)

    def test_pprofile_runs_through_a_symlink(self) -> None:
        """phandoff runs it from the plugin cache; lib/ is found from the resolved path."""
        link = self.home / "linked-pprofile"
        link.symlink_to(SCRIPT)
        proc = run_script(link, str(LEAD), home=self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("## Completion-report rows", proc.stdout)


if __name__ == "__main__":
    unittest.main()
