# -*- coding: utf-8 -*-
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import antnest_bridge as bridge
from skills_loader import load_skill, scan_skills


class BridgeListSkillsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "alpha-skill" / "SKILL.md").parent.mkdir(parents=True)
        (self.root / "alpha-skill" / "SKILL.md").write_text(
            "---\nname: alpha-skill\ndescription: Alpha skill\n---\n\nAlpha body.\n",
            encoding="utf-8",
        )
        (self.root / "legacy.baim").write_text("legacy skill content", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_skills_md_and_baim(self):
        items = bridge.list_skills(str(self.root))
        names = {x["name"] for x in items}
        self.assertIn("alpha-skill", names)
        self.assertIn("legacy", names)
        alpha = next(x for x in items if x["name"] == "alpha-skill")
        self.assertEqual(alpha["type"], "md")
        self.assertIn("Alpha", alpha["description"])

    def test_list_skills_nested_path(self):
        nested = self.root / "pkg" / "nested-skill"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text(
            "---\nname: nested-skill\ndescription: Nested\n---\n\nNested body.\n",
            encoding="utf-8",
        )
        items = scan_skills(str(self.root))
        names = {x["name"] for x in items}
        self.assertIn("pkg/nested-skill", names)
        loaded = load_skill(str(self.root), "pkg/nested-skill")
        self.assertIsNotNone(loaded)
        self.assertIn("Nested body", loaded["body"])


class SkillInjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "demo-skill").mkdir(parents=True)
        (self.root / "demo-skill" / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: Demo\n---\n\nInject this body.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_inject_skill_appends_body(self):
        importlib.reload(bridge)
        core = bridge.AntNestCore()
        core._ui_settings = {
            "skills_enabled": "true",
            "skills_dir": str(self.root),
        }
        out = core._inject_skill("用户任务", "demo-skill")
        self.assertIn("用户任务", out)
        self.assertIn("Inject this body", out)
        self.assertIn("已加载 Skill", out)

    def test_inject_skill_disabled_returns_original(self):
        importlib.reload(bridge)
        core = bridge.AntNestCore()
        core._ui_settings = {
            "skills_enabled": "false",
            "skills_dir": str(self.root),
        }
        out = core._inject_skill("用户任务", "demo-skill")
        self.assertEqual(out, "用户任务")

    def test_inject_skill_missing_name(self):
        importlib.reload(bridge)
        core = bridge.AntNestCore()
        core._ui_settings = {
            "skills_enabled": "true",
            "skills_dir": str(self.root),
        }
        out = core._inject_skill("用户任务", "not-exist")
        self.assertEqual(out, "用户任务")


if __name__ == "__main__":
    unittest.main()
