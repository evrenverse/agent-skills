"""Tests for paseo_tools.title. Nothing here runs the real claude or paseo."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from paseo_tools import cli, title
from tests.support import FakeRun, TempHome

CLAUDE = "/resolved/claude"
PASEO = "/resolved/paseo"
# Everything `claude -p` must be started with so that it runs no hooks, plugins, MCP servers or tools.
ISOLATION = ["--output-format", "json", "--tools", "", "--strict-mcp-config", "--no-session-persistence"]


def resolved(binary: str) -> str:
    # claude-rotate counts as not installed unless a test says otherwise.
    return binary if binary == "claude-rotate" else f"/resolved/{binary}"


def resolved_with_rotate(binary: str) -> str:
    return f"/resolved/{binary}"


def done(stdout: str = "", code: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, stderr)


def reply(result: Any, **extra: Any) -> subprocess.CompletedProcess[str]:
    return done(json.dumps({"type": "result", "is_error": False, "result": result, **extra}))


def uid(digit: str) -> str:
    return f"{digit * 8}-{digit * 4}-4{digit * 3}-8{digit * 3}-{digit * 12}"


def logged(run: Any, *args: Any, **kwargs: Any) -> tuple[Any, str]:
    """(result, stdout) of `run(*args, **kwargs)`."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = run(*args, **kwargs)
    return result, out.getvalue()


LONG = "Investigate why the nightly import job drops rows when batches grow"  # 67 characters, cut at 60


class AutoTitleTests(unittest.TestCase):
    def test_a_short_message_is_its_own_title(self) -> None:
        self.assertTrue(title.is_auto_title("Fix the login page redirect", "Fix the login page redirect"))
        self.assertTrue(
            title.is_auto_title(
                "look into the thing with /pagent 1111", "look into the thing with /pagent 1111"
            )
        )

    def test_a_long_message_is_cut_at_exactly_60_characters(self) -> None:
        self.assertTrue(
            title.is_auto_title("Investigate why the nightly import job drops rows when batch", LONG)
        )
        self.assertFalse(title.is_auto_title(LONG[:59], LONG), "one character less")
        self.assertFalse(title.is_auto_title(LONG[:61], LONG), "one character more")
        self.assertFalse(title.is_auto_title(LONG, LONG), "the whole message is no title of paseo's")

    def test_the_cut_is_hard_and_carries_no_ellipsis(self) -> None:
        self.assertFalse(title.is_auto_title(LONG[:59] + "\u2026", LONG))
        self.assertTrue(title.is_auto_title("x" * 60, "x" * 80), "mid-word, not at a word boundary")
        # "word " * 12 is exactly 60 units; the space at the cut is trimmed.
        self.assertTrue(title.is_auto_title("word " * 11 + "word", "word " * 20))

    def test_only_the_first_non_empty_line_counts_with_whitespace_collapsed(self) -> None:
        self.assertTrue(title.is_auto_title("Fix the login", "Fix the login\n\nThe redirect drops the page"))
        self.assertTrue(title.is_auto_title("Fix the login", "\n  \r\n  Fix \t the   login  \r\nmore"))
        self.assertFalse(title.is_auto_title("Fix the login The redirect", "Fix the login\nThe redirect"))
        self.assertFalse(title.is_auto_title("anything", "  \n "))

    def test_the_cut_counts_utf16_units_like_paseo(self) -> None:
        """An emoji is two units: the cut after unit 60 splits it, leaving a lone surrogate in the title."""
        message = "a" * 59 + "\U0001f600" + " and more"
        self.assertTrue(title.is_auto_title("a" * 59 + "\ud83d", message))
        self.assertFalse(title.is_auto_title("a" * 59 + "\U0001f600", message))

    def test_a_title_that_only_begins_the_first_message_is_not_paseos(self) -> None:
        """What this tool writes can begin the message; it must not look automatic again."""
        message = "Login bug: users cannot sign in\u2026"
        self.assertEqual(title.clean("Login bug"), "Login bug")
        self.assertFalse(title.is_auto_title("Login bug", message))
        self.assertFalse(title.is_auto_title("Fix the login", "Fix the login page redirect"))
        self.assertFalse(title.is_auto_title("So, we have the\u2026", "So, we have the ingestion now"))

    def test_slash_command_titles(self) -> None:
        message = "/superpowers:brainstorming Let us start here"
        for typed in (
            "/brainstorming",  # the short command, arguments on the next line
            "/brainstorming Let us start here",
            "/superpowers:brainstorming",
            "/superpowers:brainstorming Let us start here",
        ):
            with self.subTest(typed):
                self.assertTrue(title.is_auto_title(typed, message))
        for other in ("/brainstorm", "/brainstorming something else", "brainstorming", "/plan"):
            with self.subTest(other):
                self.assertFalse(title.is_auto_title(other, message))

    def test_other_titles_are_left_alone(self) -> None:
        self.assertFalse(title.is_auto_title("Mobile App", "<command-message>takeover</command-message>"))
        self.assertFalse(title.is_auto_title("Login Bug", "Take a look at PR 43, we want"))
        self.assertFalse(title.is_auto_title("Impl: invoice-export", 'You are implementing "Invoice export'))
        self.assertFalse(title.is_auto_title("NIGHTLY: report", "Read /repo/projects"))
        self.assertFalse(title.is_auto_title("", "anything"))
        self.assertFalse(title.is_auto_title("", ""))

    def test_a_title_written_by_this_tool_is_never_rewritten(self) -> None:
        """It is not what paseo derives from the first message: that makes the tool run once per tab."""
        self.assertFalse(title.is_auto_title("Weekly report", "draft the weekly status report for the team"))


