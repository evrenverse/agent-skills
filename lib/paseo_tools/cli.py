"""Safe subprocess runner and binary lookup for tools that run under cron with a bare PATH."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

_VERSION_DIR = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve(binary: str) -> str:
    """Return the path of `binary`.

    Looks on PATH, then in ~/.local/bin, then in the newest nvm Node, and falls back to the bare name.
    """
    found = shutil.which(binary)
    if found:
        return found
    home = Path.home()
    local = home / ".local" / "bin" / binary
    if _executable(local):
        return str(local)
    # Compare version numbers, not names or mtimes: "v9.0.0" sorts after "v20.1.0" as text.
    versions = []
    for candidate in (home / ".nvm" / "versions" / "node").glob(f"v*/bin/{binary}"):
        match = _VERSION_DIR.fullmatch(candidate.parent.parent.name)
        if match and _executable(candidate):
            versions.append((tuple(map(int, match.groups())), candidate))
    if versions:
        return str(max(versions)[1])
    return binary


def run(
    argv: list[str],
    *,
    timeout: float = 60,
    cwd: str | os.PathLike[str] | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `argv` in text mode with captured output and never raise.

    A missing binary gives returncode 127, a timeout 124 and any other failure to start (for example a file
    that is not executable) 126; the reason is in `stderr`. Undecodable output is replaced, not raised.
    Without `input`, stdin is closed so the child never waits for a terminal.
    """
    try:
        # Fixed UTF-8 with replacement: cron may run with a non-UTF-8 locale, and strict decoding raises.
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            input=input,
            stdin=subprocess.DEVNULL if input is None else None,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", f"timed out after {timeout}s: {argv[0]}")
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 126, "", str(exc))


def paseo(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    """Run the paseo CLI with `args`."""
    return run([resolve("paseo"), *args], timeout=timeout)


def json_out(proc: subprocess.CompletedProcess[str]) -> Any | None:
    """Return the parsed stdout of `proc`, or None if it failed or did not print JSON."""
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None
