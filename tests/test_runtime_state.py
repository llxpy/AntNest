# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

import antnest_runtime_state as runtime_state


class RuntimeStateTest(unittest.TestCase):
    def test_profile_updates_incrementally(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "runtime.json")
            first = runtime_state.update_profile(path, "请简洁一点，先验证再给结论")
            second = runtime_state.update_profile(path, "以后主动推进，但高风险前先询问")
            profile = second["profile"]
            self.assertIn("concise", profile["preferences"])
            self.assertIn("verify_first", profile["preferences"])
            self.assertIn("autonomous", profile["preferences"])
            self.assertIn("ask_before_risk", profile["preferences"])
            self.assertLess(profile["strategy"]["verbosity"], 0)
            self.assertEqual(profile["strategy"]["verification"], first["profile"]["strategy"]["verification"])
            self.assertGreater(profile["strategy"]["autonomy"], 0.45)

    def test_stop_snapshot_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "runtime.json")
            runtime_state.record_stop(path, {
                "task": "修复测试",
                "decision_summary": "已定位失败断言",
                "evidence": "pytest returned 1",
                "next_step": "先询问是否继续",
            })
            stop = runtime_state.get_stop(path)
            self.assertEqual(stop["task"], "修复测试")
            self.assertIn("pytest", stop["evidence"])
            runtime_state.clear_stop(path)
            self.assertEqual(runtime_state.get_stop(path), {})

    def test_persona_prompt_keeps_permission_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "runtime.json")
            runtime_state.save_persona_style(path, "以后用中文、结论先行")
            prompt = runtime_state.persona_prompt(runtime_state.load(path))
            self.assertIn("结论先行", prompt)
            self.assertIn("不改变工具权限", prompt)

    def test_pending_self_modification_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "runtime.json")
            runtime_state.set_pending_self_modification(path, "更新 Agent loop")
            pending = runtime_state.get_pending_self_modification(path)
            self.assertEqual(pending["purpose"], "更新 Agent loop")
            runtime_state.clear_pending_self_modification(path)
            self.assertEqual(runtime_state.get_pending_self_modification(path), {})


if __name__ == "__main__":
    unittest.main()
