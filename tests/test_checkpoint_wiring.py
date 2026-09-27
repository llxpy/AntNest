# -*- coding: utf-8 -*-
"""AntNest · 检查点接线测试

验证检查点在真实回合里被触发、被保存、且恢复后的消息列表能安全发给 API。
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

import antnest_checkpoint as ck  # noqa: E402
import antnest_events as ev  # noqa: E402
import antnest_plan as plan  # noqa: E402


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


def _tool_call(name: str, args: dict | None = None, tid: str = "call_1") -> dict:
    return {
        "role": "assistant", "content": "",
        "tool_calls": [{
            "id": tid, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(args or {}, ensure_ascii=False)},
        }],
    }


def _usage() -> dict:
    return {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}


class CheckpointWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="antnest_ckwire_")
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)
        ev.reset(cls._tmp)
        ck.reset(cls._tmp)

    @classmethod
    def tearDownClass(cls):
        ev.reset("")
        ck.reset("")
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        plan.reset("修复测试失败")
        self.an.messages = [
            {"role": "system", "content": "当前 system prompt（含工具契约）"},
            {"role": "user", "content": "修复测试失败"},
        ]
        self.an.AGENT_CANCEL = False
        self.an.COMPACT_PANIC = False
        self.an.TOKEN_CAP = 1_000_000
        self.an.COMPACT_THRESH = 0.99
        self.an.agent_reset_cancel()
        ev.set_current_task_id("t_ckwire")
        os.environ["ANT_MAX_ROUNDS"] = "4"
        os.environ.pop("ANT_CHECKPOINT", None)

    def tearDown(self):
        ev.clear_current_task_id()
        for c in ck.get_store().list("t_ckwire"):
            pass
        ck.get_store().clear("t_ckwire")

    def _run(self, script):
        it = iter(script)

        def _llm(_messages, tools=None):
            try:
                return next(it), _usage()
            except StopIteration:
                return {"role": "assistant", "content": "完成"}, _usage()

        with mock.patch.object(self.an, "llm_chat_stream", _llm):
            self.an.agent_single_loop()
        return self.an.messages

    # ---------------- 自动触发 ----------------

    def test_checkpoint_saved_during_turn(self):
        self._run([
            _tool_call("list_tools"),
            _tool_call("list_tools", tid="call_2"),
            {"role": "assistant", "content": "完成"},
        ])
        rows = ck.get_store().list("t_ckwire")
        self.assertTrue(rows, "回合结束没留下任何检查点")

    def test_checkpoint_carries_plan(self):
        self._run([
            _tool_call("update_plan", {
                "goal": "修复测试失败",
                "nodes": json.dumps([
                    {"id": "P1", "title": "扫描项目"},
                    {"id": "P2", "title": "跑测试", "depends_on": ["P1"]},
                ]),
            }),
            _tool_call("list_tools", tid="call_2"),
            {"role": "assistant", "content": "完成"},
        ])
        rows = ck.get_store().list("t_ckwire")
        self.assertTrue(rows)
        saved = rows[-1]
        self.assertTrue(saved.plan.get("nodes"), "检查点里没有计划快照")
        self.assertIn("P1", [n.get("id") for n in saved.plan["nodes"]])

    def test_checkpoint_saved_on_approval_wait(self):
        """等待用户授权时必须留检查点——那正是最需要恢复的时刻。"""
        self._run([
            _tool_call("write_file", {
                "path": str(ROOT / "antnest_config.py"), "content": "x",
            }),
            {"role": "assistant", "content": "等确认"},
        ])
        reasons = [c.reason for c in ck.get_store().list("t_ckwire")]
        self.assertIn("awaiting_approval", reasons)

    def test_checkpoint_saved_on_max_rounds(self):
        script = [_tool_call("list_tools", tid=f"c{i}") for i in range(6)]
        self._run(script)
        rows = ck.get_store().list("t_ckwire")
        self.assertTrue(rows, "达到最大轮次没留检查点")

    def test_checkpoint_disabled_by_env(self):
        os.environ["ANT_CHECKPOINT"] = "0"
        try:
            self._run([{"role": "assistant", "content": "直接回答"}])
            self.assertEqual(ck.get_store().list("t_ckwire"), [])
        finally:
            os.environ.pop("ANT_CHECKPOINT", None)

    # ---------------- 恢复 ----------------

    def test_resume_produces_api_safe_messages(self):
        self._run([
            _tool_call("update_plan", {
                "goal": "修复测试失败",
                "nodes": json.dumps([
                    {"id": "P1", "title": "扫描项目"},
                    {"id": "P2", "title": "跑测试", "depends_on": ["P1"]},
                ]),
            }),
            {"role": "assistant", "content": "计划好了"},
        ])
        messages, restored = ck.resume(
            "t_ckwire",
            system_msg={"role": "system", "content": "当前 system prompt（含工具契约）"},
        )
        self.assertIsNotNone(restored)
        self.assertTrue(ck.looks_valid(messages), "恢复出的消息列表会被 API 拒绝")
        self.assertEqual(messages[0]["content"], "当前 system prompt（含工具契约）")

    def test_resume_restores_plan_state(self):
        self._run([
            _tool_call("update_plan", {
                "goal": "g",
                "nodes": json.dumps([{"id": "P1", "title": "扫描项目"}]),
            }),
            {"role": "assistant", "content": "好"},
        ])
        plan.reset("被清空")
        ck.resume("t_ckwire")
        self.assertEqual([n.id for n in plan.current().nodes], ["P1"])

    def test_resume_prompt_mentions_progress(self):
        self._run([
            _tool_call("update_plan", {
                "goal": "修复测试失败",
                "nodes": json.dumps([
                    {"id": "P1", "title": "扫描项目"},
                    {"id": "P2", "title": "跑测试", "depends_on": ["P1"]},
                ]),
            }),
            {"role": "assistant", "content": "好"},
        ])
        rows = ck.get_store().list("t_ckwire")
        text = rows[-1].resume_prompt()
        self.assertIn("扫描项目", text)
        self.assertIn("修复测试失败", text)

    def test_resumed_messages_have_no_cot(self):
        self._run([
            _tool_call("list_tools"),
            {"role": "assistant", "content": "完成", "reasoning_content": "机密XYZ"},
        ])
        messages, _ = ck.resume("t_ckwire", system_msg={"role": "system", "content": "s"})
        self.assertNotIn("机密XYZ", json.dumps(messages, ensure_ascii=False))

    def test_resume_survives_truncated_batch(self):
        """切点落在工具批次中间也必须恢复出合法列表。"""
        ck.save("manual", task_id="t_trunc", messages=[
            {"role": "system", "content": "旧"},
            {"role": "user", "content": "任务"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "spawn_clone", "arguments": "{}"}},
            ]},
        ])
        messages, _ = ck.resume("t_trunc", system_msg={"role": "system", "content": "新"})
        self.assertTrue(ck.looks_valid(messages))
        self.assertNotEqual(messages[0]["content"], "旧", "仍在用切片里的过期 system")


class BridgeResumeTest(unittest.TestCase):
    """bridge 的恢复/重放接口。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import antnest_bridge as bridge

            cls.bridge = importlib.reload(bridge)

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="antnest_ckbridge_")
        ev.reset(self._tmp)
        ck.reset(self._tmp)

    def tearDown(self):
        ev.reset("")
        ck.reset("")
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_list_checkpoints(self):
        ck.save("worker_done", task_id="t1", goal="g", stats={"tool_calls": 2})
        core = self.bridge.AntNestCore()
        rows = core.list_checkpoints("t1")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["task_id"], "t1")

    def test_list_checkpoints_missing_task(self):
        core = self.bridge.AntNestCore()
        self.assertEqual(core.list_checkpoints("nope"), [])

    def test_replay_task(self):
        core = self.bridge.AntNestCore()
        ev.emit(ev.Event.TASK_CREATED, task_id="t9", goal="演示")
        text = core.replay_task("t9")
        self.assertIn("TASK_CREATED", text)

    def test_resume_task(self):
        core = self.bridge.AntNestCore()
        core.mod = type("M", (), {})()
        core.mod.messages = [{"role": "system", "content": "s"}]
        core._base_system = "s"
        core.subscribe(lambda k, p: None)
        ck.save("worker_done", task_id="t10", goal="g", messages=[
            {"role": "system", "content": "旧"},
            {"role": "user", "content": "任务"},
        ], plan={"goal": "g", "nodes": [{"id": "P1", "title": "a", "status": "pending"}]})
        ok, err = core.resume_task("t10")
        self.assertTrue(ok, err)
        # 恢复出的 system 必须是**当前**的（_system_message 读壳上的 messages[0]），
        # 不是检查点切片里的旧版本
        self.assertNotEqual(core.mod.messages[0]["content"], "旧")
        self.assertEqual(core.mod.messages[0]["role"], "system")
        self.assertTrue(ck.looks_valid(core.mod.messages))
        self.assertEqual(core.mod.messages[-1]["content"], "任务")

    def test_resume_task_missing(self):
        core = self.bridge.AntNestCore()
        core.mod = type("M", (), {})()
        core.mod.messages = []
        ok, err = core.resume_task("never")
        self.assertFalse(ok)
        self.assertIn("没有检查点", err)

    def test_replay_never_raises(self):
        core = self.bridge.AntNestCore()
        self.assertIsInstance(core.replay_task("whatever"), str)


if __name__ == "__main__":
    unittest.main()
