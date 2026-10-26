"""Helpers shared by the tests: a temporary HOME, JSON fixtures and a fake for `cli.run`."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


class TempHome(unittest.TestCase):
    """Base class that points HOME at a temporary directory (`self.home`).

    Also clears XDG_STATE_HOME, XDG_CONFIG_HOME and CLAUDE_CONFIG_DIR: each would lead a test to the real
    state, configuration or Claude config directory.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"HOME": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        # patch.dict puts them back on stop.
        os.environ.pop("XDG_STATE_HOME", None)
        os.environ.pop("XDG_CONFIG_HOME", None)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)


def write_json(path: Path, data: Any) -> None:
    """Write `data` as JSON to `path`, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def run_script(
    script: Path, *args: str, home: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a Python script as a subprocess with HOME set to `home` and no Claude config directory.

    For the skill scripts, whose command line is the interface. The environment is built here instead of
    inherited, so a test cannot reach the real home through CLAUDE_CONFIG_DIR or XDG_STATE_HOME; `extra_env`
    adds variables on top.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "XDG_STATE_HOME")}
    env.update({"HOME": str(home), **(extra_env or {})})
    return subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True, check=False, env=env
    )


class FakeRun:
    """Stand-in for `cli.run` that answers from a table and records the calls.

    `responses` maps argv without the binary (argv[1:]) to a result, or to a list of results that answer its
    calls in order. The longest key that is a prefix of the call wins; an unmatched call returns exit 1 with
    an explanation instead of raising.
    """

    def __init__(self, responses: dict[tuple[str, ...], Any]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []  # full argv of each call
        self.kwargs: list[dict[str, Any]] = []  # keyword arguments of each call

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        self.kwargs.append(kwargs)
        rest = tuple(argv[1:])
        for key in sorted(self.responses, key=len, reverse=True):
            if rest[: len(key)] == key:
                reply = self.responses[key]
                return reply.pop(0) if isinstance(reply, list) else reply
        return subprocess.CompletedProcess(argv, 1, "", f"FakeRun: no response for {rest!r}")
