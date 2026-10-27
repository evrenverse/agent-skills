"""Tests for paseo_tools.agents."""

import json
import os
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from paseo_tools import agents, cli
from paseo_tools.agents import Message
from tests.support import FakeRun, TempHome, write_json

AGENT = "11111111-1111-4111-8111-111111111111"
SESSION = "22222222-2222-4222-8222-222222222222"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "claude-limit.jsonl"
LIMIT = "You've hit your session limit · resets 4am (Europe/Berlin)"
LIMIT_WRITTEN = datetime(2026, 9, 24, 8, 50, tzinfo=timezone.utc)
LOGS = ("logs", AGENT, "--tail", "1", "--filter", "text")


def record(kind: str, content: Any, **extra: Any) -> str:
    return json.dumps({"type": kind, "message": {"content": content}, **extra})


class AgentFiles(TempHome):
    def agent_file(
        self, provider: str = "claude", session: str = SESSION, metadata: dict | None = None
    ) -> dict:
        data = {
            "id": AGENT,
            "title": "Retry the upload",
            "cwd": "/work",
            "provider": provider,
            "lastStatus": "idle",
            "config": {},
            "persistence": {"sessionId": session, "metadata": metadata or {}},
        }
        write_json(self.home / ".paseo" / "agents" / "ws" / f"{AGENT}.json", data)
        return data

    def claude_transcript(self, turns: list[tuple[str, str]], root: Path | None = None) -> Path:
        path = (root or self.home / ".claude" / "projects") / "-work" / f"{SESSION}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(record(role, body) for role, body in turns))
        return path

    def codex_transcript(self, turns: list[tuple[str, str]], archived: bool = False) -> Path:
        folder = ("archived_sessions",) if archived else ("sessions", "2026", "09", "11")
        path = self.home / ".codex" / Path(*folder) / f"rollout-2026-09-11T19-25-20-{SESSION}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                json.dumps(
                    {
                        "timestamp": "2026-09-11T17:25:20.000Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": role,
                            "content": [{"type": "input_text", "text": body}],
                        },
                    }
                )
                for role, body in turns
            )
        )
        return path

    def asked(self, agent: dict) -> list[str]:
        """What `pagent --task` shows: every user turn that is not harness preamble."""
        path = agents.transcript_path(agent)
        self.assertIsNotNone(path)
        return [
            text
            for _, role, text in agents.turns(path)
            if role == "user" and not text.lstrip().startswith(agents.PREAMBLE)
        ]


