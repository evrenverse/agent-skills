"""Paseo agent records and the transcripts behind them, whichever harness wrote them.

paseo stores one metadata file per agent under ~/.paseo/agents/<workspace>/<id>.json. The conversation
itself lives with the harness, keyed by the `persistence.sessionId` from that file: Claude Code writes
<config>/projects/<escaped-cwd>/<session>.jsonl, and because claude-rotate juggles several config
directories it can sit under any of them; Codex writes
~/.codex/sessions/<y>/<m>/<d>/rollout-<stamp>-<session>.jsonl; Antigravity writes
~/.gemini/antigravity-cli/brain/<conversation>/.system_generated/logs/transcript_full.jsonl. Reading only
the Claude roots once made every Codex agent look like it had no history at all.
"""

from __future__ import annotations

import glob
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from paseo_tools import cli

# Recorded as "user" but never typed by one: harness preamble, an injected skill body, an interruption notice.
# `<command-message>` is NOT here: that one IS the user's request.
PREAMBLE = (
    "<recommended_plugins",
    "<user_instructions",
    "<environment_context",
    "<system-reminder",
    "<local-command-stdout",
    "Base directory for this skill:",
    "[Request interrupted",
    "Caveat: The messages below",
    "# AGENTS.md instructions for",  # Codex injects the repo's AGENTS.md this way
)
# Claude Code transcript records, unescaped (so a transcript QUOTING them, escaped, never matches).
# A task starts with a backgrounded Bash call, an async subagent, or a subagent resumed by SendMessage; it
# ends with its <task-notification> or a TaskStop (which writes no notification).
TASK_RE = re.compile(
    r'"backgroundTaskId":"(?P<bash>[\w-]+)"'
    r'|"status":"async_launched","agentId":"(?P<launched>[\w-]+)"'
    r'|"resumedAgentId":"(?P<resumed>[\w-]+)"'
    r"|<task-id>(?P<notified>[\w-]+)</task-id>"
    r'|"name":"TaskStop","input":\{"task_id":"(?P<stopped>[\w-]+)"'
)

ID_PREFIX = re.compile(r"[0-9a-fA-F-]{8,}")


@dataclass(frozen=True)
class Message:
    """The last text of a transcript and when it was written (None when the source has no timestamps)."""

    text: str
    written_at: datetime | None


def _agent_root() -> Path:
    return Path.home() / ".paseo" / "agents"


def _read(path: Path) -> dict | None:
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    rec["_workspace"] = path.parent.name
    return rec


def load_agents(query: str | None = None) -> list[dict]:
    """Agent records matching `query` (id prefix or substring of the title), each with its `_workspace`."""
    root = _agent_root()
    # An id is in the filename, so skip reading 1000+ files for the common case. Only for what looks like an
    # id: a title such as "[Advisor]" is a glob pattern that matches other ids.
    if query and ID_PREFIX.fullmatch(query):
        records = [rec for path in root.glob(f"*/{glob.escape(query)}*.json") if (rec := _read(path))]
        if records:
            return records
    needle = (query or "").lower()
    found = []
    for path in root.glob("*/*.json"):
        rec = _read(path)
        if not rec:
            continue
        if needle and needle not in path.stem.lower() and needle not in (rec.get("title") or "").lower():
            continue
        found.append(rec)
    return found


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:  # deleted between the glob and the stat
        return 0.0


def _session_id(agent: dict) -> str | None:
    persistence = agent.get("persistence") or {}
    # Plugin providers (Antigravity) wrap their own id: sessionId is `plugin:{json}`.
    plugin = ((persistence.get("metadata") or {}).get("pluginProviderPersistence") or {}).get("data") or {}
    return plugin.get("conversationId") or persistence.get("sessionId")


def _claude_transcript(session: str) -> Path | None:
    home = Path.home()
    # The plain config dir, every claude-rotate account, and whatever CLAUDE_CONFIG_DIR points at.
    roots = [home / ".claude" / "projects", *home.glob(".config/claude-rotate/configs/*/projects")]
    if config := os.environ.get("CLAUDE_CONFIG_DIR"):
        roots.append(Path(config) / "projects")
    # claude-rotate symlinks every account's projects/ back to ~/.claude/projects, so without this the same
    # thousands-of-dirs tree is globbed once per account: millions of lstat calls, 100% CPU for a pass.
    # Resolve to the real directory and keep each once; genuinely separate roots still stay distinct.
    roots = list({root.resolve(): None for root in roots})
    hits = [path for root in roots for path in root.glob(f"*/{session}.jsonl")]
    return max(hits, key=_mtime, default=None)


