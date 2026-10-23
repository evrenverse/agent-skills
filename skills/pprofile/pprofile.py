#!/usr/bin/env python3
"""Profile a paseo agent's run from its transcript: time, tokens, tools, waiting, polling.

Reads the Claude Code transcript of the agent (main session plus every subagent under
<session>/subagents/agent-*.jsonl) and prints what a completion report has to carry as measured
numbers instead of estimates: wall time, gaps over five minutes, API calls, output / cache-create /
cache-read tokens, context maximum, tool counts and tool wait per tool, the slowest bash commands,
every Agent dispatch with model and duration, identical tool calls from the third repetition on
(polling), identical assistant texts, and whole-file reads of files over 100 KB.

    pprofile <agent-id | id-prefix | title-substring | transcript.jsonl>

Locating the agent and its transcript is paseo_tools.agents; this script only measures.
"""

import collections
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# The plugin cache holds the whole repository, so lib/ sits next to skills/. resolve() follows symlinks.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from paseo_tools import agents, limits

BIG_FILE = 100 * 1024
GAP_SECONDS = 300
REPEAT_FROM = 3


def ts(value: str) -> datetime:
    """Parse an ISO timestamp as Claude Code writes it, with a trailing Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fmt_tok(n: float) -> str:
    """A token count as 1.23M or 456k."""
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.0f}k"


def load(path: Path) -> list[dict]:
    """The timestamped user and assistant records of a Claude Code transcript, unreadable lines skipped."""
    out = []
    # Replace, never raise: a running agent's transcript can end inside a character.
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("type") in ("assistant", "user") and "timestamp" in rec:
                out.append(rec)
    return out


def _is_big(path: str) -> bool:
    try:
        return os.path.getsize(path) > BIG_FILE
    except OSError:
        return False


_BIG_READ_CMD = re.compile(r"\b(cat|sed -n '1,|head -n ?[0-9]{4,})\b")


def _whole_file_read(name: str, inp: dict, cwd: str) -> str | None:
    """A Read without limit, or a cat/sed-from-line-1 in bash, on a file over 100 KB."""
    if name == "Read":
        path = inp.get("file_path") or ""
        if not inp.get("limit") and _is_big(path):
            return path
        return None
    if name == "Bash":
        cmd = inp.get("command") or ""
        if not _BIG_READ_CMD.search(cmd):
            return None
        for token in re.findall(r"[\w./()\[\]-]+\.md\b|[\w./()\[\]-]+\.jsonl?\b", cmd):
            candidate = token if os.path.isabs(token) else os.path.join(cwd, token)
            if _is_big(candidate):
                return token
    return None


def analyze(path: Path, label: str) -> dict | None:
    """Measure one transcript (time, tokens, tools, polling, big reads); None when it has no records."""
    recs = load(path)
    if not recs:
        return None
    result: dict = {"label": label, "path": str(path)}
    t0, t1 = ts(recs[0]["timestamp"]), ts(recs[-1]["timestamp"])
    result["start"], result["end"] = t0, t1
    result["wall_min"] = (t1 - t0).total_seconds() / 60
    usage: collections.Counter = collections.Counter()
    calls = 0
    ctx_sizes: list[int] = []
    tool_uses: dict[str, tuple[str, dict, datetime]] = {}
    tool_counts: collections.Counter = collections.Counter()
    tool_time: collections.Counter = collections.Counter()
    bash_cmds: list[tuple[float, str]] = []
    agents: list[dict] = []
    repeats: collections.Counter = collections.Counter()
    texts: collections.Counter = collections.Counter()
    gaps: list[tuple[datetime, float]] = []
    big_reads: list[str] = []
    thinking = 0
    limit_hits = 0
    prev: datetime | None = None
    cwd = recs[0].get("cwd") or os.getcwd()
    for rec in recs:
        t = ts(rec["timestamp"])
        if prev is not None and (t - prev).total_seconds() > GAP_SECONDS:
            gaps.append((prev, (t - prev).total_seconds()))
        prev = t
        message = rec.get("message") or {}
        content = message.get("content")
        if rec["type"] == "assistant":
            u = message.get("usage") or {}
            if u:
                calls += 1
                for key in (
                    "input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                    "output_tokens",
                ):
                    usage[key] += u.get(key) or 0
                thinking += (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0
                ctx_sizes.append(
                    (u.get("input_tokens") or 0)
                    + (u.get("cache_creation_input_tokens") or 0)
                    + (u.get("cache_read_input_tokens") or 0)
                )
            if isinstance(content, list):
                for block in content:
                    kind = block.get("type")
                    if kind == "tool_use":
                        inp = block.get("input") or {}
                        repeats[(block["name"], json.dumps(inp, sort_keys=True)[:400])] += 1
                        tool_uses[block["id"]] = (block["name"], inp, t)
                        tool_counts[block["name"]] += 1
                        big = _whole_file_read(block["name"], inp, cwd)
                        if big:
                            big_reads.append(big)
                    elif kind == "text":
                        text = block.get("text", "")
                        if limits.classify(text):
                            limit_hits += 1
                        if len(text) > 40:
                            texts[text[:200]] += 1
        elif isinstance(content, list):
            for block in content:
                if block.get("type") != "tool_result":
                    continue
                use = tool_uses.get(block.get("tool_use_id"))
                if not use:
                    continue
                name, inp, t_use = use
                duration = (t - t_use).total_seconds()
                tool_time[name] += duration
                if name == "Bash":
                    bash_cmds.append((duration, (inp.get("command") or "").replace("\n", " ")[:140]))
                elif name == "Agent":
                    agents.append(
                        {
                            "dur": duration,
                            "desc": inp.get("description"),
                            "model": inp.get("model"),
                            "background": bool(inp.get("run_in_background", True)),
                        }
                    )
    result.update(
        calls=calls,
        usage=dict(usage),
        thinking=thinking,
        limit_hits=limit_hits,
        ctx_avg=sum(ctx_sizes) / len(ctx_sizes) if ctx_sizes else 0,
        ctx_max=max(ctx_sizes) if ctx_sizes else 0,
        tool_counts=tool_counts,
        tool_time=tool_time,
        bash_top=sorted(bash_cmds, reverse=True)[:10],
        bash_total=sum(d for d, _ in bash_cmds),
        agents=agents,
        repeats=[(k, n) for k, n in repeats.items() if n >= REPEAT_FROM],
        polled=sum(n - (REPEAT_FROM - 1) for n in repeats.values() if n >= REPEAT_FROM),
        text_repeats=[(k, n) for k, n in texts.items() if n >= REPEAT_FROM],
        gaps=gaps,
        gap_min=sum(g for _, g in gaps) / 60,
        big_reads=big_reads,
    )
    return result


def show(r: dict) -> None:
    """Print the block of one analyzed transcript."""
    u = r["usage"]
    print(f"\n### {r['label']}")
    print(
        f"  wall {r['wall_min']:.0f} min ({r['start'].strftime('%Y-%m-%d %H:%M')} → "
        f"{r['end'].strftime('%Y-%m-%d %H:%M')} UTC)  gaps>5min {r['gap_min']:.0f} min  "
        f"limit hits {r['limit_hits']}  api-calls {r['calls']}"
    )
    print(
        f"  tokens: out {fmt_tok(u.get('output_tokens', 0))} (thinking {fmt_tok(r['thinking'])})  "
        f"cache_create {fmt_tok(u.get('cache_creation_input_tokens', 0))}  "
        f"cache_read {fmt_tok(u.get('cache_read_input_tokens', 0))}  "
        f"uncached_in {fmt_tok(u.get('input_tokens', 0))}  "
        f"| ctx avg {fmt_tok(r['ctx_avg'])} max {fmt_tok(r['ctx_max'])}"
    )
    print("  tools: " + ", ".join(f"{k}:{v}" for k, v in r["tool_counts"].most_common(8)))
    print(
        "  tool wait: "
        + ", ".join(f"{k}:{v / 60:.0f}m" for k, v in r["tool_time"].most_common(6))
        + f"   (bash total {r['bash_total'] / 60:.0f} min)"
    )
    print("  slowest bash:")
    for d, c in r["bash_top"][:8]:
        print(f"    {d / 60:6.1f} min  {c}")
    if r["agents"]:
        background = sum(1 for a in r["agents"] if a["background"])
        print(
            f"  Agent dispatches: {len(r['agents'])}  in background: {background}  "
            f"total wait {sum(a['dur'] for a in r['agents']) / 60:.0f} min"
        )
        for a in sorted(r["agents"], key=lambda a: -a["dur"])[:12]:
            print(f"    {a['dur'] / 60:6.1f} min  model={a['model']}  {a['desc']}")
    if r["repeats"]:
        print(f"  polled calls (identical from the {REPEAT_FROM}rd repetition on): {r['polled']}")
        for (name, key), n in sorted(r["repeats"], key=lambda x: -x[1])[:10]:
            print(f"    {n}x {name} {key[:110]}")
    if r["text_repeats"]:
        print("  repeated identical assistant text:")
        for k, n in sorted(r["text_repeats"], key=lambda x: -x[1])[:6]:
            print(f"    {n}x {k[:110]!r}")
    if r["big_reads"]:
        print(f"  whole-file reads of files over 100 KB: {len(r['big_reads'])}")
        for name, n in collections.Counter(r["big_reads"]).most_common(6):
            print(f"    {n}x {name}")
    if r["gaps"]:
        print("  gaps >5min:")
        for t, g in r["gaps"][:8]:
            print(f"    {t.strftime('%Y-%m-%d %H:%M')} UTC  {g / 60:.0f} min")


def report_rows(lead: dict, subs: list[dict], tot: collections.Counter) -> None:
    """Print the rows the /phandoff completion report asks for, ready to paste; `tot` sums the subagents."""
    implementing = sum(
        1
        for s in subs
        if re.search(r"implement|fix|finish|task", s["meta_desc"], re.I)
        and not re.search(r"review", s["meta_desc"], re.I)
    )
    reviewing = sum(1 for s in subs if re.search(r"review", s["meta_desc"], re.I))
    other = len(subs) - implementing - reviewing
    out_all = lead["usage"].get("output_tokens", 0) + tot["output_tokens"]
    read_all = lead["usage"].get("cache_read_input_tokens", 0) + tot["cache_read_input_tokens"]
    rows = {
        "Duration (wall-clock)": f"{lead['wall_min'] / 60:.1f} h",
        "Gaps > 5 min": f"{lead['gap_min']:.0f} min in {len(lead['gaps'])} gap(s)",
        "API calls (lead)": lead["calls"],
        "Cache read (lead)": fmt_tok(lead["usage"].get("cache_read_input_tokens", 0)),
        "Subagents": f"{len(subs)} ({implementing} implementing, {reviewing} reviewing, {other} other)",
        "Polled calls": lead["polled"],
        "Whole-file reads of large files": len(lead["big_reads"]) + sum(len(s["big_reads"]) for s in subs),
        "Tokens total": f"{fmt_tok(out_all)} output, {fmt_tok(read_all)} cache read",
    }
    print("\n## Completion-report rows (measured)")
    for label, value in rows.items():
        print(f"| {label} | {value} |")


def resolve(query: str) -> Path:
    """The transcript for a .jsonl path or an agent id, id prefix or title substring; exits on no or many."""
    candidate = Path(query).expanduser()
    if candidate.suffix == ".jsonl" and candidate.exists():
        return candidate
    matches = agents.load_agents(query)
    if len(matches) != 1:
        names = "\n".join(f"  {a.get('id', '?')}  {a.get('title', '')}" for a in matches[:10])
        sys.exit(f"{len(matches)} agents match {query!r}; give an id or a transcript path.\n{names}")
    path = agents.transcript_path(matches[0])
    if path is None:
        sys.exit(f"no transcript found for {query!r}")
    return path


def main() -> int:
    """Run the command line: 2 for a usage error, a message and exit when nothing can be profiled."""
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    path = resolve(sys.argv[1])
    lead = analyze(path, path.name)
    if lead is None:
        sys.exit(f"empty transcript: {path}")
    show(lead)
    subs: list[dict] = []
    for sub_path in sorted((path.parent / path.stem / "subagents").glob("agent-*.jsonl")):
        meta_path = sub_path.with_suffix(".meta.json")
        desc = ""
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                desc = meta.get("description") or meta.get("agentType") or ""
            except ValueError:
                desc = ""
        sub = analyze(sub_path, sub_path.name)
        if sub:
            sub["meta_desc"] = desc
            subs.append(sub)
    tot = sum((collections.Counter(s["usage"]) for s in subs), collections.Counter())
    if subs:
        print(
            f"\n== {len(subs)} subagents: out {fmt_tok(tot['output_tokens'])}  "
            f"cache_create {fmt_tok(tot['cache_creation_input_tokens'])}  "
            f"cache_read {fmt_tok(tot['cache_read_input_tokens'])}  "
            f"wall-sum {sum(s['wall_min'] for s in subs):.0f} min"
        )
        for s in sorted(subs, key=lambda s: s["start"]):
            u = s["usage"]
            print(
                f"  {s['start'].strftime('%H:%M')} {s['wall_min']:5.0f}min calls={s['calls']:3d} "
                f"out={fmt_tok(u.get('output_tokens', 0)):>5} "
                f"cread={fmt_tok(u.get('cache_read_input_tokens', 0)):>6} "
                f"ctxmax={fmt_tok(s['ctx_max']):>5} bash={s['bash_total'] / 60:4.0f}m "
                f"polled={s['polled']:3d}  {s['meta_desc'][:60]}"
            )
        slow = sorted(((d, c) for s in subs for d, c in s["bash_top"]), reverse=True)[:8]
        print("  slowest bash in subagents:")
        for d, c in slow:
            print(f"    {d / 60:6.1f} min  {c[:120]}")
    report_rows(lead, subs, tot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
