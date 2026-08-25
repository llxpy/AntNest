# -*- coding: utf-8 -*-
"""回归测试：工具工坊（Tool Forge）——登记/列表/取源码/删除/路径穿越防护。

注意：setUpClass 预登记工具，测试方法间不依赖执行顺序（pytest 字母序）。
"""

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mock_models_open(*_a, **_k):
    body = json.dumps({"data": [{"id": "deepseek-v4-flash", "context_length": 128000}]}).encode()

    class _Resp:
        status = 200

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *_x):
            return False

    return _Resp()


_TOOL_CODE = (
    "import json, sys\n"
    "\n"
    "def main():\n"
    "    text = sys.stdin.read()\n"
    "    data = json.loads(text)\n"
    "    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))\n"
)


class ToolForgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)
        # 工具库目录指向临时目录，避免污染真实项目
        cls.tmpdir = tempfile.mkdtemp(prefix="antnest_tf_")
        cls.an.PROJECT_ANT_DIR = cls.tmpdir
        # 预登记一个工具，供各测试独立使用
        cls.tool_root = Path(cls.tmpdir, "tools", "json_pretty")
        r = json.loads(cls.an.register_tool(
            name="json_pretty",
            description="将 JSON 字符串美化输出并排序键",
            parameters='{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}',
            code=_TOOL_CODE,
            category="data",
            source_note="由日志格式化任务演化",
        ))
        assert r["status"] == "ok", r

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_list_contains_registered(self):
        an = self.an
        lst = json.loads(an.list_tools())
        self.assertEqual(lst["status"], "ok")
        names = [t["name"] for t in lst["tools"]]
        self.assertIn("json_pretty", names)
        self.assertTrue(self.tool_root.joinpath("tool.py").is_file())
        self.assertTrue(self.tool_root.joinpath("tool.json").is_file())

    def test_get_source_and_rewrite(self):
        an = self.an
        src = json.loads(an.get_tool_source("json_pretty"))
        self.assertEqual(src["status"], "ok")
        self.assertIn("json.dumps", src["code"])
        # 覆盖更新：version 递增
        r = json.loads(an.register_tool(
            name="json_pretty", description="改进版", code=src["code"] + "# v2\n"
        ))
        self.assertTrue(r["replaced"])
        meta = json.loads(self.tool_root.joinpath("tool.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["version"], 2)

    def test_register_rejects_bad_name(self):
        an = self.an
        r = json.loads(an.register_tool(name="../evil", description="x", code="pass"))
        self.assertEqual(r["status"], "error")
        self.assertFalse(Path(self.tmpdir, "..", "evil").exists())

    def test_get_missing_tool(self):
        an = self.an
        r = json.loads(an.get_tool_source("not_exist"))
        self.assertEqual(r["status"], "error")

    def test_remove_tool(self):
        an = self.an
        r = json.loads(an.remove_tool("json_pretty"))
        self.assertEqual(r["status"], "ok")
        self.assertFalse(self.tool_root.exists())
        r2 = json.loads(an.remove_tool("json_pretty"))
        self.assertEqual(r2["status"], "error")

    def test_toolforge_wired_into_shell(self):
        an = self.an
        # 壳工具注册表含工坊三件套
        for name in ("register_tool", "list_tools", "get_tool_source"):
            self.assertIn(name, an.tool_executors)
        # 工具 schema 已 re-export
        for attr in ("register_tool_schema", "list_tools_schema", "get_tool_source_schema"):
            self.assertTrue(hasattr(an, attr), attr)
        # 蚁后工具列表包含工坊工具
        tools = an.get_queen_tools()
        names = [t["function"]["name"] for t in tools]
        for name in ("register_tool", "list_tools", "get_tool_source"):
            self.assertIn(name, names)


if __name__ == "__main__":
    unittest.main()