class PaseoTitleTests(unittest.TestCase):
    def test_blank_messages_have_no_title(self) -> None:
        for blank in ("", "  \n\t ", "\r\n"):
            self.assertIsNone(title.paseo_title(blank))

    def test_the_title_of_a_message(self) -> None:
        self.assertEqual(title.paseo_title("  Fix   the\tlogin \nrest"), "Fix the login")
        self.assertEqual(title.paseo_title(LONG), LONG[:60])


class CleanTests(unittest.TestCase):
    def test_quotes_and_trailing_period_are_stripped(self) -> None:
        self.assertEqual(title.clean('  "Invoices". '), "Invoices")

    def test_a_long_title_is_cut_at_a_word_boundary(self) -> None:
        self.assertEqual(title.clean("Quarterly review of invoices"), "Quarterly")
        self.assertLessEqual(len(title.clean("A very long title about everything") or ""), title.LIMIT)

    def test_a_word_without_boundary_is_cut_hard(self) -> None:
        self.assertEqual(title.clean("Internationalization"), "Internationaliz")

    def test_a_cut_that_leaves_under_three_characters_is_worse_than_a_hard_cut(self) -> None:
        self.assertEqual(title.clean("UI - Notifications"), "UI - Notificati")

    def test_unusable_titles_are_none(self) -> None:
        for raw in ("Ab", "", None, "/brainstorming", '""'):
            with self.subTest(raw=raw):
                self.assertIsNone(title.clean(raw))


class OpeningTests(unittest.TestCase):
    def test_slash_command_envelope_is_the_bare_command_with_its_arguments(self) -> None:
        envelope = (
            "<command-message>superpowers:brainstorming</command-message>"
            "<command-name>/superpowers:brainstorming</command-name>"
            "<command-args>Set tab titles</command-args>"
        )
        self.assertEqual(title.opening([("user", envelope)]), "/superpowers:brainstorming Set tab titles")

    def test_the_arguments_keep_their_line_breaks(self) -> None:
        envelope = (
            "<command-message>plan</command-message><command-name>/plan</command-name>"
            "<command-args>first line\nsecond line</command-args>"
        )
        self.assertEqual(title.opening([("user", envelope)]), "/plan first line\nsecond line")

    def test_the_first_user_message_wins(self) -> None:
        self.assertEqual(title.opening([("assistant", "hi"), ("user", "plain text")]), "plain text")
        self.assertEqual(title.opening([("assistant", "hi")]), "")


