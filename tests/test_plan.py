# -*- coding: utf-8 -*-
"""AntNest · 计划 DAG 测试

重点是三个不可让步的性质：

  1. **环必须被拒绝，且原计划保持完整**。半个新计划比旧计划更糟。
  2. **自动派生必须有兜底**。只依赖 LLM 主动调 update_plan，flash 类模型
     不按格式调工具时 Plan 面板会是空的，整个特性等于没做。
  3. **计划不能凭空造出**——任何 spawn 都要落在某个节点上，但不能多造。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import antnest_events as ev  # noqa: E402
import antnest_plan as plan  # noqa: E402
from antnest_plan import NodeStatus, Plan, PlanError  # noqa: E402


def _reset() -> None:
    d = tempfile.mkdtemp(prefix="antnest_plan_")
    ev.reset(d)
    plan.reset("测试目标")


class PlanBasicsTest(unittest.TestCase):
    def setUp(self):
        _reset()
        self.p = plan.current()

    def test_new_plan_is_empty_but_usable(self):
        d = self.p.to_dict()
        self.assertEqual(d["nodes"], [])
        self.assertEqual(d["progress"]["total"], 0)
        self.assertEqual(self.p.to_prompt(), "", "空计划不应产出提示词段落")

    def test_plan_id_shape(self):
        self.assertTrue(self.p.plan_id.startswith("plan_"))

    def test_add_node_auto_ids(self):
        a = self.p.add_node("扫描")
        b = self.p.add_node("测试")
        self.assertEqual((a.id, b.id), ("P1", "P2"))

    def test_node_limit(self):
        for _ in range(Plan.MAX_NODES):
            self.p.add_node("x")
        with self.assertRaises(PlanError):
            self.p.add_node("y")

    def test_progress_counts(self):
        self.p.replace("g", [
            {"id": "A", "title": "a"},
            {"id": "B", "title": "b", "depends_on": ["A"]},
            {"id": "C", "title": "c", "depends_on": ["A"]},
        ])
        self.p.mark_running("A")
        self.p.mark_completed("A")
        self.p.mark_failed("B", "boom")
        p = self.p.progress()
        self.assertEqual(p["total"], 3)
        self.assertEqual(p["completed"], 1)
        self.assertEqual(p["failed"], 1)
        self.assertEqual(p["percent"], 33)

    def test_mark_unknown_node_is_noop(self):
        self.p.mark_completed("NOPE")
        self.p.mark_failed("NOPE", "x")
        self.p.mark_running("NOPE")
        self.assertEqual(self.p.progress()["total"], 0)


class DependencyTest(unittest.TestCase):
    def setUp(self):
        _reset()
        self.p = plan.current()
        self.p.replace("修复测试失败", [
            {"id": "P1", "title": "扫描项目"},
            {"id": "P2", "title": "分析依赖", "depends_on": ["P1"]},
            {"id": "P3", "title": "执行测试", "depends_on": ["P1", "P2"]},
            {"id": "P4", "title": "修复", "depends_on": ["P3"]},
        ])

    def test_ready_respects_dependencies(self):
        self.assertEqual([n.id for n in self.p.ready_nodes()], ["P1"])
        self.p.mark_completed("P1")
        self.assertEqual([n.id for n in self.p.ready_nodes()], ["P2"])
        self.p.mark_completed("P2")
        self.assertEqual([n.id for n in self.p.ready_nodes()], ["P3"])
        self.p.mark_completed("P3")
        self.assertEqual([n.id for n in self.p.ready_nodes()], ["P4"])

    def test_independent_nodes_become_ready_together(self):
        p2 = Plan()
        p2.replace("g", [
            {"id": "A", "title": "a"},
            {"id": "B", "title": "b"},
        ])
        self.assertEqual([n.id for n in p2.ready_nodes()], ["A", "B"])

    def test_skipped_counts_as_done(self):
        p2 = Plan()
        p2.replace("g", [
            {"id": "A", "title": "a"},
            {"id": "B", "title": "b", "depends_on": ["A"]},
        ])
        node = p2.node("A")
        node.status = NodeStatus.SKIPPED
        self.assertEqual([n.id for n in p2.ready_nodes()], ["B"])

    def test_failed_does_not_unblock(self):
        p2 = Plan()
        p2.replace("g", [
            {"id": "A", "title": "a"},
            {"id": "B", "title": "b", "depends_on": ["A"]},
        ])
        p2.mark_failed("A", "boom")
        self.assertEqual(p2.ready_nodes(), [], "失败节点不应解锁后继")

    def test_groups_layer_by_depth(self):
        layers = self.p.groups()
        self.assertEqual([[n.id for n in l] for l in layers],
                         [["P1"], ["P2"], ["P3"], ["P4"]])


class CycleRejectionTest(unittest.TestCase):
    """环必须被整体拒绝，原计划保持完整。"""

    def setUp(self):
        _reset()
        self.p = plan.current()
        self.p.replace("原目标", [
            {"id": "P1", "title": "a"},
            {"id": "P2", "title": "b", "depends_on": ["P1"]},
        ])

    def test_direct_cycle_rejected(self):
        with self.assertRaises(PlanError) as ctx:
            self.p.replace("坏", [
                {"id": "A", "title": "a", "depends_on": ["B"]},
                {"id": "B", "title": "b", "depends_on": ["A"]},
            ])
        self.assertIn("循环依赖", str(ctx.exception))

    def test_long_cycle_rejected(self):
        with self.assertRaises(PlanError):
            self.p.replace("坏", [
                {"id": "A", "title": "a", "depends_on": ["C"]},
                {"id": "B", "title": "b", "depends_on": ["A"]},
                {"id": "C", "title": "c", "depends_on": ["B"]},
            ])

    def test_self_cycle_rejected(self):
        with self.assertRaises(PlanError):
            self.p.replace("坏", [{"id": "A", "title": "a", "depends_on": ["A"]}])

    def test_plan_intact_after_rejection(self):
        before = self.p.to_dict()
        with self.assertRaises(PlanError):
            self.p.replace("坏", [
                {"id": "A", "title": "a", "depends_on": ["B"]},
                {"id": "B", "title": "b", "depends_on": ["A"]},
            ])
        after = self.p.to_dict()
        self.assertEqual(before["nodes"], after["nodes"], "被拒的更新污染了原计划")
        self.assertEqual(before["goal"], after["goal"])

    def test_missing_dependency_rejected(self):
        with self.assertRaises(PlanError) as ctx:
            self.p.replace("坏", [{"id": "A", "title": "a", "depends_on": ["GHOST"]}])
        self.assertIn("GHOST", str(ctx.exception))

    def test_duplicate_id_rejected(self):
        with self.assertRaises(PlanError):
            self.p.replace("坏", [
                {"id": "A", "title": "a"},
                {"id": "A", "title": "again"},
            ])

    def test_missing_title_rejected(self):
        with self.assertRaises(PlanError):
            self.p.replace("坏", [{"id": "A", "title": "  "}])

    def test_has_cycle_detects_without_raising(self):
        p2 = Plan()
        p2.replace("g", [{"id": "A", "title": "a"}])
        self.assertIsNone(p2.has_cycle())
        p2.nodes[0].depends_on = ["A"]
        self.assertEqual(p2.has_cycle(), ["A", "A"])


class ProgressPreservationTest(unittest.TestCase):
    def setUp(self):
        _reset()
        self.p = plan.current()

    def test_rebuild_keeps_completed_state(self):
        """LLM 重建计划时不能抹掉已完成的进度。"""
        self.p.replace("g", [
            {"id": "A", "title": "a"},
            {"id": "B", "title": "b", "depends_on": ["A"]},
        ])
        self.p.mark_running("A", worker_id=1)
        self.p.mark_completed("A", "183 files")
        self.p.replace("g 修订", [
            {"id": "A", "title": "a（改名）"},
            {"id": "B", "title": "b", "depends_on": ["A"]},
            {"id": "C", "title": "c", "depends_on": ["B"]},
        ])
        node = self.p.node("A")
        self.assertEqual(node.status, NodeStatus.COMPLETED)
        self.assertEqual(node.result_summary, "183 files")
        self.assertEqual(node.worker_ids, [1])
        self.assertEqual(self.p.node("A").title, "a（改名）")

    def test_explicit_flag_set_on_replace(self):
        self.assertFalse(self.p.explicit)
        self.p.replace("g", [{"id": "A", "title": "a"}])
        self.assertTrue(self.p.explicit)

    def test_add_node_does_not_set_explicit(self):
        self.p.add_node("自动派生")
        self.assertFalse(self.p.explicit, "自动派生不应被当成 LLM 显式规划")


class WorkerBindingTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_bind_and_lookup_by_task_id(self):
        p = plan.current()
        p.add_node("扫描")
        p.bind_worker("task_abc", "P1")
        self.assertEqual(p.node_for_task("task_abc").id, "P1")

    def test_unknown_task_returns_none(self):
        self.assertIsNone(plan.current().node_for_task("nope"))

    def test_complete_via_task_id(self):
        p = plan.current()
        node = plan.ensure_node_for_spawn("扫描", task_id="t1", worker_id=7)
        self.assertIsNotNone(node)
        self.assertEqual(node.status, NodeStatus.RUNNING)
        self.assertEqual(node.worker_ids, [7])
        done = plan.complete_node_for_spawn("t1", "ok", "183 files")
        self.assertEqual(done.status, NodeStatus.COMPLETED)
        self.assertEqual(done.result_summary, "183 files")

    def test_fail_via_task_id(self):
        plan.ensure_node_for_spawn("扫描", task_id="t1")
        node = plan.complete_node_for_spawn("t1", "timeout", "超时")
        self.assertEqual(node.status, NodeStatus.FAILED)
        self.assertIn("超时", node.error)

    def test_blocked_status_maps_to_failed(self):
        plan.ensure_node_for_spawn("扫描", task_id="t1")
        node = plan.complete_node_for_spawn("t1", "blocked", "深度限制")
        self.assertEqual(node.status, NodeStatus.FAILED)

    def test_fail_running_marks_all(self):
        p = plan.current()
        p.add_node("a")
        p.add_node("b")
        p.mark_running("P1")
        p.mark_running("P2")
        ids = p.fail_running("回合被取消")
        self.assertEqual(ids, ["P1", "P2"])
        self.assertEqual(p.progress()["failed"], 2)


class AutoDeriveTest(unittest.TestCase):
    """自动派生兜底。"""

    def setUp(self):
        _reset()

    def test_spawn_without_explicit_plan_creates_node(self):
        p = plan.current()
        self.assertEqual(p.progress()["total"], 0)
        node = plan.ensure_node_for_spawn("扫描项目")
        self.assertIsNotNone(node)
        self.assertEqual(p.progress()["total"], 1)
        self.assertEqual(node.title, "扫描项目")

    def test_second_spawn_reuses_running_node(self):
        p = plan.current()
        a = plan.ensure_node_for_spawn("扫描")
        b = plan.ensure_node_for_spawn("测试")
        self.assertEqual(a.id, b.id, "已有 RUNNING 节点时应复用，不该 proliferation")
        self.assertEqual(p.progress()["total"], 1)

    def test_after_completion_next_spawn_opens_new_node(self):
        p = plan.current()
        a = plan.ensure_node_for_spawn("扫描", task_id="t1")
        plan.complete_node_for_spawn("t1", "ok")
        b = plan.ensure_node_for_spawn("测试")
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(p.progress()["total"], 2)

    def test_explicit_plan_is_respected(self):
        p = plan.current()
        p.replace("g", [
            {"id": "P1", "title": "扫描"},
            {"id": "P2", "title": "测试", "depends_on": ["P1"]},
        ])
        node = plan.ensure_node_for_spawn("随便什么 label")
        self.assertEqual(node.id, "P1", "显式规划下应取 ready 的第一个节点")
        self.assertEqual(p.progress()["total"], 2, "显式规划下不应新增节点")

    def test_explicit_plan_advances_through_dependencies(self):
        p = plan.current()
        p.replace("g", [
            {"id": "P1", "title": "扫描"},
            {"id": "P2", "title": "测试", "depends_on": ["P1"]},
        ])
        plan.ensure_node_for_spawn("x", task_id="t1")
        plan.complete_node_for_spawn("t1", "ok")
        node = plan.ensure_node_for_spawn("y", task_id="t2")
        self.assertEqual(node.id, "P2")

    def test_derive_survives_node_limit(self):
        p = plan.current()
        for _ in range(Plan.MAX_NODES):
            p.add_node("x")
        p.mark_completed("P1")
        self.assertIsNone(plan.ensure_node_for_spawn("x"), "超限时不应抛异常")


class UpdatePlanToolTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_valid_submission(self):
        out = json.loads(plan.update_plan(
            goal="修复测试失败",
            nodes=json.dumps([
                {"id": "P1", "title": "扫描项目"},
                {"id": "P2", "title": "跑测试", "depends_on": ["P1"]},
            ]),
        ))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["nodes"], ["P1", "P2"])
        self.assertEqual(out["progress"]["total"], 2)
        self.assertTrue(plan.current().explicit)

    def test_empty_submission_clears(self):
        plan.update_plan(goal="g", nodes=json.dumps([{"id": "A", "title": "a"}]))
        out = json.loads(plan.update_plan(goal="g2", nodes="[]"))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(plan.current().progress()["total"], 0)

    def test_malformed_json_rejected(self):
        out = json.loads(plan.update_plan(nodes="{not json"))
        self.assertEqual(out["status"], "error")
        self.assertIn("JSON", out["error"])

    def test_non_array_rejected(self):
        out = json.loads(plan.update_plan(nodes='{"id":"A"}'))
        self.assertEqual(out["status"], "error")

    def test_cycle_rejected_with_reason(self):
        out = json.loads(plan.update_plan(nodes=json.dumps([
            {"id": "A", "title": "a", "depends_on": ["B"]},
            {"id": "B", "title": "b", "depends_on": ["A"]},
        ])))
        self.assertEqual(out["status"], "error")
        self.assertIn("循环依赖", out["error"])
        self.assertIn("hint", out, "被拒时应告诉模型怎么改")

    def test_error_result_is_not_approval_required(self):
        """update_plan 不涉及权限，被拒时不能返回 approval_required，
        否则 loop 会误以为需要用户授权，把批次打断。"""
        out = json.loads(plan.update_plan(nodes="[bad json"))
        self.assertNotIn(out["status"], ("approval_required", "denied"))


class SnapshotRestoreTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_roundtrip(self):
        p = plan.current()
        p.replace("修复测试失败", [
            {"id": "P1", "title": "扫描"},
            {"id": "P2", "title": "测试", "depends_on": ["P1"]},
        ])
        p.mark_running("P1", worker_id=3)
        snap = p.to_dict()

        restored = plan.restore(snap)
        self.assertEqual(restored.goal, "修复测试失败")
        self.assertEqual([n.id for n in restored.nodes], ["P1", "P2"])
        self.assertEqual(restored.node("P1").status, NodeStatus.RUNNING)
        self.assertEqual(restored.node("P1").worker_ids, [3])
        self.assertEqual(restored.node("P2").depends_on, ["P1"])
        self.assertTrue(restored.explicit)

    def test_restore_tolerates_garbage(self):
        restored = plan.restore({"nodes": [{"bad": 1}, "not a dict", None]})
        self.assertIsInstance(restored, Plan)
        self.assertEqual(restored.progress()["total"], 0)

    def test_restore_with_empty_dict(self):
        restored = plan.restore({})
        self.assertEqual(restored.progress()["total"], 0)

    def test_unknown_status_falls_back_to_pending(self):
        p = plan.restore({"nodes": [{"id": "A", "title": "a", "status": "bogus"}]})
        self.assertEqual(p.node("A").status, NodeStatus.PENDING)

    def test_restore_replaces_current(self):
        plan.current().add_node("旧的")
        plan.restore({"nodes": [{"id": "NEW", "title": "新的"}]})
        self.assertEqual([n.id for n in plan.current().nodes], ["NEW"])


class PromptTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_prompt_shape(self):
        p = plan.current()
        p.replace("修复测试失败", [
            {"id": "P1", "title": "扫描项目"},
            {"id": "P2", "title": "执行测试", "depends_on": ["P1"]},
        ])
        text = p.to_prompt()
        self.assertIn("修复测试失败", text)
        self.assertIn("P1", text)
        self.assertIn("扫描项目", text)
        self.assertIn("← P1", text)
        self.assertIn("○", text)

    def test_summary_shape(self):
        p = plan.current()
        p.replace("g", [{"id": "A", "title": "a"}])
        p.mark_completed("A")
        self.assertIn("1/1", p.summary())


class NodeStatusTest(unittest.TestCase):
    def test_glyphs_distinct(self):
        glyphs = [s.glyph for s in NodeStatus]
        self.assertEqual(len(glyphs), len(set(glyphs)), "状态字形需可区分")

    def test_done_semantics(self):
        self.assertTrue(NodeStatus.COMPLETED.done)
        self.assertTrue(NodeStatus.SKIPPED.done)
        for s in (NodeStatus.PENDING, NodeStatus.RUNNING, NodeStatus.FAILED, NodeStatus.BLOCKED):
            self.assertFalse(s.done, f"{s} 不应算完成")


if __name__ == "__main__":
    unittest.main()
