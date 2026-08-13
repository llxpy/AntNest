# -*- coding: utf-8 -*-
import importlib
import json
import os
import sys
import tempfile
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


class CloneWorkerTest(unittest.TestCase):
    def test_blocks_dangerous_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_file = os.path.join(tmp, "result.json")
            os.environ["AN_CLONE_MODE"] = "1"
            os.environ["AN_CLONE_COMMAND"] = "format c:"
            os.environ["AN_RESULT_FILE"] = result_file
            os.environ["AN_CLONE_DIR"] = tmp
            import antnest_clone_worker as cw

            with mock.patch.object(sys, "exit", side_effect=SystemExit(0)):
                with self.assertRaises(SystemExit):
                    importlib.reload(cw)
                    cw.run_if_clone_mode()
            data = json.loads(Path(result_file).read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "blocked")
        for k in ("AN_CLONE_MODE", "AN_CLONE_COMMAND", "AN_RESULT_FILE", "AN_CLONE_DIR"):
            os.environ.pop(k, None)


class AntNestToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)

    def test_spawn_clone_depth_block(self):
        os.environ["AN_DEPTH"] = "99"
        try:
            result = json.loads(self.an.spawn_clone("echo hi"))
            self.assertEqual(result["status"], "blocked")
        finally:
            os.environ.pop("AN_DEPTH", None)

    def test_spawn_clone_copies_worker_deps(self):
        import tempfile
        from pathlib import Path

        pool = Path(tempfile.mkdtemp())
        orig_pool = self.an.CLONE_POOL_DIR
        self.an.CLONE_POOL_DIR = str(pool)
        os.environ["AN_DEPTH"] = "0"
        clone_dir = None
        try:
            with mock.patch.object(self.an.subprocess, "Popen") as popen:
                proc = mock.MagicMock()
                proc.poll.return_value = 0
                proc.returncode = 0
                proc.communicate.return_value = ("", "")
                popen.return_value = proc

                def _capture_clone_dir(*args, **kwargs):
                    nonlocal clone_dir
                    clone_dir = kwargs.get("cwd") or (args[1] if len(args) > 1 else None)
                    return proc

                popen.side_effect = _capture_clone_dir
                with mock.patch.object(self.an.shutil, "rmtree"):
                    self.an.spawn_clone("echo x", timeout=5)
                self.assertTrue(clone_dir and Path(clone_dir).is_dir())
                files = {p.name for p in Path(clone_dir).iterdir()}
                self.assertIn("AntNest.py", files)
                self.assertIn("antnest_clone_worker.py", files)
        finally:
            self.an.CLONE_POOL_DIR = orig_pool
            os.environ.pop("AN_DEPTH", None)
            import shutil
            shutil.rmtree(pool, ignore_errors=True)

    def test_queen_tools_include_mcp_when_enabled(self):
        self.an.MCP_ENABLED = True
        self.an.MCP_HUB = object()
        names = [t["function"]["name"] for t in self.an.get_queen_tools()]
        self.assertIn("mcp_call", names)
        self.assertIn("mcp_list_tools", names)
        self.an.MCP_ENABLED = False
        self.an.MCP_HUB = None


if __name__ == "__main__":
    unittest.main()
