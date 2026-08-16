# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path

import code_tools as ct


class CodeToolsTest(unittest.TestCase):
    def test_resolve_path_relative(self):
        root = Path("E:/proj")
        p = ct.resolve_path("src/main.py", str(root))
        self.assertEqual(p, Path("E:/proj/src/main.py").resolve())

    def test_unwrap_worker_json_nested(self):
        inner = {"status": "ok", "content": "hello"}
        outer = {"status": "ok", "stdout": json.dumps(inner), "exit_code": 0}
        out = ct.unwrap_worker_json(json.dumps(outer))
        self.assertIn("hello", out)

    def test_unwrap_worker_json_cancelled(self):
        outer = {"status": "cancelled", "error": "stopped"}
        out = ct.unwrap_worker_json(json.dumps(outer))
        self.assertIn("cancelled", out)

    def test_validate_grep_pattern(self):
        self.assertIsNone(ct.validate_grep_pattern(r"def \w+"))
        self.assertIsNotNone(ct.validate_grep_pattern("[invalid"))

    def test_build_scripts_non_empty(self):
        p = Path("E:/proj/foo.py")
        self.assertIn("pathlib", ct.build_view_file_script(p, 1, 10))
        self.assertIn("iterdir", ct.build_list_dir_script(p.parent, 20))
        self.assertIn("rglob", ct.build_grep_script(p.parent, "foo", "*.py", 10))
        self.assertIn("write_text", ct.build_write_file_script(p, "x=1"))
        self.assertIn("text.replace", ct.build_search_replace_script(p, "a", "b", False))
        self.assertIn("replace_all", ct.build_search_replace_script(p, "a", "b", True))

    def test_build_scripts_compilable(self):
        """生成的脚本必须能通过 compile() 语法验证（回归防护：修复单行复合语句 bug）"""
        p = Path("E:/proj/foo.py")
        cases = [
            ("view_file", ct.build_view_file_script(p, 1, 10)),
            ("list_dir", ct.build_list_dir_script(p.parent, 20)),
            ("grep", ct.build_grep_script(p.parent, "foo", "*.py", 10)),
            ("write_file", ct.build_write_file_script(p, "x=1")),
            ("search_replace", ct.build_search_replace_script(p, "a", "b", False)),
            ("search_replace_all", ct.build_search_replace_script(p, "a", "b", True)),
        ]
        for name, code in cases:
            with self.subTest(name=name):
                try:
                    compile(code, f"<{name}>", "exec")
                except SyntaxError as e:
                    self.fail(f"{name} 生成的脚本语法错误: {e}")

    def test_worker_py_cmd_windows_base64(self):
        cmd = ct.worker_py_cmd('print("hello")', is_windows=True, python_exe="python")
        self.assertIn("base64", cmd)
        self.assertNotIn('\\"', cmd)

    def test_worker_py_cmd_windows_runs(self):
        import os
        import subprocess

        if os.name != "nt":
            self.skipTest("Windows only")
        cmd = ct.worker_py_cmd('print("antnest_ok")', is_windows=True, python_exe="python")
        r = subprocess.run(
            ["powershell", "-Command", cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertIn("antnest_ok", r.stdout, msg=r.stderr or r.stdout)


if __name__ == "__main__":
    unittest.main()
