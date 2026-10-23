#!/usr/bin/env python3
"""Locate a paseo agent and read its transcript, whichever harness wrote it.

    pagent [query] [--task] [-t] [-n N] [--full]

The lookup and the transcript parsing (Claude Code, Codex, Antigravity) are paseo_tools.agents; this script
is the command line and the rendering. Without a query it lists every agent, newest first.
"""

import argparse
import sys
from pathlib import Path

# The plugin cache holds the whole repository, so lib/ sits next to skills/. resolve() follows symlinks.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from paseo_tools import agents


def render_head(agent: dict) -> None:
    """Print the id, title, status, working directory, model, times and transcript path of an agent."""
    print(f"id        {agent['id']}")
    print(f"title     {agent.get('title') or '—'}")
    print(
        f"status    {agent.get('lastStatus')}"
        + ("  ⚠ needs attention" if agent.get("requiresAttention") else "")
    )
    print(f"cwd       {agent.get('cwd')}")
    cfg = agent.get("config") or {}
    print(f"model     {cfg.get('model')} · {cfg.get('thinkingOptionId')} · {cfg.get('modeId')}")
    created, active = agents.when(agent.get("createdAt")), agents.when(agent.get("lastActivityAt"))
    print(f"created   {created}   last activity {active}")
    print(f"transcript {agents.transcript_path(agent) or '— not found —'}")


def show(selected: list[tuple[str, str, str]], limit: int) -> None:
    """Print turns as a bold header (role, time) and the text cut to `limit` characters."""
    for stamp, role, text in selected:
        label = {"user": "▶ user", "assistant": "● agent"}.get(role, "⚙ tool")
        print(f"\n\033[1m{label}  {stamp}\033[0m")
        print(text[:limit] + ("…" if len(text) > limit else ""))


def main() -> int:
    """Run the command line; the exit code is 1 for no match or no transcript, 0 otherwise."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("query", nargs="?", help="agent id (or prefix) or part of its title")
    ap.add_argument("-t", "--transcript", action="store_true", help="print the conversation")
    ap.add_argument("-n", type=int, default=12, help="how many turns from the end (default 12)")
    ap.add_argument("--full", action="store_true", help="do not truncate turns")
    ap.add_argument(
        "--task", action="store_true", help="print every instruction the user gave, corrections included"
    )
    args = ap.parse_args()

    matches = agents.load_agents(args.query)
    if not matches:
        print(f"no paseo agent matches {args.query!r}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        matches.sort(key=lambda a: a.get("lastActivityAt") or "", reverse=True)
        print(f"{len(matches)} matches, newest first:\n")
        for a in matches[:20]:
            print(
                f"  {a['id']}  {agents.when(a.get('lastActivityAt')):<16} {a.get('lastStatus') or '?':<10} "
                f"{(a.get('title') or '—')[:60]}"
            )
        return 0

    agent = matches[0]
    render_head(agent)
    if not (args.transcript or args.task):
        return 0
    path = agents.transcript_path(agent)
    if not path:
        sid = (agent.get("persistence") or {}).get("sessionId")
        # Never return 0 here: an empty read and a missing file look identical to
        # the caller, and "the agent said nothing" is the wrong thing to conclude.
        print(
            f"no transcript on disk for session {sid!r} ({agent.get('provider') or 'claude'})",
            file=sys.stderr,
        )
        return 1

    conversation = agents.turns(path)
    limit = 10**9 if args.full else 1200
    if args.task:
        # Every user turn, not just the first. A later correction ("use option B, but
        # keep the retry limit at three") IS the assignment; taking over on the opening line
        # alone re-opens decisions the user already made.
        asked = [t for t in conversation if t[1] == "user" and not t[2].lstrip().startswith(agents.PREAMBLE)]
        print(f"\n\033[1m═══ what the user asked for ({len(asked)}) ═══\033[0m")
        show(asked, limit)
    if args.transcript:
        print(f"\n\033[1m═══ last {args.n} turns ═══\033[0m")
        show(conversation[-args.n :], limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
