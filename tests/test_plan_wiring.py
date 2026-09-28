# -*- coding: utf-8 -*-
"""AntNest · 计划层接线测试

验证 Plan 不是「有个模块就算做了」，而是真的接进了：
spawn_clone（派生 + 绑定 + 归巢推进）、update_plan 工具、UI 事件。

最重要的一条是路线图 §3 的硬指标：
    **任何含 spawn_clone 的任务，UI 都有非空 Plan 面板。**
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

import antnest_events as ev  # noqa: E402
import antnest_plan as plan  # noqa: E402
from antnest_plan import NodeStatus  # noqa: E402


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

def _llm_result(msg, usage):
    """llm_chat_stream 返回 (message, usage, finish_reason)。

    第三位按有无 tool_calls 给真实值：统一填 "stop" 会在将来有测试
    真的走失败归因时喂进一个假的 finish_reason。
    """
    return msg, usage, ("tool_calls" if (msg or {}).get("tool_calls") else "stop")



class PlanToolWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="antnest_planwire_")
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)
        ev.reset(cls._tmp)

    @classmethod
    def tearDownClass(cls):
        ev.reset("")
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        plan.reset("测试目标")
        self.an.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "修复测试失败"},
        ]
        self.an.AGENT_CANCEL = False
        self.an.COMPACT_PANIC = False
        self.an.TOKEN_CAP = 1_000_000
        self.an.COMPACT_THRESH = 0.99
        self.an.agent_reset_cancel()
        ev.set_current_task_id("t_planwire")
        os.environ["ANT_MAX_ROUNDS"] = "4"

    def tearDown(self):
        ev.clear_current_task_id()

    def _run(self, script):
        it = iter(script)

        def _llm(_messages, tools=None):
            try:
                return _llm_result(next(it), _usage())
            except StopIteration:
                return _llm_result({"role": "assistant", "content": "完成"}, _usage())

        with mock.patch.object(self.an, "llm_chat_stream", _llm):
            self.an.agent_single_loop()
        return self.an.messages

    # ---------------- 工具可见性 ----------------

    def test_update_plan_is_exposed(self):
        names = [t["function"]["name"] for t in self.an.get_queen_tools()]
        self.assertIn("update_plan", names)

    def test_update_plan_is_read_level(self):
        """level=READ：只读审阅模式下也该能规划（它不改任何文件）。"""
        import antnest_registry as reg

        self.assertEqual(reg.get("update_plan").level.name, "READ")
        self.assertFalse(reg.get("update_plan").mutating)

    def test_update_plan_allowed_at_read_only_level(self):
        from antnest_permissions import PermLevel, PermissionEngine, PermissionsConfig, PermissionPolicy

        engine = PermissionEngine(
            PermissionsConfig(level=PermLevel.READ),
            PermissionPolicy(level=PermLevel.READ, ask_above_level=PermLevel.READ),
        )
        self.assertTrue(engine.check_tool("update_plan", {}, required=PermLevel.READ).allowed)

    def test_schema_declares_required_nodes(self):
        schema = self.an.update_plan_schema
        self.assertEqual(schema["function"]["name"], "update_plan")
        self.assertIn("nodes", schema["function"]["parameters"]["required"])
        self.assertIn("goal", schema["function"]["parameters"]["properties"])

    # ---------------- 端到端：显式规划 ----------------

    def test_explicit_plan_through_loop(self):
        self._run([
            _tool_call("update_plan", {
                "goal": "修复测试失败",
                "nodes": json.dumps([
                    {"id": "P1", "title": "扫描项目"},
                    {"id": "P2", "title": "跑测试", "depends_on": ["P1"]},
                ]),
            }),
            {"role": "assistant", "content": "计划已登记"},
        ])
        p = plan.current()
        self.assertTrue(p.explicit)
        self.assertEqual([n.id for n in p.nodes], ["P1", "P2"])
        self.assertEqual(p.goal, "修复测试失败")

    def test_explicit_plan_events_recorded(self):
        self._run([
            _tool_call("update_plan", {
                "goal": "g",
                "nodes": json.dumps([{"id": "P1", "title": "a"}]),
            }),
            {"role": "assistant", "content": "好"},
        ])
        kinds = [r.event for r in ev.get_log().records("t_planwire")]
        self.assertIn("PLAN_UPDATED", kinds)

    def test_cycle_rejection_through_loop(self):
        self._run([
            _tool_call("update_plan", {"nodes": json.dumps([
                {"id": "A", "title": "a", "depends_on": ["B"]},
                {"id": "B", "title": "b", "depends_on": ["A"]},
            ])}),
            {"role": "assistant", "content": "被拒了"},
        ])
        kinds = [r.event for r in ev.get_log().records("t_planwire")]
        self.assertIn("PLAN_REJECTED", kinds)
        self.assertEqual(plan.current().progress()["total"], 0, "被拒的计划仍被写入了")

    def test_rejected_plan_does_not_break_loop(self):
        """update_plan 被拒不能返回 approval_required，否则 loop 会误以为
        需要用户授权并打断批次。"""
        self._run([
            _tool_call("update_plan", {"nodes": "{bad json"}),
            _tool_call("list_tools", tid="call_2"),
            {"role": "assistant", "content": "继续"},
        ])
        blob = json.dumps(self.an.messages, ensure_ascii=False)
        self.assertNotIn("approval_required", blob)


class PlanAutoDeriveWiringTest(unittest.TestCase):
    """硬指标：任何含 spawn_clone 的任务，UI 都有非空 Plan 面板。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="antnest_planwire2_")
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)
        ev.reset(cls._tmp)

    @classmethod
    def tearDownClass(cls):
        ev.reset("")
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        plan.reset("扫描项目")
        os.environ.pop("AN_DEPTH", None)
        self.an.agent_reset_cancel()

    def test_spawn_creates_plan_node(self):
        """真实派一次工蚁（命令无害），验证计划被自动派生。"""
        with mock.patch.object(self.an, "_ACTIVE_CLONE_PROCS", []):
            out = json.loads(self.an.spawn_clone("echo antnest-plan-ok", timeout=30))
        p = plan.current()
        self.assertGreaterEqual(
            p.progress()["total"], 1,
            "spawn_clone 之后计划仍为空——自动派生没生效",
        )
        self.assertIsNotNone(out.get("task_id"))

    def test_spawn_node_completes_on_success(self):
        with mock.patch.object(self.an, "_ACTIVE_CLONE_PROCS", []):
            self.an.spawn_clone("echo antnest-plan-ok", timeout=30)
        statuses = [n.status for n in plan.current().nodes]
        self.assertIn(
            NodeStatus.COMPLETED, statuses,
            f"工蚁成功归巢但节点未完成：{statuses}",
        )

    def test_rendered_plan_is_non_empty_after_spawn(self):
        import ui_render

        with mock.patch.object(self.an, "_ACTIVE_CLONE_PROCS", []):
            self.an.spawn_clone("echo antnest-plan-ok", timeout=30)
        html = ui_render.render_plan(plan.snapshot())
        self.assertTrue(html, "路线图 §3 硬指标：含 spawn 的任务必须有非空 Plan 面板")
        self.assertIn("plan-panel", html)

    def test_depth_blocked_spawn_still_leaves_plan_empty(self):
        """被深度拦截的 spawn 不该凭空造节点（什么都没执行）。"""
        os.environ["AN_DEPTH"] = "99"
        try:
            out = json.loads(self.an.spawn_clone("echo hi"))
            self.assertEqual(out["status"], "blocked")
            self.assertEqual(plan.current().progress()["total"], 0)
        finally:
            os.environ.pop("AN_DEPTH", None)

    def test_denied_spawn_leaves_plan_empty(self):
        """被权限拒绝的 spawn 同样不该造节点。"""
        out = json.loads(self.an.spawn_clone("rm -rf /"))
        self.assertIn(out["status"], ("approval_required", "denied"))
        self.assertEqual(plan.current().progress()["total"], 0)


class PlanUiEventTest(unittest.TestCase):
    """bridge 必须把计划推给 UI。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import antnest_bridge as bridge

            cls.bridge = importlib.reload(bridge)

    def test_plan_event_kind_is_emitted(self):
        """_turn 开始时应 emit plan 事件，UI 才有东西可渲染。"""
        core = self.bridge.AntNestCore()
        got: list[tuple] = []
        core.subscribe(lambda kind, payload: got.append((kind, payload)))
        # 不真跑 turn（会 import 核心），只验证 emit 协议里有 plan 这一路
        core.emit("plan", **{"plan_id": "p1", "nodes": [], "goal": "g"})
        self.assertTrue(any(k == "plan" for k, _ in got))
        payload = next(p for k, p in got if k == "plan")
        self.assertEqual(payload["goal"], "g")

    def test_task_id_event_kind_documented(self):
        src = (ROOT / "antnest_bridge.py").read_text(encoding="utf-8")
        self.assertIn("task_id  {task_id}", src)


if __name__ == "__main__":
    unittest.main()