def transcript_path(agent: dict) -> Path | None:
    """The transcript file of an agent record from Claude Code, Codex or Antigravity, or None.

    When several exist (a session resumed under another claude-rotate account), the newest wins.
    """
    session = _session_id(agent)
    if not session:
        return None
    if found := _claude_transcript(session):
        return found
    home = Path.home()
    brains = home / ".gemini"
    for hits in (
        # Archiving a Codex agent moves its rollout out of the dated tree into a flat folder.
        [
            *(home / ".codex" / "sessions").glob(f"*/*/*/rollout-*-{session}.jsonl"),
            *(home / ".codex" / "archived_sessions").glob(f"rollout-*-{session}.jsonl"),
        ],
        # antigravity-cli/ today, antigravity-acp/ for the first agents. The _full file first:
        # transcript.jsonl cuts long fields.
        [*brains.glob(f"antigravity*/brain/{session}/.system_generated/logs/transcript_full.jsonl")],
        [*brains.glob(f"antigravity*/brain/{session}/.system_generated/logs/transcript.jsonl")],
    ):
        if hits:
            return max(hits, key=_mtime)
    return None


def when(value: str | None) -> str:
    """An ISO timestamp as local `YYYY-MM-DD HH:MM`; "?" when there is none, the input when unreadable."""
    if not value:
        return "?"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _render(blocks: Iterable[Any]) -> tuple[list[str], bool]:
    """Rendered lines for a content list, plus whether any of it is human prose.

    Claude and Codex name the same three things differently (prose, a tool call, a tool result), so one
    renderer covers both and the callers stay short.
    """
    parts, spoken = [], False
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
            spoken = True
            continue
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind in ("text", "input_text", "output_text"):
            parts.append(block.get("text", ""))
            spoken = True
        elif kind == "tool_use":
            parts.append(
                f"⚙ {block.get('name')} {json.dumps(block.get('input', {}), ensure_ascii=False)[:160]}"
            )
        elif kind == "tool_result":
            body = block.get("content")
            parts.append(
                f"← {(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))[:160]}"
            )
    return [x for x in parts if x and x.strip()], spoken


def _claude_turn(rec: dict) -> tuple[str, str, str] | None:
    if rec.get("type") not in ("user", "assistant"):
        return None
    content = (rec.get("message") or {}).get("content")
    parts, spoken = _render([content] if isinstance(content, str) else content or [])
    if not parts:
        return None
    # A "user" record carrying only tool results is the harness talking, not the user.
    role = rec["type"] if spoken or rec["type"] == "assistant" else "tool"
    return when(rec.get("timestamp")), role, "\n".join(parts)


def _codex_turn(rec: dict) -> tuple[str, str, str] | None:
    p = rec.get("payload")
    if not isinstance(p, dict):
        return None
    stamp, kind = when(rec.get("timestamp")), p.get("type")
    if kind in ("message", "agent_message"):
        parts, _ = _render(p.get("content") or [])
        role = "user" if p.get("role") == "user" else "assistant"
        return (stamp, role, "\n".join(parts)) if parts else None
    if kind in ("function_call", "custom_tool_call"):
        arg = p.get("arguments") or p.get("input") or ""
        return stamp, "tool", f"⚙ {p.get('name')} {str(arg)[:160]}"
    if kind in ("function_call_output", "custom_tool_call_output"):
        out = p.get("output")
        if not isinstance(out, str):
            out = "\n".join(_render(out or [])[0])
        return stamp, "tool", f"← {out[:160]}"
    return None


def _antigravity_turn(rec: dict) -> tuple[str, str, str] | None:
    stamp, kind, text = when(rec.get("created_at")), rec.get("type"), rec.get("content") or ""
    if kind == "USER_INPUT":
        # What was typed sits inside <USER_REQUEST>; the rest is harness metadata.
        typed = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", text, re.S)
        return stamp, "user", (typed.group(1) if typed else text).strip()
    if kind == "PLANNER_RESPONSE":
        calls = [
            f"⚙ {c.get('name')} {json.dumps(c.get('args', {}), ensure_ascii=False)[:160]}"
            for c in rec.get("tool_calls") or []
        ]
        parts = [x for x in (text, *calls) if x.strip()]
        return (stamp, "assistant" if text.strip() else "tool", "\n".join(parts)) if parts else None
    if kind in ("GENERIC", "ERROR_MESSAGE"):
        # Tool output; every one opens with a "Created At / Completed At" header.
        body = re.sub(r"\A(?:(?:Created|Completed) At: .*\n)+\n?", "", text)
        return (stamp, "tool", f"← {body[:160]}") if body.strip() else None
    return None  # SYSTEM_MESSAGE, CHECKPOINT: task notices and compaction summaries


