# Contributing

Changes reach `main` through pull requests only. The maintainer reviews and merges them; nobody pushes to `main`
directly. Agents working in this repository also follow [AGENTS.md](AGENTS.md).

## Setup

You need Python 3.10 or newer, [ruff](https://docs.astral.sh/ruff/), [shellcheck](https://www.shellcheck.net/) and the
Claude Code CLI (`claude`) for the plugin validation. The code itself uses only the standard library, so there is
nothing to install for it.

```bash
git clone https://github.com/evrenverse/agent-skills.git
cd agent-skills
python3 -m unittest discover -s tests -t .
```

`./install.sh` links the four commands in `bin/` into `~/.local/bin`. It replaces symlinks of the same name, so links
that point into another clone then point into this one.

## Checks

These are the definition of green. All must pass before you open a pull request:

```bash
python3 -m unittest discover -s tests -t .
ruff check .
ruff format --check .
shellcheck install.sh
claude plugin validate .
for f in $(git ls-files '*.json'); do python3 -m json.tool "$f" > /dev/null || echo "invalid: $f"; done
```

The suite takes about five seconds. It runs on synthetic fixtures and points `HOME` at a temporary directory, so it
never touches your agents, transcripts or state. `claude plugin validate .` must report no errors; its warnings
about the missing `version` are expected (see the standards below). A pull request runs the same checks in CI
(`.github/workflows/ci.yml`), with the suite on Python 3.10 and on the newest stable release, and adds a
[gitleaks](https://github.com/gitleaks/gitleaks) scan of the full history for secrets. Dependabot keeps the commit-SHA
pins of the workflow's actions current.

## Trying a change locally

**A skill.** Start Claude Code with your checkout as a session-only plugin:

```bash
claude --plugin-dir /path/to/your/clone
```

For that session it replaces an installed `team-skills` of the same name, so `/team-skills:<name>` runs your version.
Nothing is installed or changed.

**A tool.** Run it from the checkout, for example `./bin/paseo-limit-watch --dry-run` or `./bin/paseo-notify --help`.
Under `claude --plugin-dir` the plugin's `bin/` comes after your own `PATH`, so a `paseo-notify` linked into
`~/.local/bin` still wins over the checkout's; call `./bin/<tool>` to be sure which one runs. `paseo-title --dry-run`
still sends conversation text to Claude (see [SECURITY.md](SECURITY.md)).

## Standards

- **English** in code, comments, documents, commits and pull requests, the trigger phrases of skill descriptions
  included.
- **No private names.** No names of people, companies, customers or projects beyond those already in the repository, no
  personal home paths, no real agent or workspace ids. Fixtures use ids like `11111111-1111-4111-8111-111111111111` and
  invented conversations.
- **Standard library only**, Python 3.10 or newer, in `lib/`, `bin/` and `skills/`. No third-party imports.
- **No secrets, no disguise.** No tool reads credentials, a secret store or another process's environment, calls an
  undocumented endpoint or fakes a User-Agent.
- **No `version` in any manifest.** Without one, Claude Code takes every commit as a new version; with one, users stay
  on it until the next bump.
- **Paths are computed when used** (`Path.home()` inside a function), never at import, so tests can set `HOME`.
- **Typed and documented.** Every public function has type hints and a docstring. Comments say why, not what.
- **Paths inside a skill** are relative to the skill's base directory (the harness prints it when the skill loads),
  never `~/.agents/skills/…`.
- **Skill shape.** Frontmatter with `name` (equal to the folder name) and a `description` that starts with "Use when";
  then title, overview, invocation, steps and a table of common mistakes.
- **ruff** clean, lint and format (`ruff.toml`).
- **No tool manages the paseo daemon.** No tool, script, skill or document starts, stops or restarts it: not
  `paseo restart`, not `paseo daemon start|stop|restart`, not `systemctl --user restart paseo`. When the daemon does
  not answer, a run is skipped and logged.
- **Tests stay in a temporary home.** A test that touches `HOME` derives from `tests.support.TempHome`; no test reads or
  writes the real home directory.
- **Security is documented.** When a tool reads, writes, runs or sends something new, update its table in
  [SECURITY.md](SECURITY.md) in the same pull request.
- **Conventional commits** (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:`), in English, without AI
  attribution.

## Pull requests

1. Branch from an up-to-date `main` (a fork if you have no write access).
2. Keep the pull request to one topic, with tests for new behaviour.
3. Run every check above.
4. Describe what changes for users and how you tested it, including any command you ran against real paseo agents.
5. The maintainer merges once the review and CI are green.

## Implementers

For autonomous agents that implement a plan in this repository, for example one started by `phandoff`. This section
replaces the parts of the kickoff that do not fit here.

- **Workspace.** Work in a git worktree on its own branch, never in the main checkout and never on `main`.
- **Finish with a pull request, not a merge.** Never push to `main` and never merge into it yourself. Push your branch
  and open a pull request against `main` (in `superpowers:finishing-a-development-branch`, the option "Push and create
  a Pull Request"); the maintainer merges. If `main` moved, rebase onto it and run the checks again before you open
  the pull request.
- **Checks.** The six commands under [Checks](#checks) are the definition of green; the full set takes well under a
  minute, so subagents can run it in the foreground. There is no separate end-to-end suite, no database, no migration
  and no port.
- **Waiting.** To wait for a paseo agent, run `paseo-notify <id>` and end your turn; the message wakes you. No `sleep`,
  no polling of `paseo ls`, `logs` or `inspect`, no watcher script.
- **Report.** In the completion report, the outcome row names the pull request instead of a merge; the test-evidence
  row carries the last line of the unittest run (`OK`) on the branch head and states that the other checks exited 0.
  The migration and end-to-end rows are "—".
