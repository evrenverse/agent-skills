# AGENTS.md

Rules for agents working in this repository: the `team-skills` plugin for Claude Code and Codex (marketplace
`evrenverse`).

## Layout

```text
.claude-plugin/          Claude Code plugin and marketplace manifests
.codex-plugin/           Codex plugin manifest
.agents/plugins/         Codex marketplace catalog
.github/                 CI workflow (lint, tests, plugin validation, secret scan) and Dependabot config
bin/                     paseo-limit-watch, paseo-notify, paseo-title, test-queue: extension-less entry points that
                         put lib/ on sys.path and call the module's main()
docs/plans/              implementation plans, one file per change
hooks/                   hooks.json: the Claude Code hook that sends heavy Bash commands through test-queue
lib/paseo_tools/         the shared library (standard library only)
  agents.py              agent records, transcript lookup and reading (Claude Code, Codex, Antigravity)
  cli.py                 running paseo, claude-rotate and claude; resolving binaries under cron
  limits.py              recognizing limit and transient-error messages, parsing reset times
  limit_watch.py         paseo-limit-watch
  notify.py              paseo-notify
  test_queue.py          test-queue: the queue, its status and its hook
  title.py               paseo-title
skills/<name>/SKILL.md   the nine skills; pagent and pprofile carry a script, phandoff its templates
tests/                   unittest suite; support.py (TempHome), synthetic fixtures
install.sh               links bin/ into ~/.local/bin
README.md  SECURITY.md  CONTRIBUTING.md  LICENSE  icon.svg  ruff.toml
```

## Rules

The standards, checks and pull-request flow in [CONTRIBUTING.md](CONTRIBUTING.md) are binding: read them before you
change anything. Its implementer section replaces the parts of a `phandoff` kickoff that do not fit this repository.
The three rules an agent breaks most easily, stated here as well:

- Never push to `main` and never merge into it: work on a branch and open a pull request.
- Never start, stop or restart the paseo daemon: not `paseo restart`, not `paseo daemon start|stop|restart`, not
  `systemctl --user restart paseo`.
- Tests never touch the real home directory: use `tests.support.TempHome`.