def turn(rec: dict) -> tuple[str, str, str] | None:
    """One transcript record as (timestamp, role, text), whichever harness wrote it."""
    if "payload" in rec:
        return _codex_turn(rec)
    if "step_index" in rec:
        return _antigravity_turn(rec)
    return _claude_turn(rec)


def turns(path: Path) -> list[tuple[str, str, str]]:
    """(timestamp, role, text) per turn, tool noise collapsed to one line.

    The role is "user" only for what a human actually typed (tool results and tool calls are "tool"), so a
    reader can show the instructions without the machinery wedged between them.
    """
    out = []
    # Fixed UTF-8: cron may run with a non-UTF-8 locale.
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and (found := turn(rec)):
            out.append(found)
    return out


def tail_lines(path: Path, max_bytes: int = 1_000_000) -> list[str]:
    """The complete lines within the last `max_bytes` bytes of `path`.

    Transcripts run to tens of MB and the line wanted is at the end, so only the tail is read. Raises OSError
    when the file cannot be read.
    """
    with path.open("rb") as f:
        start = max(0, f.seek(0, os.SEEK_END) - max_bytes)
        f.seek(start)
        data = f.read(max_bytes)
    # Split on "\n" only: str.splitlines() also breaks at U+2028, which JSON text may carry unescaped.
    lines = data.decode("utf-8", "replace").split("\n")
    if start:
        lines = lines[1:]  # the window began inside this line
    return [line for line in lines if line]


def _written_at(value: Any) -> datetime | None:
    try:
        written = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return written if written.tzinfo else None


def last_message(lines: list[str]) -> Message | None:
    """The last text a person or the model wrote in the main chain of a Claude Code transcript, or None.

    What `paseo logs --tail 1 --filter text` shows, and where Claude Code writes its limit messages. Tool
    results, thinking, subagent (sidechain) and harness-injected (isMeta) records are skipped, and so are
    background-task notifications: Claude Code queues them as user records even while the agent sits on its
    limit, and nobody wrote them.
    """
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("type") not in ("user", "assistant"):
            continue
        if rec.get("isSidechain") or rec.get("isMeta"):
            continue
        if (rec.get("origin") or {}).get("kind") == "task-notification":
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        else:
            blocks = content if isinstance(content, list) else []
            text = "\n".join(
                b.get("text") or "" for b in blocks if isinstance(b, dict) and b.get("type") == "text"
            )
        if text:
            return Message(text, _written_at(rec.get("timestamp")))
    return None


def last_message_of(agent: dict) -> Message | None:
    """The last message of an agent: its Claude Code transcript, else `paseo logs` (without a timestamp).

    `agent` is a record from `load_agents` or an entry of `paseo ls --json`. Reading the transcript tail
    spares the daemon: for `paseo logs` it sends the whole timeline and reloads a closed agent just to be
    read.
    """
    if "persistence" not in agent:
        # A `paseo ls` entry names no session; the record on disk does.
        paths = _agent_root().glob(f"*/{agent['id']}.json")
        agent = next(filter(None, map(_read, paths)), agent)
    session = _session_id(agent)
    path = _claude_transcript(session) if session else None
    if path:
        try:
            return last_message(tail_lines(path))
        except OSError:
            pass  # gone between lookup and read: ask paseo instead
    proc = cli.paseo("logs", agent["id"], "--tail", "1", "--filter", "text")
    text = proc.stdout.strip() if proc.returncode == 0 else ""
    return Message(text, None) if text else None


def _is_main_chain_answer(line: str) -> bool:
    try:
        rec = json.loads(line)
    except ValueError:
        return False
    return isinstance(rec, dict) and rec.get("type") == "assistant" and not rec.get("isSidechain")


def pending_tasks(lines: list[str] | None) -> set[str]:
    """Background tasks the agent started whose result it has not yet answered.

    Empty for an agent without a Claude Code transcript (None, e.g. Codex): its idle is final. A task is
    still out after its `<task-notification>` (or a TaskStop) until an assistant record in the main chain
    follows: `paseo wait` reports idle in the gap before Claude Code starts the turn that answers the
    notification, and the agent is not done then.
    """
    # In order, not as sets: a subagent that reported once can be resumed and be out again.
    started: set[str] = set()  # every task seen starting in these lines
    out: set[str] = set()
    unanswered: set[str] = set()  # ended, but no turn has answered yet
    for line in lines or ():
        if unanswered and _is_main_chain_answer(line):
            unanswered.clear()
        for m in TASK_RE.finditer(line):
            begun = m["bash"] or m["launched"] or m["resumed"]
            if begun:
                started.add(begun)
                out.add(begun)
                continue
            ended = m["notified"] or m["stopped"]
            # Only a task seen starting: a transcript that merely mentions `<task-id>` starts nothing.
            if ended in started:
                out.discard(ended)
                unanswered.add(ended)
    return out | unanswered
