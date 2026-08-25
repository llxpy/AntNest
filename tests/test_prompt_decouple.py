# -*- coding: utf-8 -*-
"""回归测试：主题文件解耦（prompts/queen_system.md 懒加载 + 记忆树接线）。"""

import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mock_models_open(*_args, **_kwargs):
    body = json.dumps({"data": [{"id": "deepseek-v4-flash", "context_length": 128000}]}).encode()

    class _Resp:
        status = 200

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    return _Resp()


class PromptDecoupleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)

    def test_prompt_file_exists(self):
        self.assertTrue((ROOT / "prompts" / "queen_system.md").is_file())

    def test_system_prompt_loaded_from_file(self):
        self.assertIn("蚁后/工蚁架构", self.an.SYSTEM_PROMPT)
        self.assertIn("spawn_clone", self.an.SYSTEM_PROMPT)
        # 会话级占位符保留，供使用点 .format 填充
        self.assertIn("{nest_md}", self.an.SYSTEM_PROMPT)
        self.assertIn("{hints}", self.an.SYSTEM_PROMPT)
        filled = self.an.SYSTEM_PROMPT.format(
            nest_md="NEST内容", hints="HINT内容", env_info="环境",
            model_capability=self.an.model_capability_summary(),
        )
        self.assertIn("NEST内容", filled)
        self.assertIn("HINT内容", filled)
        self.assertIn("模型", filled)  # 能力画像已填充

    def test_memory_tree_wired(self):
        self.assertTrue(self.an.MEMORY_TREE_FILE.endswith("memory_tree.json"))
        self.assertIsNotNone(getattr(self.an, "memory_tree", None))
        self.assertIn("memory_tree.py", self.an._ARTIFACT_EXCLUDES)


if __name__ == "__main__":
    unittest.main()