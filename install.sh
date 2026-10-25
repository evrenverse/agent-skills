#!/usr/bin/env bash
# Links every executable in bin/ into ~/.local/bin, so the paseo tools and test-queue run from any shell. Claude
# Code and Codex users get the skills through the plugin (see README.md); this script only puts the CLI tools on PATH.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
dest="$HOME/.local/bin"
mkdir -p "$dest"
for script in "$here"/bin/*; do
  { [ -f "$script" ] && [ -x "$script" ]; } || continue
  name="$(basename "$script")"
  # A symlink is ours to refresh; a real file or directory is the user's.
  if [ -e "$dest/$name" ] && [ ! -L "$dest/$name" ]; then
    echo "skip $name: $dest/$name exists and is not a symlink, move it away first" >&2
    continue
  fi
  ln -sfn "$script" "$dest/$name"
  echo "linked $name -> $dest/$name"
done
