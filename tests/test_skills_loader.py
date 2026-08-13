# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from pathlib import Path

from skills_loader import format_skill_block, load_skill, scan_skills, _parse_frontmatter


class SkillsLoaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        skill_dir = self.root / "demo-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: A demo skill\n---\n\n# Body\nDo the thing.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_frontmatter(self):
        meta, body = _parse_frontmatter("---\ntitle: x\n---\n\nhello")
        self.assertEqual(meta.get("title"), "x")
        self.assertIn("hello", body)

    def test_load_and_format(self):
        skill = load_skill(str(self.root), "demo-skill")
        self.assertIsNotNone(skill)
        block = format_skill_block(skill)
        self.assertIn("demo-skill", block)
        self.assertIn("Do the thing", block)

    def test_scan_recursive(self):
        items = scan_skills(str(self.root))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "demo-skill")


if __name__ == "__main__":
    unittest.main()
