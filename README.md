# agent-skills

Skills and command-line tools for teams that work with [paseo](https://github.com/getpaseo/paseo) agents in Claude
Code and Codex. The skills hand a committed plan to an autonomous implementer, spawn standalone agents, get a second
opinion or a two-model committee, take over an interrupted agent, and read and profile agent transcripts. The tools
resume agents after a usage limit, tell one agent when another one stops, and give agent tabs short titles.

The repository is one plugin, `team-skills`, published through the marketplace `evrenverse`, for Claude Code
(`.claude-plugin/`) and Codex (`.codex-plugin/`, `.agents/plugins/marketplace.json`).

## Requirements

- [paseo](https://github.com/getpaseo/paseo) 0.12.0 or newer: the skills and tools steer agents with
  `paseo send --behavior steer`.
- [superpowers](https://github.com/obra/superpowers): the `phandoff` implementer works through its execution skills.
- Python 3.10 or newer as `python3`, standard library only. The tools and the skill scripts need nothing else.
- Optional: [claude-rotate](https://github.com/evrenverse/claude-rotate) for several Claude accounts.
  `paseo-limit-watch` asks it when a Claude model limit resets and needs it for `--reload`.

Linux, macOS and Windows through WSL. The cron lines below use `flock`, which ships with Linux (util-linux) but not with
macOS: install it with `brew install flock`, or drop `flock -n <lock>` from the lines and accept that a slow pass may
overlap the next one.

## Install

### Claude Code

Commit this to your project's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "evrenverse": {
      "source": {
        "source": "git",
        "url": "https://github.com/evrenverse/agent-skills.git"
      },
      "autoUpdate": true
    }
  },
  "enabledPlugins": {
    "team-skills@evrenverse": true
  }
}
```

Claude Code registers the marketplace once a teammate trusts the project folder, and `autoUpdate` keeps it current in
the background. To install the plugin for yourself in every project instead:

```bash
claude plugin marketplace add https://github.com/evrenverse/agent-skills.git
claude plugin install team-skills@evrenverse
```

Skills are invoked as `/team-skills:<name>`, for example `/team-skills:pagent`.

### Codex

Commit this to your project's `.codex/config.toml` (Codex reads it only in projects you trust):

```toml
[marketplaces.evrenverse]
source_type = "git"
source = "https://github.com/evrenverse/agent-skills.git"

[plugins."team-skills@evrenverse"]
enabled = true
```

Or install it for yourself:

```bash
codex plugin marketplace add https://github.com/evrenverse/agent-skills.git
codex plugin add team-skills@evrenverse
```

Codex finds the plugin through this repository's `.agents/plugins/marketplace.json`. Start a new session after the
install before you use its skills.

## Updates

The manifests carry no `version`: Claude Code takes the commit as the version, so every commit on `main` reaches users
on their next update.

- **Claude Code** with `autoUpdate` refreshes the marketplace in the background during an interactive session. A
  running session keeps the skills it loaded; run `/reload-plugins` or start a new session. Without `autoUpdate`:

  ```bash
  claude plugin marketplace update evrenverse
  claude plugin update team-skills@evrenverse
  ```

- **Codex** refreshes the marketplace with this command; new sessions load the new version:

  ```bash
  codex plugin marketplace upgrade evrenverse
  ```

- **The CLI tools** linked by `install.sh` point into your clone: `git pull` there updates them. Run `install.sh`
  again after each pull: it refreshes a link whose target moved, which a pull alone leaves dangling.

## Skills

| Skill | Needs paseo | What it does |
| --- | --- | --- |
| `phandoff` | yes | Hands a committed spec and plan to an autonomous implementer that builds, reviews and merges the whole plan |
| `pspawn` | yes | Hands any other task to a standalone paseo agent in the current workspace |
| `padvisor` | yes | One agent, by default from another model family, gives a second opinion on the current task; it changes nothing |
| `pcommittee` | yes | A Claude and a Codex agent analyse a hard problem in parallel, argue out where they differ and agree on a plan |
| `pagent` | yes | Finds an agent by id, id prefix or title and shows its status, every instruction it got and its transcript |
| `ptakeover` | yes | Takes over another agent's assignment after a limit, crash or stall and finishes it |
| `pprofile` | yes | Measures an agent's run from its Claude Code transcript: time, gaps, API calls, tokens, polling |
| `continue` | no | Resumes your own work after a usage-limit interruption without redoing what already landed |
| `review-spec-and-plan` | no | A final completeness and consistency pass over a spec and plan before implementation |

The agents that `phandoff`, `pspawn`, `padvisor` and `pcommittee` start run without permission prompts. Read
[SECURITY.md](SECURITY.md) for what each of them may do and how to restrict it.

## Command-line tools

| Command | What it does |
| --- | --- |
| `paseo-limit-watch [--dry-run] [--reload]` | One pass over all paseo agents: an agent whose last message is a usage limit gets a resume line one minute after the limit resets (at once after a transient API error), then every 30 minutes while the limit is still its last message, at most three times. Agents taken over through `ptakeover` and archived agents are never resumed. An unknown reset is not guessed: agents waiting through `paseo-notify` are told instead. `--dry-run` prints what it would do and changes nothing. |
| `paseo-notify <agent> [--to <id>]` | Returns at once; the calling agent (`$PASEO_AGENT_ID`, or `--to <id>`) gets one message when `<agent>` finishes its turn, errors or needs a permission. A watch on an agent that stopped on a usage limit is handed to `paseo-limit-watch`, which re-arms it after the resume. Log: `~/.paseo-notify.log`. |
| `paseo-title [--dry-run]` | Replaces the title paseo gives a new agent (the first line of its first message, at most 60 characters) with a short topic that your own Claude writes, once the conversation has three user messages. A title a person or a skill set is left alone unless it is exactly what paseo itself would have generated; if paseo changes how it derives titles, the tool simply stops renaming. It sends conversation text to Anthropic, `--dry-run` included: see [SECURITY.md](SECURITY.md). |

With the plugin enabled, Claude Code puts `bin/` on the `PATH` of its Bash tool, so agents run these as bare commands.
Cron, your own shell and Codex need them on `PATH` themselves: clone this repository and run `install.sh`, which links
the three commands into `~/.local/bin` (it replaces a symlink of the same name and leaves a regular file alone).

```bash
git clone https://github.com/evrenverse/agent-skills.git
agent-skills/install.sh
```

No tool starts, stops or restarts the paseo daemon. When it does not answer, `paseo-limit-watch` logs the pass as
skipped, and `paseo-notify` reports the ended watch to the waiting agent, or to its log when that message cannot be
sent either.

### Cron

`paseo-limit-watch` and `paseo-title` are built for cron: one run is one pass. Add to your crontab (`crontab -e`):

```cron
PATH=/usr/local/bin:/usr/bin:/bin
*/3 * * * * flock -n /tmp/paseo-limit-watch.lock ~/.local/bin/paseo-limit-watch >> ~/.paseo-limit-watch.log 2>&1
*/5 * * * * flock -n /tmp/paseo-title.lock ~/.local/bin/paseo-title >> ~/.paseo-title.log 2>&1
```

- **`PATH`.** The tools look for `paseo`, `claude` and `claude-rotate` on `PATH`, then in `~/.local/bin`, then in the
  newest Node under `~/.nvm`. `paseo` installed from npm is a Node script and needs `node` on `PATH` as well. If
  `node`, `paseo` or a `python3` of at least 3.10 live elsewhere (an npm prefix, an nvm version), add their directories
  to the `PATH` line as absolute paths: cron does not expand `~` or `$HOME` there.
- **`paseo-title` needs a Claude login** in cron's environment. With claude-rotate installed it runs claude through
  `claude-rotate run`, which starts it on a logged-in account. Without claude-rotate it runs the first `claude` it
  finds; if that login lives in another config directory, put `CLAUDE_CONFIG_DIR=<that directory>` in front of
  `flock` on its line.
- **`--reload`** (opt-in) lets the watcher move a Claude agent to another account instead of waiting:

  ```cron
  */3 * * * * flock -n /tmp/paseo-limit-watch.lock ~/.local/bin/paseo-limit-watch --reload >> ~/.paseo-limit-watch.log 2>&1
  ```

  It applies only to a Claude agent whose provider command in `~/.paseo/config.json` is `claude-rotate`, whose limit
  resets more than 30 minutes from now, that is not running a turn and whose transcript shows no background task still
  running, and only when `claude-rotate status` reports an account to move to that is not used up. The watcher then runs
  `paseo agent reload` (paseo restarts the agent's process, claude-rotate starts it on a free account) and sends the
  resume line at once. At most one reload per reset; if the agent is still blocked, it waits for the reset as usual.

Try a pass by hand first: `paseo-limit-watch --dry-run`.

## Security

Spawned agents run without permission prompts by team default, and the `phandoff` implementer merges into `main` and
pushes. `paseo-title` sends about 2 000 characters of each agent's opening conversation to Anthropic, whichever
provider the agent runs on. `paseo-limit-watch` and `paseo-notify` open no network connection of their own.
[SECURITY.md](SECURITY.md) lists, per skill and tool, what it reads, writes, runs and sends, how to restrict spawned
agents, and how to report a vulnerability.

## Development

```bash
python3 -m unittest discover -s tests -t .
```

The suite uses fixtures only and never touches your home directory. The other checks, the standards and the
pull-request flow are in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
