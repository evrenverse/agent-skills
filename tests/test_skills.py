"""Guards for the skill texts: every skills/*/SKILL.md (and the templates beside it) follows the house rules.

Each rule is one test that loops over the skills; a failure names the skill and the rule, so a fix is a
search for that rule in that folder.
"""

import json
import re
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "skills"
UUID = re.compile(r"\b([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
# German umlauts and sharp s, escaped so that this file holds no German letter either.
GERMAN = re.compile(r"[\u00e4\u00f6\u00fc\u00c4\u00d6\u00dc\u00df]")


def skill_dirs() -> list[Path]:
    """Every skill folder, i.e. every folder under skills/ that holds a SKILL.md."""
    return sorted(p.parent for p in SKILLS.glob("*/SKILL.md"))


def texts(skill: Path) -> dict[str, str]:
    """The markdown files of a skill folder (SKILL.md and its templates), name to content."""
    return {p.name: p.read_text(encoding="utf8") for p in sorted(skill.glob("*.md"))}


def frontmatter(text: str) -> dict[str, str]:
    """The top-level `key: value` lines of the YAML frontmatter; double-quoted values are unescaped."""
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines() if match else []:
        key, sep, value = line.partition(": ")
        if sep and key.isidentifier():
            value = value.strip()
            fields[key] = json.loads(value) if value.startswith('"') else value
    return fields


class SkillRules(unittest.TestCase):
    """One test per rule; each loops over all skills with a subTest named after the skill."""

    def test_there_are_skills(self) -> None:
        """The glob finds the skills, so the rules below do not pass on an empty list."""
        self.assertGreaterEqual(len(skill_dirs()), 9)

    def test_name_equals_folder(self) -> None:
        """Rule `name-equals-folder`: the frontmatter `name` is the folder name."""
        for skill in skill_dirs():
            with self.subTest(skill=skill.name):
                name = frontmatter(texts(skill)["SKILL.md"]).get("name")
                self.assertEqual(name, skill.name, f"{skill.name}: rule name-equals-folder")

    def test_description_starts_with_use(self) -> None:
        """Rule `description-use`: the description is a trigger and starts with "Use when "."""
        for skill in skill_dirs():
            with self.subTest(skill=skill.name):
                description = frontmatter(texts(skill)["SKILL.md"]).get("description", "")
                self.assertTrue(
                    description.startswith("Use when "),
                    f'{skill.name}: rule description-use, start with "Use when " (is {description[:30]!r})',
                )

    def test_one_shape(self) -> None:
        """Rule `one-shape`: overview, invocation, steps and a mistakes table, in that order."""
        sections = ("## Overview", "## Invocation", "## Steps", "## Common mistakes")
        for skill in skill_dirs():
            with self.subTest(skill=skill.name):
                headings = re.findall(r"^## .+$", texts(skill)["SKILL.md"], re.MULTILINE)
                found = [h for h in headings if h in sections]
                self.assertEqual(found, list(sections), f"{skill.name}: rule one-shape")

    def test_no_home_paths(self) -> None:
        """Rule `no-home-path`: no `/home/` path; skills use their base directory or `{SKILLS_ROOT}`."""
        for skill in skill_dirs():
            for name, text in texts(skill).items():
                with self.subTest(skill=skill.name, file=name):
                    self.assertNotIn("/home/", text, f"{skill.name}/{name}: rule no-home-path")

    def test_uuids_are_obvious_fakes(self) -> None:
        """Rule `fake-uuid`: an ID-shaped string must start with one repeated digit (`11111111-…`)."""
        for skill in skill_dirs():
            for name, text in texts(skill).items():
                with self.subTest(skill=skill.name, file=name):
                    matches = UUID.finditer(text)
                    real = [m.group(0) for m in matches if not re.fullmatch(r"(\d)\1{7}", m.group(1))]
                    if real:
                        self.fail(f"{skill.name}/{name}: rule fake-uuid, not an obvious fake: {real}")

    def test_english_only(self) -> None:
        """Rule `english-only`: no German letters, trigger phrases included."""
        for skill in skill_dirs():
            for name, text in texts(skill).items():
                with self.subTest(skill=skill.name, file=name):
                    self.assertIsNone(GERMAN.search(text), f"{skill.name}/{name}: rule english-only")


class PhandoffPlaceholders(unittest.TestCase):
    """The kickoff template and the fill-in step of phandoff's SKILL.md name the same placeholders."""

    # NAME only appears in the template's explanation of its own syntax.
    IGNORED = frozenset({"NAME"})

    def placeholders(self, text: str) -> set[str]:
        """The `{NAME}` and `{NAME: …}` tokens of a text, names only."""
        return set(re.findall(r"\{([A-Z][A-Z_]*)[:}]", text)) - self.IGNORED

    def test_template_and_skill_agree(self) -> None:
        """Every placeholder of the kickoff is documented in SKILL.md, and SKILL.md fills nothing else."""
        folder = SKILLS / "phandoff"
        template = (folder / "kickoff-template.md").read_text(encoding="utf8")
        fill_in = (folder / "SKILL.md").read_text(encoding="utf8").split("## Spawn", 1)[1].split("```", 1)[0]
        used, documented = self.placeholders(template), self.placeholders(fill_in)
        self.assertEqual(used - documented, set(), "phandoff: kickoff placeholders missing from SKILL.md")
        self.assertEqual(documented - used, set(), "phandoff: SKILL.md fills placeholders the kickoff lacks")


if __name__ == "__main__":
    unittest.main()
