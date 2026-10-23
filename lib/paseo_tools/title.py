"""paseo-title: give every paseo tab a short, high-level name.

    paseo-title              rename
    paseo-title --dry-run    show what it would rename, change nothing

paseo names a new agent after the first thing typed into it, so the tab strip fills up with "look into the
thing with /pagent 11111111-…" and "/brainstorming". This walks the live agents (the ones the tab strip
shows), works out which of those titles are paseo's own, and replaces each with a short topic written by the
user's own Claude (`claude -p --model haiku`, through `claude-rotate run` when claude-rotate is installed).
Cron runs it; one run is one pass and the exit code is 0.

WHAT IT NEVER TOUCHES
  - Anything a human or a skill named. A title counts as paseo's own only when it equals, character for
    character, what paseo derives from the first message (`paseo_title`), so "Impl: invoice-export",
    "Mobile App" and a title that merely begins the first message are left alone. The same test is why a
    title this tool wrote is not rewritten: it is not what paseo derives. No state file, no marker label. If a
    paseo update changes how it derives titles, nothing is renamed until `paseo_title` follows.
  - Agents with fewer than MIN_USER_TURNS real user messages. The opening message alone rarely says what a
    session is about ("have a quick look"); the model needs the first answer and a follow-up or two.
  - Archived agents. `archivedAt is None` is exactly what `paseo ls -g` lists, so the work stays proportional
    to the tab strip, not to the archive.

THE MODEL CALL is isolated (see `CLAUDE_FLAGS`): no hooks, no MCP servers, no tools, no saved session, and no
settings of the user or of an agent's repository. What still runs despite the flags: the built-in system
prompt and the built-in plugins (without hooks); managed policy settings would still apply. At most about
2 000 characters of one conversation go into the prompt. When claude gives no answer, the pass stops and the
next cron tick tries again.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path

from paseo_tools import agents, cli

# Hard cap on the rendered title. Short enough that a tab strip stays readable; a longer one is cut at a word
# boundary rather than dropped.
LIMIT = 15
# Below this the model is guessing from an opening line alone, the very failure this tool exists to avoid.
MIN_USER_TURNS = 3
# A transcript runs to tens of MB, but the topic is decided in its opening. Stop reading once there is enough,
# and never walk more than this many lines.
MAX_LINES = 4000
TIMEOUT = 90
# The prompt goes in on stdin, never as an argument: --tools is variadic and swallows a trailing positional.
CLAUDE_FLAGS = (
    "-p",
    "--model",
    "haiku",
    "--output-format",
    "json",
    "--tools",
    "",
    "--strict-mcp-config",
    "--no-session-persistence",
    "--setting-sources",
    "",
)

# paseo cuts a derived title at 60 UTF-16 units: min(60, MAX_EXPLICIT_AGENT_TITLE_CHARS = 200).
AUTO_TITLE_UNITS = 60
# A slash command reaches the transcript wrapped in the harness envelope while paseo titles the tab with what
# was typed, so the transcript text alone is not the title.
SLASH_RE = re.compile(r"<command-message>.*?</command-message>\s*<command-name>(.*?)</command-name>", re.S)
ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)

PROMPT = """You name tabs in an agent UI. Give ONE title for the conversation below.

Rules:
- AT MOST {limit} characters. Hard limit.
- Very high-level: the topic, not the current step. A noun or a short noun phrase.
- Write it in the language of the conversation. No imperative verb, no sentence, no quotes,
  no trailing period.
- Answer with the title and nothing else.

Examples: "Login Bug", "Invoices", "Mobile App"