class TranscriptTests(AgentFiles):
    def test_task_keeps_every_later_correction(self) -> None:
        agent = self.agent_file()
        self.claude_transcript(
            [
                ("user", "/code-review 7"),
                ("assistant", "Choose A or B."),
                ("user", "use option B, but keep the retry limit at three."),
            ]
        )
        asked = self.asked(agent)
        self.assertIn("/code-review 7", asked)
        self.assertIn(
            "use option B, but keep the retry limit at three.", asked, "a later decision was dropped"
        )

    def test_task_drops_injected_harness_text(self) -> None:
        agent = self.agent_file()
        self.claude_transcript(
            [("user", "Base directory for this skill: /x\n# Code Review"), ("user", "finish it")]
        )
        self.assertEqual(self.asked(agent), ["finish it"])

    def test_codex_history_is_found(self) -> None:
        agent = self.agent_file(provider="codex")
        self.codex_transcript(
            [("user", "take over and finish"), ("assistant", "on it"), ("user", "use option B")]
        )
        self.assertEqual(self.asked(agent), ["take over and finish", "use option B"])

    def test_codex_agents_md_block_is_not_a_request(self) -> None:
        agent = self.agent_file(provider="codex")
        self.codex_transcript(
            [
                ("user", "# AGENTS.md instructions for /repo\n\n<INSTRUCTIONS>rules"),
                ("user", "research the rumours"),
            ]
        )
        self.assertEqual(self.asked(agent), ["research the rumours"])

    def test_archived_codex_history_is_found(self) -> None:
        agent = self.agent_file(provider="codex")
        self.codex_transcript([("user", "finish the archived one")], archived=True)
        self.assertEqual(self.asked(agent), ["finish the archived one"])

    def test_antigravity_history_is_found(self) -> None:
        """The id sits inside a plugin: wrapper, the history in Antigravity's brain."""
        agent = self.agent_file(
            provider="antigravity",
            session=f'plugin:{{"version":1,"data":{{"conversationId":"{SESSION}"}}}}',
            metadata={"pluginProviderPersistence": {"data": {"conversationId": SESSION}}},
        )
        logs = self.home / ".gemini" / "antigravity-cli" / "brain" / SESSION / ".system_generated" / "logs"
        steps = [
            {
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>\nmove the agents\n</USER_REQUEST>\n"
                "<ADDITIONAL_METADATA>\nlocal time\n</ADDITIONAL_METADATA>",
            },
            {
                "type": "PLANNER_RESPONSE",
                "content": "",
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": "paseo ls"}}],
            },
            {"type": "GENERIC", "content": "Created At: x\nCompleted At: y\n\nexit code 0"},
            {
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>\nonly the workspace, nothing else\n</USER_REQUEST>",
            },
        ]
        logs.mkdir(parents=True)
        (logs / "transcript_full.jsonl").write_text(
            "\n".join(
                json.dumps({"step_index": i, "created_at": "2026-10-15T19:58:59Z", **step})
                for i, step in enumerate(steps)
            )
        )
        self.assertEqual(agents.transcript_path(agent), logs / "transcript_full.jsonl")
        self.assertEqual(self.asked(agent), ["move the agents", "only the workspace, nothing else"])
        texts = "\n".join(text for _, _, text in agents.turns(logs / "transcript_full.jsonl"))
        self.assertNotIn("ADDITIONAL_METADATA", texts)
        self.assertIn("⚙ run_command", texts)
        self.assertIn("← exit code 0", texts)

    def test_missing_history_is_none(self) -> None:
        """None, not an empty list: "the agent said nothing" is the wrong conclusion."""
        self.assertIsNone(agents.transcript_path(self.agent_file(session="no-such-session")))
        self.assertIsNone(agents.transcript_path({"id": AGENT}))

    def test_claude_transcript_found_under_a_rotated_config(self) -> None:
        agent = self.agent_file()
        older = self.claude_transcript([("user", "old")])
        newer = self.claude_transcript(
            [("user", "new")],
            root=self.home / ".config" / "claude-rotate" / "configs" / "acct-b" / "projects",
        )
        os.utime(older, (1_000_000, 1_000_000))
        self.assertEqual(agents.transcript_path(agent), newer)

    def test_rotated_config_symlinked_to_the_base_is_not_scanned_twice(self) -> None:
        # claude-rotate symlinks each account's projects/ back to ~/.claude/projects; the dedup must still
        # find the transcript (and not glob the shared tree once per account).
        agent = self.agent_file()
        path = self.claude_transcript([("user", "hi")])
        rotate = self.home / ".config" / "claude-rotate" / "configs"
        for acct in ("acct-a", "acct-b"):
            (rotate / acct).mkdir(parents=True)
            (rotate / acct / "projects").symlink_to(self.home / ".claude" / "projects")
        with mock.patch.object(agents.Path, "glob", autospec=True, side_effect=Path.glob) as spy:
            self.assertEqual(agents.transcript_path(agent), path)
        project_globs = [c for c in spy.call_args_list if str(c.args[1]).endswith(".jsonl")]
        self.assertEqual(len(project_globs), 1, "the shared tree was globbed more than once")

    def test_claude_transcript_found_under_claude_config_dir(self) -> None:
        agent = self.agent_file()
        custom = self.home / "custom-config"
        path = self.claude_transcript([("user", "hi")], root=custom / "projects")
        self.assertIsNone(agents.transcript_path(agent))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(custom)}):
            self.assertEqual(agents.transcript_path(agent), path)

    def test_load_agents_by_id_prefix_or_title(self) -> None:
        self.agent_file()
        [by_id] = agents.load_agents(AGENT[:8])
        self.assertEqual((by_id["id"], by_id["_workspace"]), (AGENT, "ws"))
        self.assertEqual([a["id"] for a in agents.load_agents("UPLOAD")], [AGENT])
        self.assertEqual(agents.load_agents("nothing like it"), [])

    def test_a_title_query_is_never_a_glob(self) -> None:
        """`ptakeover` stops whatever id the lookup returns: a title must not match as a file pattern."""
        advisor = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        write_json(
            self.home / ".paseo" / "agents" / "ws" / f"{advisor}.json", {"id": advisor, "title": "Other"}
        )
        self.agent_file()
        cache = self.home / ".paseo" / "agents" / "ws" / f"{AGENT}.json"
        write_json(cache, {**json.loads(cache.read_text()), "title": "[Advisor] cache bug"})
        self.assertEqual([a["id"] for a in agents.load_agents("[Advisor]")], [AGENT])
        for stars in ("**", "*" * 8):
            self.assertEqual(agents.load_agents(stars), [])

    def test_an_id_prefix_takes_the_filename_fast_path(self) -> None:
        self.agent_file()
        with mock.patch.object(agents, "_read", wraps=agents._read) as read:
            self.assertEqual([a["id"] for a in agents.load_agents(AGENT[:8])], [AGENT])
        read.assert_called_once()


