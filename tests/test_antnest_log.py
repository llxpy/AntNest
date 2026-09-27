# -*- coding: utf-8 -*-
"""antnest_log 单元测试：日志初始化、结构化输出、审计日志。"""
import json
import logging
import os
import tempfile
import unittest


class LogSetupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        # 每次独立目录，避免共享到全局单例状态
        import antnest_log
        self.mod = antnest_log
        antnest_log._initialized = False  # 允许重新 setup

    def test_setup_creates_files(self):
        self.mod.setup(log_dir=self._tmp)
        self.assertTrue(os.path.exists(os.path.join(self._tmp, "antnest.log")))
        self.assertTrue(os.path.exists(os.path.join(self._tmp, "audit.log")))

    def test_file_output_is_json(self):
        self.mod.setup(log_dir=self._tmp)
        log = self.mod.get_logger("unit")
        log.info("hello json")
        self._flush()
        with open(os.path.join(self._tmp, "antnest.log"), encoding="utf-8") as f:
            line = f.readline().strip()
            data = json.loads(line)
        self.assertEqual(data["level"], "INFO")
        self.assertEqual(data["logger"], "antnest.unit")
        self.assertEqual(data["msg"], "hello json")

    def test_logger_namespacing(self):
        self.mod.setup(log_dir=self._tmp)
        log = self.mod.get_logger("loop")
        self.assertEqual(log.name, "antnest.loop")
        log2 = self.mod.get_logger("antnest.queen")
        self.assertEqual(log2.name, "antnest.queen")

    def test_audit_log_entries(self):
        self.mod.setup(log_dir=self._tmp)
        audit = self.mod.get_audit()
        audit.log_tool_call("write_file", {"path": "a.txt", "content": "x"}, result_preview="done")
        audit.log_worker_done("w1", 0, 12.3)
        self._flush()
        with open(os.path.join(self._tmp, "audit.log"), encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        self.assertEqual(len(lines), 2)
        d1 = json.loads(lines[0])
        self.assertEqual(d1["extra"]["tool"], "write_file")
        # 超长参数被截断
        from antnest_log import _safe_args
        long_arg = _safe_args({"content": "y" * 600})["content"]
        self.assertIn("...", long_arg)

    def test_bridge_emit_callback(self):
        """桥接事件回调应收到处理后文本。"""
        self.mod.setup(log_dir=self._tmp)
        received = []
        self.mod.set_bridge_emit(lambda tag, text: received.append((tag, text)))
        log = self.mod.get_logger("bridge")
        log.warning("some warn")
        self.assertTrue(received, "bridge handler 应收到事件")
        tag, text = received[0]
        self.assertEqual(tag, "warn")
        self.assertIn("some warn", text)

    def test_audit_never_raised(self):
        """审计日志应 noexcept（写失败时静默）。"""
        self.mod.setup(log_dir=self._tmp)
        audit = self.mod.get_audit()
        audit.log_tool_call("x", {})  # 不应抛异常
        self.assertTrue(True)

    def _flush(self):
        for h in logging.getLogger("antnest.audit").handlers:
            h.flush()
        for h in logging.getLogger("antnest").handlers:
            h.flush()


if __name__ == "__main__":
    unittest.main()