Conversation (project: {repo}):
{body}"""


def log(line: str) -> None:
    """Print a timestamped line; cron redirects stdout to the log."""
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}", flush=True)


def live_agents() -> list[dict]:
    """Agents the tab strip shows: everything not archived."""
    return [
        rec
        for rec in agents.load_agents()
        if rec.get("archivedAt") is None and rec.get("id") and rec.get("title")
    ]


def head_turns(path: Path, want_users: int = MIN_USER_TURNS) -> list[tuple[str, str]]:
    """(role, text) for the opening of a transcript, stopping once there is enough.

    Reading the whole file would mean tens of MB per agent per cron tick for a decision that only ever looks
    at the beginning. The text keeps its line breaks: paseo titles an agent after the first line only.
    """
    out: list[tuple[str, str]] = []
    users = 0
    try:
        # Fixed UTF-8: cron may run with a non-UTF-8 locale.
        with path.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= MAX_LINES:
                    break
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                found = agents.turn(rec) if isinstance(rec, dict) else None
                if not found:
                    continue
                _, role, text = found
                if role == "user":
                    if text.lstrip().startswith(agents.PREAMBLE):
                        continue
                    users += 1
                out.append((role, text))
                # One turn past the threshold, so the last follow-up has an answer behind it.
                if users > want_users:
                    break
    except OSError:
        pass  # unreadable: what was read so far decides, and a short opening is skipped anyway
    return out


def opening(turns: list[tuple[str, str]]) -> str:
    """The first user message as paseo would have seen it when it titled the tab."""
    for role, text in turns:
        if role != "user":
            continue
        cmd = SLASH_RE.match(text)
        if cmd:
            args = ARGS_RE.search(text)
            return (cmd.group(1) + " " + (args.group(1) if args else "")).strip()
        return text
    return ""


def paseo_title(message: str) -> str | None:
    """The title paseo gives an agent whose first message is `message`, or None for a blank message.

    The first non-empty line, whitespace collapsed, cut at 60 UTF-16 units. No ellipsis, no word boundary.
    """
    # Mirrors create-agent-title.js:3-17 (deriveInitialAgentTitle) in @getpaseo/server 0.12.0 (paseo CLI).
    # shortcut: Python's \s and strip() differ from JS on U+FEFF, U+001C-1F, U+0085; a title is then missed.
    first = next((s for s in map(str.strip, re.split(r"\r?\n", message.strip())) if s), "")
    units = re.sub(r"\s+", " ", first).encode("utf-16-le", "surrogatepass")[: 2 * AUTO_TITLE_UNITS]
    return units.decode("utf-16-le", "surrogatepass").strip() or None


def is_auto_title(title: str, first_message: str) -> bool:
    """True when `title` is exactly what paseo derives from `first_message`, so replacing it destroys nothing.

    A prefix is not enough: a title this tool wrote ("Login bug") can begin the first message ("Login bug:
    users cannot sign in"), and then it would be asked for again on every run, and a person's title would be
    overwritten the same way.
    """
    forms = [first_message]
    message = first_message.strip()
    if command := re.match(r"/\S+", message):
        # The transcript names a slash command in full ("/ns:cmd"), but the tab carries what was typed: the
        # short "/cmd", and the arguments may have started a line of their own.
        short = "/" + command[0][1:].rsplit(":", 1)[-1]
        forms += [command[0], short, short + message[command.end() :]]
    return title in {paseo_title(form) for form in forms}


def context(turns: list[tuple[str, str]], budget: int = 2000) -> str:
    """What the model gets: the opening, the first real answer, the follow-ups; at most `budget` characters.

    The answer matters as much as the question: an agent that spent a turn reading the repo says what the
    session is about far more plainly than "have a quick look". Only prose goes in: `agents.turn` renders a
    tool call or result as a line of its own (`⚙ name {arguments}`, `← output`), and whatever a command was
    given or returned is not the topic and may be anything.
    """
    prose = [
        (role, "\n".join(s for s in text.splitlines() if not s.startswith(("⚙ ", "← "))))
        for role, text in turns
    ]
    # One line per message: the prompt is smaller and the labels stay at the start of a line.
    squeezed = [(role, " ".join(text.split())) for role, text in prose]
    users = [text for role, text in squeezed if role == "user"]
    answers = [text for role, text in squeezed if role == "assistant" and len(text) > 120]
    if not users:
        return ""
    parts = [f"USER 1: {users[0][:700]}"]
    if answers:
        parts.append(f"AGENT: {answers[0][:400]}")
    for i, msg in enumerate(users[1:3], 2):
        parts.append(f"USER {i}: {msg[:400]}")
    return "\n".join(parts)[:budget]


def claude_command() -> list[str]:
    """How to start Claude Code: `claude-rotate run` when claude-rotate is installed, else the first `claude`.

    Cron sees no shell alias, and claude-rotate keeps its logins in its own config directories:
    `claude-rotate run` starts claude on a logged-in account, where a bare `claude` may find only an expired
    default login.
    """
    rotate = cli.resolve("claude-rotate")
    return [rotate, "run"] if Path(rotate).is_absolute() else [cli.resolve("claude")]


def ask(prompt: str) -> str | None:
    """The reply of the user's own Claude to `prompt`, or None (after one log line) when it gave none.

    It runs from a fresh temporary directory so that nothing of an agent's repository is in reach, with the
    environment unchanged (the login may depend on CLAUDE_CONFIG_DIR). `is_error`, a non-zero exit and
    unparseable output all mean "no answer": nothing is renamed.
    """
    with tempfile.TemporaryDirectory() as cwd:
        proc = cli.run([*claude_command(), *CLAUDE_FLAGS], timeout=TIMEOUT, cwd=cwd, input=prompt)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        data = None
    result = data.get("result") if isinstance(data, dict) else None
    if proc.returncode == 0 and isinstance(result, str) and not data.get("is_error"):
        return result.strip()
    detail = result if isinstance(result, str) else proc.stderr or proc.stdout
    log(f"claude gave no title (exit {proc.returncode}): {' '.join(detail.split())[:200]!r}")
    return None


def clean(raw: str | None, limit: int = LIMIT) -> str | None:
    """A usable title, or None. Over-long output is cut at a word boundary."""
    # Quotes and a trailing period in any order: models mix them despite the rules.
    title = re.sub(r"^[\s\"']+|[\s\"'.]+$", "", " ".join((raw or "").split()))
    if not title or title.startswith("/"):
        return None
    if len(title) > limit:
        cut = title[:limit].rsplit(" ", 1)[0].rstrip(" -\u2013\u2014")  # hyphen, en dash, em dash
        # A word-boundary cut that leaves under three characters is worse than a hard cut.
        title = cut if len(cut) >= 3 else title[:limit]
    return title if len(title) >= 3 else None


def rename(agent_id: str, title: str, dry_run: bool) -> bool:
    """Set the agent's name (not on a dry run). False, after a log line, when paseo refuses."""
    if dry_run:
        return True
    proc = cli.paseo("agent", "update", agent_id, "--name", title, timeout=30)
    if proc.returncode != 0:
        log(f"rename failed for {agent_id[:8]}: {(proc.stderr or proc.stdout).strip()[:200]}")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    """The `paseo-title` command line: one pass over the live agents, exit code 0."""
    parser = argparse.ArgumentParser(
        prog="paseo-title",
        description="Replace paseo's literal auto-titles of agent tabs with a short topic title.",
    )
    parser.add_argument("--dry-run", action="store_true", help="show what it would rename; change nothing")
    args = parser.parse_args(argv)
    for agent in live_agents():
        path = agents.transcript_path(agent)
        if not path:
            continue
        turns = head_turns(path)
        if sum(1 for role, _ in turns if role == "user") < MIN_USER_TURNS:
            continue
        if not is_auto_title(agent["title"], opening(turns)):
            continue
        repo = Path(agent.get("cwd") or "").name
        raw = ask(PROMPT.format(limit=LIMIT, repo=repo, body=context(turns)))
        if raw is None:
            # Already logged. Not another call per tab: a hung claude costs TIMEOUT seconds each.
            break
        current = " ".join(agent["title"].split())
        new = clean(raw)
        short_id = agent["id"][:8]
        if not new or new == current:
            log(f"skipped {short_id} {current[:40]!r}: no usable new title")
        elif rename(agent["id"], new, args.dry_run):
            log(f"{'would rename' if args.dry_run else 'renamed'} {short_id}  {current[:40]!r} -> {new!r}")
    return 0