class WhenTests(unittest.TestCase):
    def test_a_timestamp_is_iso_style_local_time(self) -> None:
        self.assertRegex(agents.when("2026-09-24T08:40:00.000Z"), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_no_timestamp_and_unreadable_ones(self) -> None:
        self.assertEqual(agents.when(None), "?")
        self.assertEqual(agents.when("soon"), "soon")


class LastMessageTests(TempHome):
    def test_last_message_skips_sidechain_meta_and_task_notifications(self) -> None:
        lines = [*FIXTURE.read_text().splitlines(), "not json", "[]"]
        self.assertEqual(agents.last_message(lines).text, LIMIT)

    def test_a_later_human_turn_is_the_last_message(self) -> None:
        lines = [record("assistant", [{"type": "text", "text": LIMIT}]), record("user", "keep going")]
        self.assertEqual(agents.last_message(lines).text, "keep going")

    def test_no_text_is_no_message(self) -> None:
        self.assertIsNone(agents.last_message([]))
        self.assertIsNone(
            agents.last_message(["not json", record("user", [{"type": "tool_result", "content": "x"}])])
        )

    def test_last_message_carries_the_line_timestamp(self) -> None:
        self.assertEqual(agents.last_message(FIXTURE.read_text().splitlines()).written_at, LIMIT_WRITTEN)
        # The line carrying the text, not a later bookkeeping line or an earlier message.
        lines = [
            record(
                "assistant", [{"type": "text", "text": "Resets in 1h."}], timestamp="2026-09-05T10:00:00.000Z"
            ),
            record(
                "assistant",
                [{"type": "text", "text": "API Error: Request rejected (429) · Resets in 3h10m55s."}],
                timestamp="2026-09-05T19:14:31.481Z",
            ),
            json.dumps({"type": "last-prompt", "timestamp": "2026-09-05T19:20:00.000Z"}),
        ]
        self.assertEqual(
            agents.last_message(lines),
            Message(
                "API Error: Request rejected (429) · Resets in 3h10m55s.",
                datetime(2026, 9, 5, 19, 14, 31, 481000, tzinfo=timezone.utc),
            ),
        )
        self.assertIsNone(agents.last_message([record("user", "hi")]).written_at)

    def test_tail_finds_the_last_message_of_a_large_transcript(self) -> None:
        path = self.home / "big.jsonl"
        filler = record("assistant", [{"type": "text", "text": "filler " * 20}]) + "\n"
        with path.open("w") as f:
            f.write(filler * (3_000_000 // len(filler) + 1))
            f.write(FIXTURE.read_text())
        self.assertGreater(path.stat().st_size, 3_000_000)
        reads: list[int] = []
        real_open = Path.open

        def spy_open(self_: Path, *args: Any, **kwargs: Any) -> Any:
            handle = real_open(self_, *args, **kwargs)
            real_read = handle.read

            def read(*read_args: Any) -> Any:
                data = real_read(*read_args)
                reads.append(len(data))
                return data

            handle.read = read
            return handle

        with mock.patch.object(Path, "open", spy_open):
            lines = agents.tail_lines(path)
        self.assertTrue(reads, "tail_lines did not read through Path.open")
        self.assertLessEqual(sum(reads), 1_000_000)
        json.loads(lines[0])  # the line cut by the window was dropped
        self.assertEqual(agents.last_message(lines).text, LIMIT)

    def test_tail_keeps_the_first_line_of_a_small_file(self) -> None:
        path = self.home / "small.jsonl"
        path.write_text("first\nsecond\nthird\n")
        self.assertEqual(agents.tail_lines(path), ["first", "second", "third"])
        self.assertEqual(agents.tail_lines(path, max_bytes=8), ["third"])  # window starts inside "second"


class LastMessageOfTests(AgentFiles):
    def test_reads_the_claude_transcript_of_a_listed_agent(self) -> None:
        self.agent_file()
        path = self.home / ".claude" / "projects" / "-work" / f"{SESSION}.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(FIXTURE.read_text())
        fake = FakeRun({})
        with mock.patch.object(cli, "run", fake):
            # A `paseo ls --json` entry names no session; the record on disk does.
            message = agents.last_message_of({"id": AGENT, "status": "idle"})
        self.assertEqual(message, Message(LIMIT, LIMIT_WRITTEN))
        self.assertEqual(fake.calls, [])

    def test_asks_paseo_without_a_claude_transcript(self) -> None:
        agent = self.agent_file(provider="codex")
        self.codex_transcript([("assistant", "a Codex transcript is not read for this")])
        text = "You've hit your usage limit. ... try again at 3:23 PM."
        fake = FakeRun({LOGS: subprocess.CompletedProcess([], 0, text + "\n", "")})
        with mock.patch.object(cli, "run", fake):
            self.assertEqual(agents.last_message_of(agent), Message(text, None))
        self.assertEqual(fake.calls[0][1:], list(LOGS))

    def test_failed_paseo_logs_is_no_message(self) -> None:
        with mock.patch.object(cli, "run", FakeRun({})):
            self.assertIsNone(agents.last_message_of({"id": AGENT}))


class PendingTasksTests(unittest.TestCase):
    """Background work: started by a Bash call or an async subagent, ended by a notification or TaskStop."""

    BASH = '{"toolUseResult":{"stdout":"","backgroundTaskId":"b0000task1","timedOutAfterMs":120000}}'
    SUB = '{"toolUseResult":{"isAsync":true,"status":"async_launched","agentId":"a1111111111111111"}}'
    DONE = '{"type":"queue-operation","content":"<task-notification>\\n<task-id>b0000task1</task-id>\\n"}'
    STOP = '{"type":"tool_use","name":"TaskStop","input":{"task_id":"a1111111111111111"}}'
    RESUMED = '{"toolUseResult":{"success":true,"resumedAgentId":"a1111111111111111"}}'
    # The turn that answers a notification: an assistant record in the main chain.
    ANSWER = record("assistant", [{"type": "text", "text": "The task is done."}])

    def test_started_tasks_are_pending(self) -> None:
        self.assertEqual(agents.pending_tasks([self.BASH, self.SUB]), {"b0000task1", "a1111111111111111"})

    def test_answered_notification_and_stop_end_a_task(self) -> None:
        self.assertEqual(
            agents.pending_tasks([self.BASH, self.SUB, self.DONE, self.ANSWER]), {"a1111111111111111"}
        )
        self.assertEqual(
            agents.pending_tasks([self.BASH, self.SUB, self.DONE, self.STOP, self.ANSWER]), set()
        )

    def test_unanswered_task_notification_still_counts_as_pending(self) -> None:
        # `paseo wait` reports idle in the gap before Claude Code starts the turn that answers the
        # notification; the task must not look finished until that turn has begun.
        text = "<task-notification>\n<task-id>a1111111111111111</task-id>\n"
        queued = json.dumps({"type": "queue-operation", "operation": "enqueue", "content": text})
        delivered = json.dumps(
            {"type": "user", "origin": {"kind": "task-notification"}, "message": {"content": text}}
        )
        lines = [self.RESUMED, queued, delivered]
        self.assertEqual(agents.pending_tasks(lines), {"a1111111111111111"})
        # What a subagent says in its own sidechain is not the main chain answering.
        side = record("assistant", [{"type": "text", "text": "working"}], isSidechain=True)
        self.assertEqual(agents.pending_tasks([*lines, side]), {"a1111111111111111"})
        self.assertEqual(agents.pending_tasks([*lines, self.ANSWER]), set())
        # Out again after the answer: still in order, not sets.
        self.assertEqual(agents.pending_tasks([*lines, self.ANSWER, self.RESUMED]), {"a1111111111111111"})

    def test_resumed_subagent_is_out_again(self) -> None:
        self.assertEqual(agents.pending_tasks([self.SUB, self.STOP, self.RESUMED]), {"a1111111111111111"})
        self.assertEqual(
            agents.pending_tasks([self.SUB, self.STOP, self.ANSWER, self.RESUMED]), {"a1111111111111111"}
        )
        # A notification for a task nobody saw start (outside the tail window) starts nothing.
        self.assertEqual(agents.pending_tasks([self.DONE]), set())

    def test_no_transcript_has_nothing_pending(self) -> None:
        self.assertEqual(agents.pending_tasks(None), set())

    def test_quoted_record_starts_nothing(self) -> None:
        self.assertEqual(agents.pending_tasks([json.dumps({"content": self.BASH})]), set())


if __name__ == "__main__":
    unittest.main()