class ContextTests(unittest.TestCase):
    def test_opening_first_answer_and_follow_ups(self) -> None:
        turns = [("user", "first question"), ("assistant", "x" * 200), ("user", "second"), ("user", "third")]
        body = title.context(turns)
        self.assertIn("USER 1: first question", body)
        self.assertIn("AGENT: ", body)
        self.assertIn("USER 3: third", body)

    def test_every_message_is_squeezed_onto_one_line(self) -> None:
        body = title.context([("user", "first\n\n  question"), ("user", "second\nline")])
        self.assertIn("USER 1: first question", body)
        self.assertIn("USER 2: second line", body)

    def test_nothing_without_a_user_turn(self) -> None:
        self.assertEqual(title.context([("assistant", "only me")]), "")

    def test_context_never_exceeds_2000_characters(self) -> None:
        turns = [("user", "u" * 5000), ("assistant", "a" * 5000), ("user", "v" * 5000), ("user", "w" * 5000)]
        self.assertLessEqual(len(title.context(turns)), 2000)
        self.assertLessEqual(len(title.context(turns, budget=50)), 50)


class HeadTurnsTests(TempHome):
    def write(self, *records: Any) -> Path:
        path = self.home / "t.jsonl"
        path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in records))
        return path

    @staticmethod
    def msg(role: str, text: str) -> dict:
        return {"type": role, "message": {"content": text}}

    def test_harness_preamble_is_not_a_user_turn(self) -> None:
        path = self.write(self.msg("user", "<system-reminder>x</system-reminder>"), self.msg("user", "hello"))
        self.assertEqual(title.head_turns(path), [("user", "hello")])

    def test_line_breaks_are_kept_because_paseo_titles_after_the_first_line(self) -> None:
        path = self.write(self.msg("user", "Fix the login\n\nThe redirect drops the page"))
        self.assertEqual(title.head_turns(path), [("user", "Fix the login\n\nThe redirect drops the page")])

    def test_stops_one_user_turn_past_the_threshold(self) -> None:
        path = self.write(*(self.msg("user", f"message {i}") for i in range(1, 9)))
        self.assertEqual(len(title.head_turns(path)), title.MIN_USER_TURNS + 1)

    def test_garbage_lines_are_skipped_and_a_missing_file_is_empty(self) -> None:
        path = self.write("not json", "[1, 2]", self.msg("user", "hello"))
        self.assertEqual(title.head_turns(path), [("user", "hello")])
        self.assertEqual(title.head_turns(self.home / "missing.jsonl"), [])

    def test_a_tool_call_never_reaches_the_prompt(self) -> None:
        """A tool call renders as `⚙ name {arguments}`: whatever a command was given is not the topic."""
        command = "grep -rn launch-date ~/notes/private-plan.md " + "-e x " * 40
        call = {"type": "tool_use", "name": "Bash", "input": {"command": command}}
        answer = "The upload fails because the retry loop drops the session cookie. " * 3
        path = self.write(
            self.msg("user", "look at the upload"),
            {"type": "assistant", "message": {"content": [call]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Checking."}, call]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}},
            self.msg("user", "fix it"),
        )
        body = title.context(title.head_turns(path))
        self.assertNotIn("private-plan", body)
        self.assertNotIn("⚙", body)
        self.assertIn("AGENT: The upload fails", body)

    def test_never_reads_past_max_lines(self) -> None:
        with mock.patch.object(title, "MAX_LINES", 2):
            path = self.write(
                self.msg("assistant", "a"), self.msg("assistant", "b"), self.msg("user", "late")
            )
            self.assertEqual(title.head_turns(path), [("assistant", "a"), ("assistant", "b")])


class AskTests(TempHome):
    def ask(self, proc: subprocess.CompletedProcess[str]) -> tuple[Any, FakeRun, str]:
        fake = FakeRun({("-p",): proc})
        with mock.patch.object(cli, "run", fake), mock.patch.object(cli, "resolve", resolved):
            result, out = logged(title.ask, "name it")
        return result, fake, out

    def test_ask_runs_claude_print_with_the_isolation_flags(self) -> None:
        cwd_seen: list[bool] = []
        inner = FakeRun({("-p",): reply("Login")})

        def spy(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            cwd_seen.append(Path(kwargs["cwd"]).is_dir())
            return inner(argv, **kwargs)

        with mock.patch.object(cli, "run", spy), mock.patch.object(cli, "resolve", resolved):
            result = title.ask("name it")
        argv, kwargs = inner.calls[0], inner.kwargs[0]
        self.assertEqual(result, "Login")
        self.assertEqual(argv[0], CLAUDE)
        self.assertEqual(argv[1:4], ["-p", "--model", "haiku"])
        for flag in ISOLATION:
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertEqual(argv[-2:], ["--setting-sources", ""])
        # The prompt goes in on stdin: --tools is variadic and would swallow a trailing positional.
        self.assertNotIn("name it", argv)
        self.assertEqual(kwargs["input"], "name it")
        self.assertEqual(kwargs["timeout"], 90)
        # Environment untouched (a login may depend on CLAUDE_CONFIG_DIR); working directory a fresh temp dir.
        self.assertNotIn("env", kwargs)
        self.assertEqual(cwd_seen, [True])
        self.assertEqual(Path(kwargs["cwd"]).parent, Path(tempfile.gettempdir()))
        self.assertNotEqual(Path(kwargs["cwd"]), Path.cwd())
        self.assertFalse(Path(kwargs["cwd"]).exists(), "temporary directory left behind")

    def test_ask_goes_through_claude_rotate_when_it_is_installed(self) -> None:
        # Cron sees no shell alias; claude-rotate run starts claude on a logged-in account.
        fake = FakeRun({("run", "-p"): reply("Login")})
        with mock.patch.object(cli, "run", fake), mock.patch.object(cli, "resolve", resolved_with_rotate):
            result = title.ask("name it")
        self.assertEqual(result, "Login")
        argv = fake.calls[0]
        self.assertEqual(argv[:2], ["/resolved/claude-rotate", "run"])
        self.assertEqual(argv[2:], [*title.CLAUDE_FLAGS])

    def test_the_title_is_the_stripped_result(self) -> None:
        result, _, out = self.ask(reply("  Login\n", total_cost_usd=0.001))
        self.assertEqual(result, "Login")
        self.assertEqual(out, "")

    def test_a_failed_call_gives_none_and_one_log_line(self) -> None:
        cases = {
            "error reply": reply("You've hit your limit", is_error=True),
            "error reply with exit 1": done(json.dumps({"is_error": True, "result": "bad"}), code=1),
            "non-zero exit": done("", code=1, stderr="boom"),
            "not installed": done("", code=127, stderr="no such file"),
            "timeout": done("", code=124, stderr="timed out after 90s: claude"),
            "not JSON": done("hello"),
            "no result": done(json.dumps({"is_error": False})),
            "result is not text": done(json.dumps({"is_error": False, "result": ["x"]})),
            "JSON but no object": done("[1]"),
        }
        for name, proc in cases.items():
            with self.subTest(name):
                result, _, out = self.ask(proc)
                self.assertIsNone(result)
                self.assertEqual(len(out.splitlines()), 1, out)
                self.assertIn("claude", out)

    def test_the_log_line_carries_the_reason(self) -> None:
        _, _, out = self.ask(reply("You've hit your limit", is_error=True))
        self.assertIn("You've hit your limit", out)


class PromptTests(unittest.TestCase):
    def test_prompt_asks_for_one_short_title_in_the_conversation_language(self) -> None:
        text = title.PROMPT.format(limit=title.LIMIT, repo="app", body="USER 1: {hi}")
        self.assertIn(f"AT MOST {title.LIMIT} characters", text)
        self.assertIn("language of the conversation", text)
        self.assertIn("noun", text)
        for example in ('"Login Bug"', '"Invoices"', '"Mobile App"'):
            self.assertIn(example, text)
        self.assertTrue(text.endswith("USER 1: {hi}"))


FIRST = "Fix the login page redirect"
ANSWER = "I read the router and the session code. The redirect drops the target page after sign-in. " * 2


class PassTests(TempHome):
    """One pass of `main` over agent records and transcripts on disk, with claude and paseo faked."""

    def setUp(self) -> None:
        super().setUp()
        self.claude = reply("Login")
        self.update = done()

    def add(
        self,
        n: int,
        name: str | None = None,
        *,
        provider: str = "claude",
        first: str = FIRST,
        users: int = 3,
        archived: str | None = None,
        transcript: bool = True,
    ) -> str:
        """An agent record (id from digit `n`) with its transcript; returns the agent id.

        Unless `name` says otherwise the tab carries what paseo derives from a short first message: all of it.
        """
        agent_id, session = uid(str(n)), uid(str(n + 4))
        turns = [("user", first), ("assistant", ANSWER)]
        turns += [("user", f"follow-up {i}") for i in range(2, users + 1)]
        persistence: dict[str, Any] = {"sessionId": session, "metadata": {}}
        if provider == "antigravity":
            persistence = {
                "sessionId": f'plugin:{{"version":1,"data":{{"conversationId":"{session}"}}}}',
                "metadata": {"pluginProviderPersistence": {"data": {"conversationId": session}}},
            }
        record = {
            "id": agent_id,
            "title": first if name is None else name,
            "cwd": "/work/shop",
            "provider": provider,
            "archivedAt": archived,
            "persistence": persistence,
        }
        agent_dir = self.home / ".paseo" / "agents" / "ws"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / f"{agent_id}.json").write_text(json.dumps(record))
        if transcript:
            self.write_transcript(provider, session, turns)
        return agent_id

    def write_transcript(self, provider: str, session: str, turns: list[tuple[str, str]]) -> None:
        if provider == "claude":
            path = self.home / ".claude" / "projects" / "-work" / f"{session}.jsonl"
            lines = [{"type": role, "message": {"content": text}} for role, text in turns]
        elif provider == "codex":
            path = self.home / ".codex" / "sessions" / "2026" / "09" / "11" / f"rollout-x-{session}.jsonl"
            kind = {"user": "input_text", "assistant": "output_text"}
            lines = [
                {
                    "timestamp": "2026-09-11T17:25:20.000Z",
                    "payload": {
                        "type": "message",
                        "role": role,
                        "content": [{"type": kind[role], "text": text}],
                    },
                }
                for role, text in turns
            ]
        else:
            brain = self.home / ".gemini" / "antigravity-cli" / "brain" / session
            path = brain / ".system_generated" / "logs" / "transcript_full.jsonl"
            lines = [
                {"step_index": i, "created_at": "2026-10-15T19:58:59Z", **step}
                for i, step in enumerate(
                    {"type": "USER_INPUT", "content": f"<USER_REQUEST>\n{text}\n</USER_REQUEST>"}
                    if role == "user"
                    else {"type": "PLANNER_RESPONSE", "content": text}
                    for role, text in turns
                )
            ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(line) for line in lines))

    def run_main(self, *argv: str) -> tuple[FakeRun, str]:
        fake = FakeRun({("-p",): self.claude, ("agent", "update"): self.update})
        with mock.patch.object(cli, "run", fake), mock.patch.object(cli, "resolve", resolved):
            code, out = logged(title.main, list(argv))
        self.assertEqual(code, 0)
        return fake, out

    @staticmethod
    def renamed(fake: FakeRun) -> list[tuple[str, str]]:
        return [(c[3], c[5]) for c in fake.calls if c[:3] == [PASEO, "agent", "update"]]

    @staticmethod
    def prompts(fake: FakeRun) -> list[str]:
        return [kw["input"] for c, kw in zip(fake.calls, fake.kwargs, strict=True) if c[0] == CLAUDE]

    def test_renames_only_auto_titled_live_agents_with_enough_turns(self) -> None:
        mine = self.add(1)
        self.add(2, "Mobile App")  # named by a human
        self.add(3, users=1)  # too few user turns
        self.add(4, archived="2026-09-01T00:00:00Z")
        self.add(5, transcript=False)
        self.add(6, "Fix the login")  # begins the first message, but is not what paseo derives from it
        fake, out = self.run_main()
        self.assertEqual(self.renamed(fake), [(mine, "Login")])
        self.assertEqual(len(self.prompts(fake)), 1)
        self.assertIn("renamed 11111111", out)
        self.assertIn(f"{FIRST!r} -> 'Login'", out)

    def test_titles_agents_of_every_provider(self) -> None:
        ids = {
            "claude": self.add(1, provider="claude", first=FIRST + " (claude)"),
            "codex": self.add(2, provider="codex", first=FIRST + " (codex)"),
            "antigravity": self.add(3, provider="antigravity", first=FIRST + " (agy)"),
        }
        fake, _ = self.run_main()
        self.assertCountEqual(self.renamed(fake), [(agent_id, "Login") for agent_id in ids.values()])
        prompts = self.prompts(fake)
        self.assertEqual(len(prompts), 3)
        for marker in ("(claude)", "(codex)", "(agy)"):
            self.assertEqual(sum(marker in p for p in prompts), 1, marker)
        for text in prompts:
            self.assertIn("USER 1: Fix the login page redirect", text)
            self.assertIn("AGENT: I read the router", text)
            self.assertIn("USER 3: follow-up 3", text)
            self.assertIn("project: shop", text)

    def test_dry_run_renames_nothing(self) -> None:
        self.add(1)
        fake, out = self.run_main("--dry-run")
        self.assertEqual(self.renamed(fake), [])
        self.assertIn("would rename 11111111", out)

    def test_a_refused_rename_is_logged_and_the_pass_goes_on(self) -> None:
        self.add(1)
        self.add(2)
        self.update = done("", code=1, stderr="daemon not running")
        fake, out = self.run_main()
        self.assertEqual(len(self.renamed(fake)), 2)
        self.assertEqual(out.count("rename failed"), 2)
        self.assertIn("daemon not running", out)
        self.assertNotIn("renamed", out)

    def test_an_unusable_or_unchanged_title_is_skipped_with_a_log_line(self) -> None:
        for first, answer in ((FIRST, "/plan"), (FIRST, "Ab"), ("Fix login", "Fix login")):
            with self.subTest(first=first, answer=answer):
                self.claude = reply(answer)
                self.add(1, first=first)
                fake, out = self.run_main()
                self.assertEqual(self.renamed(fake), [])
                self.assertEqual(len(out.splitlines()), 1, out)
                self.assertIn("skipped 11111111", out)

    def test_a_title_this_tool_wrote_is_not_asked_for_again(self) -> None:
        """The reported loop: the model's "Login bug" begins the first message but is not paseo's title."""
        self.add(1, "Login bug", first="Login bug: users cannot sign in")
        fake, out = self.run_main()
        self.assertEqual(fake.calls, [])
        self.assertEqual(out, "")

    def test_the_title_comes_from_the_first_line_of_a_multi_line_message(self) -> None:
        first = "Fix the login page\n\nThe redirect drops the page after sign-in."
        agent = self.add(1, "Fix the login page", first=first)
        fake, _ = self.run_main()
        self.assertEqual(self.renamed(fake), [(agent, "Login")])

    def test_an_unavailable_claude_ends_the_pass(self) -> None:
        """Two agents, one failing call: not 90 seconds of timeout per tab, and no half-renamed pass."""
        self.add(1)
        self.add(2)
        self.claude = done("", code=1, stderr="rate limited")
        fake, out = self.run_main()
        self.assertEqual(len(self.prompts(fake)), 1)
        self.assertEqual(self.renamed(fake), [])
        self.assertEqual(len(out.splitlines()), 1, out)

    def test_help_exits_zero_and_names_the_option(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            title.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--dry-run", out.getvalue())


if __name__ == "__main__":
    unittest.main()
