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


if __name__ == "__main__":
    unittest.main()
