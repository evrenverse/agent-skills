"""Tests for skills/pagent/pagent.py as a command line: the script is run, never imported."""

import tempfile
import unittest
from pathlib import Path

from tests import test_agents
from tests.support import run_script, write_json

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "pagent" / "pagent.py"
AGENT = test_agents.AGENT
SESSION = test_agents.SESSION


class PagentCliTests(test_agents.AgentFiles):
    def pagent(self, *args: str):
        return run_script(SCRIPT, *args, home=self.home)

    def test_pagent_task_prints_every_instruction(self) -> None:
        self.agent_file()
        self.claude_transcript(
            [
                ("user", "<system-reminder>injected rules</system-reminder>"),
                ("user", "/code-review 7"),
                ("assistant", "Choose A or B."),
                ("user", "use option B, but keep the retry limit at three."),
            ]
        )
        proc = self.pagent(AGENT[:8], "--task")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("what the user asked for (2)", proc.stdout)
        self.assertIn("/code-review 7", proc.stdout)
        self.assertIn(
            "use option B, but keep the retry limit at three.", proc.stdout, "a later turn was dropped"
        )
        self.assertNotIn("injected rules", proc.stdout)
        self.assertNotIn("Choose A or B.", proc.stdout)  # the agent's turns are for --transcript

    def test_pagent_missing_history_exits_non_zero(self) -> None:
        self.agent_file()  # the record exists, the transcript does not
        for flag in ("--task", "-t"):
            with self.subTest(flag=flag):
                proc = self.pagent(AGENT, flag)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("no transcript on disk", proc.stderr)

    def test_pagent_unknown_agent_exits_non_zero(self) -> None:
        proc = self.pagent("no-such-agent")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no paseo agent matches", proc.stderr)

    def test_pagent_without_query_lists_agents(self) -> None:
        self.agent_file()
        write_json(
            self.home / ".paseo" / "agents" / "ws" / "44444444-4444-4444-8444-444444444444.json",
            {"id": "44444444-4444-4444-8444-444444444444", "title": "Second agent", "lastStatus": "running"},
        )
        proc = self.pagent()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("2 matches, newest first", proc.stdout)
        self.assertIn("Retry the upload", proc.stdout)
        self.assertIn("Second agent", proc.stdout)

    def test_pagent_transcript_shows_the_last_turns(self) -> None:
        self.agent_file()
        self.claude_transcript([("user", "first"), ("assistant", "second"), ("user", "third")])
        proc = self.pagent(AGENT, "-t", "-n", "2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("last 2 turns", proc.stdout)
        self.assertNotIn("first", proc.stdout)
        self.assertIn("second", proc.stdout)
        self.assertIn("third", proc.stdout)

    def test_pagent_finds_history_under_claude_config_dir(self) -> None:
        """The lookup is the library's, so it follows CLAUDE_CONFIG_DIR like every other tool."""
        self.agent_file()
        with tempfile.TemporaryDirectory() as config:
            self.claude_transcript([("user", "from the other config")], root=Path(config) / "projects")
            env = {"CLAUDE_CONFIG_DIR": config}
            proc = run_script(SCRIPT, AGENT, "--task", home=self.home, extra_env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("from the other config", proc.stdout)

    def test_pagent_runs_through_a_symlink(self) -> None:
        """Plugin caches and bin directories link to the script; lib/ is found from the real path."""
        self.agent_file()
        link = self.home / "linked-pagent"
        link.symlink_to(SCRIPT)
        proc = run_script(link, AGENT, home=self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"id        {AGENT}", proc.stdout)


if __name__ == "__main__":
    unittest.main()
