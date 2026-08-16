# -*- coding: utf-8 -*-
"""回归测试：锁定 1.2.2 安全修复（S1-S5）。

对应 BUG_REVIEW_1.2.2.md：
  S1  API Key 不再明文落盘 config.json（改存独立密钥文件）
  S2  危险命令过滤覆盖 rm -rf . / ~ / * / /home 等递归删除
  S3  run_python 不再作为蚁后工具暴露（蚁后主进程不直接执行任意代码）
  S4  resolve_path 强制限制在项目目录内（防 ../ 逃逸 / 写项目外）
  S5  web_fetch 仅允许 http/https（防 file:// 读本地 / SSRF）
"""
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


class SecurityFixesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)
            import code_tools as ct

            cls.ct = ct
            import antnest_bridge as bridge

            cls.bridge = bridge

    # ---- S2 危险命令过滤 ----
    def test_danger_blocks_recursive_deletes(self):
        blocked = [c for c, _ in [
            ("rm -rf /home", ""), ("rm -rf .", ""), ("rm -rf ~", ""),
            ("rm -rf *", ""), ("rm -r /etc", ""), ("rm -rf C:\\", ""),
            ("format c:", ""), ("shutdown /s", ""),
        ]]
        for cmd in blocked:
            with self.subTest(cmd=cmd):
                res, _why = self.an._check_danger_command(cmd)
                self.assertTrue(res, f"应拦截：{cmd}")

    def test_danger_allows_project_relative(self):
        allowed = ["rm -rf node_modules", "rm -r build", "rm -rf ./dist"]
        for cmd in allowed:
            with self.subTest(cmd=cmd):
                res, _why = self.an._check_danger_command(cmd)
                self.assertFalse(res, f"项目内相对路径不应拦截：{cmd}")

    # ---- S3 蚁后不直接执行 run_python ----
    def test_queen_tools_exclude_run_python(self):
        names = [t["function"]["name"] for t in self.an.get_queen_tools()]
        self.assertNotIn("run_python", names)

    # ---- S4 路径越界 ----
    def test_resolve_path_blocks_escape(self):
        with self.assertRaises(ValueError):
            self.ct.resolve_path("../../etc/passwd", str(ROOT))
        with self.assertRaises(ValueError):
            self.ct.resolve_path("C:/Windows/system32/x.txt", str(ROOT))

    def test_resolve_path_allows_relative(self):
        p = self.ct.resolve_path("sub/rel.txt", str(ROOT))
        self.assertTrue(str(p).startswith(str(ROOT.resolve())))

    # ---- S5 web_fetch 协议白名单 ----
    def test_web_fetch_rejects_non_http(self):
        self.assertIn("拒绝", self.an.web_fetch("file:///etc/passwd"))
        self.assertIn("拒绝", self.an.web_fetch("ftp://example.com/x"))

    # ---- S1 密钥文件 ----
    def test_secret_key_not_in_config_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "config.json")
            ui = os.path.join(tmp, "ui_config.json")
            Path(cfg).write_text(
                json.dumps({"api": {"api_key": "OLD", "base_url": "x"}}), encoding="utf-8"
            )
            Path(ui).write_text("{}", encoding="utf-8")
            os.environ["ANT_CONFIG_PATH"] = cfg
            os.environ["ANT_UI_CONFIG_PATH"] = ui
            try:
                reloaded = importlib.reload(self.bridge)
                # 模拟 UI 保存 key
                reloaded.save_settings({"llm_api_key": "sk-secret123"}, {})
                data = json.loads(Path(cfg).read_text(encoding="utf-8"))
                self.assertEqual(data["api"].get("api_key"), "")
                self.assertTrue(os.path.exists(reloaded.KEY_FILE))
                self.assertEqual(reloaded._load_secret_key(), "sk-secret123")
                # 清空 key 后密钥文件应被删除
                reloaded.save_settings({"llm_api_key": ""}, {})
                self.assertFalse(os.path.exists(reloaded.KEY_FILE))
                # 旧 config.json 里的明文 key 读不到（回退也应为空）
                self.assertEqual(reloaded._load_secret_key(), "")
            finally:
                os.environ.pop("ANT_CONFIG_PATH", None)
                os.environ.pop("ANT_UI_CONFIG_PATH", None)
                importlib.reload(self.bridge)


if __name__ == "__main__":
    unittest.main()


class CloneRecursionTest(unittest.TestCase):
    """回归：工蚁执行的命令子进程绝不能继承 AntNest 的 clone 控制变量，
    否则命令里 `python AntNest.py` / `import AntNest` 会带着 AN_CLONE_MODE 无限自嵌套，
    每级一个新 python 进程，最终撑爆内存。
    """

    def test_worker_command_env_stripped(self):
        import antnest_clone_worker as cw
        import sys as _sys
        from unittest import mock as _mock

        tmp = tempfile.mkdtemp()
        result_file = os.path.join(tmp, "result.json")
        clone_dir = os.path.join(tmp, "clone")
        os.makedirs(clone_dir, exist_ok=True)
        cmd = 'python -c "import os; print(\'MODE=\'+str(os.environ.get(\'AN_CLONE_MODE\')))"'
        os.environ["AN_CLONE_MODE"] = "1"
        os.environ["AN_CLONE_COMMAND"] = cmd
        os.environ["AN_CLONE_COMMAND_FILE"] = ""
        os.environ["AN_RESULT_FILE"] = result_file
        os.environ["AN_CLONE_DIR"] = clone_dir
        os.environ["AN_CLONE_TIMEOUT"] = "15"
        try:
            with _mock.patch.object(_sys, "exit", side_effect=SystemExit(0)):
                try:
                    cw.run_if_clone_mode()
                except SystemExit:
                    pass
            data = json.loads(Path(result_file).read_text(encoding="utf-8"))
            # 关键断言：命令子进程里 AN_CLONE_MODE 必须被剥离（None）
            self.assertIn("MODE=None", data.get("stdout") or "")
        finally:
            for _k in ("AN_CLONE_MODE", "AN_CLONE_COMMAND", "AN_CLONE_COMMAND_FILE",
                       "AN_RESULT_FILE", "AN_CLONE_DIR", "AN_CLONE_TIMEOUT"):
                os.environ.pop(_k, None)

