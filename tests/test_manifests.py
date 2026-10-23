"""Guards for the plugin manifests: names, no `version`, the MIT license, and that named paths exist."""

import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
CLAUDE_MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
CODEX_PLUGIN = ROOT / ".codex-plugin" / "plugin.json"
CODEX_MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"
MANIFESTS = (CLAUDE_PLUGIN, CLAUDE_MARKETPLACE, CODEX_PLUGIN, CODEX_MARKETPLACE)


def load(path: Path) -> dict[str, Any]:
    """Parse one manifest."""
    return json.loads(path.read_text(encoding="utf8"))


def has_key(node: Any, key: str) -> bool:
    """Whether `key` occurs as an object key anywhere in the parsed JSON `node`."""
    if isinstance(node, dict):
        return key in node or any(has_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(has_key(v, key) for v in node)
    return False


def named_paths(path: Path) -> list[str]:
    """The repository paths a manifest names (icons, skills, local plugin sources), as written."""
    data = load(path)
    names: list[Any] = [data.get("icon"), data.get("skills")]
    interface = data.get("interface", {})
    names += [interface.get("logo"), interface.get("composerIcon")]
    for plugin in data.get("plugins", []):
        source = plugin["source"]
        # A string source is a local path; Codex spells it {"source": "local", "path": ...}.
        local = isinstance(source, dict) and source["source"] == "local"
        names.append(source["path"] if local else source)
    return [n for n in names if n is not None]


class ManifestRules(unittest.TestCase):
    def test_every_manifest_parses(self) -> None:
        for path in MANIFESTS:
            with self.subTest(manifest=path.relative_to(ROOT).as_posix()):
                self.assertIsInstance(load(path), dict)

    def test_names(self) -> None:
        for path in (CLAUDE_PLUGIN, CODEX_PLUGIN):
            self.assertEqual(load(path)["name"], "team-skills", path)
        for path in (CLAUDE_MARKETPLACE, CODEX_MARKETPLACE):
            data = load(path)
            self.assertEqual(data["name"], "evrenverse", path)
            self.assertEqual([p["name"] for p in data["plugins"]], ["team-skills"], path)

    def test_no_manifest_has_a_version(self) -> None:
        # Without a version Claude Code takes every commit as a new release.
        for path in MANIFESTS:
            self.assertFalse(has_key(load(path), "version"), path)

    def test_plugins_are_mit_licensed(self) -> None:
        for path in (CLAUDE_PLUGIN, CODEX_PLUGIN):
            self.assertEqual(load(path)["license"], "MIT", path)
        self.assertIn("MIT License", (ROOT / "LICENSE").read_text(encoding="utf8"))

    def test_codex_marketplace_source_is_local(self) -> None:
        # A `url` source makes Codex clone the checkout as a git remote.
        for plugin in load(CODEX_MARKETPLACE)["plugins"]:
            self.assertEqual(plugin["source"], {"source": "local", "path": "./"})

    def test_every_named_path_exists(self) -> None:
        for path in MANIFESTS:
            for name in named_paths(path):
                with self.subTest(manifest=path.relative_to(ROOT).as_posix(), path=name):
                    self.assertTrue(name.startswith("./"), "paths are relative to the repository root")
                    self.assertTrue((ROOT / name).exists())


if __name__ == "__main__":
    unittest.main()
