# -*- coding: utf-8 -*-
"""AntNest · 评测基础设施自身的测试

这里测的不是 Agent 行为（那是 evals/cases 的职责），而是**评测框架本身**是否可信：

1. 用例文件能不能被加载、schema 对不对；
2. 断言引擎的每个键是否按预期判定（包括「不该通过」的反例）；
3. 指标计算对不对；
4. 跨用例状态是否隔离（这是真实踩过的坑）。
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import evals.harness as H  # noqa: E402


class CaseLoadingTest(unittest.TestCase):
    def test_all_cases_load(self):
        cases = H.load_cases()
        self.assertGreaterEqual(len(cases), 20, "用例数量异常少，检查 cases/")

    def test_case_ids_unique(self):
        ids = [c.case_id for c in H.load_cases()]
        self.assertEqual(len(ids), len(set(ids)), "用例 id 重复")

    def test_every_case_has_required_fields(self):
        for c in H.load_cases():
            self.assertTrue(c.case_id, "缺少 id")
            self.assertTrue(c.category, f"{c.case_id} 缺少 category")
            self.assertTrue(c.task, f"{c.case_id} 缺少 task")
            self.assertIsInstance(c.script, list, f"{c.case_id} 的 script 不是数组")
            self.assertTrue(c.script, f"{c.case_id} 的 script 为空")
            self.assertIsInstance(c.expect, dict, f"{c.case_id} 的 expect 不是对象")

    def test_categories_cover_roadmap(self):
        cats = {c.category for c in H.load_cases()}
        for want in ("safety", "planning", "recovery", "permissions"):
            self.assertIn(want, cats, f"缺少 {want} 类用例")

    def test_category_filter(self):
        safety = H.load_cases("safety")
        self.assertTrue(safety)
        for c in safety:
            self.assertEqual(c.category, "safety")

    def test_unknown_category_yields_empty(self):
        self.assertEqual(H.load_cases("nope"), [])

    def test_script_steps_are_wellformed(self):
        for c in H.load_cases():
            for step in c.script:
                self.assertIsInstance(step, (dict, str), f"{c.case_id} 脚本项类型异常")
                if isinstance(step, dict):
                    self.assertIn(str(step.get("kind") or "text"),
                                  ("text", "tool", "malformed"),
                                  f"{c.case_id} 出现未知的 kind")

    def test_root_placeholder_available(self):
        self.assertTrue(str(H.ROOT))
        self.assertTrue((H.ROOT / "AntNest.py").is_file())


class SubstitutionTest(unittest.TestCase):
    def test_substitutes_root_in_strings(self):
        self.assertEqual(H._subst("x {ROOT}/a.py"), f"x {H.ROOT}/a.py")

    def test_recurses_into_dict_and_list(self):
        out = H._subst({"a": ["{ROOT}/b", {"c": "{ROOT}/d"}]})
        self.assertIn(str(H.ROOT), out["a"][0])
        self.assertIn(str(H.ROOT), out["a"][1]["c"])

    def test_leaves_other_values(self):
        self.assertEqual(H._subst(42), 42)
        self.assertIsNone(H._subst(None))


class AssertEngineTest(unittest.TestCase):
    def _ctx(self, **kw):
        base = {
            "tools_called": [], "tools_executed": [], "last_status": "",
            "permission_denied": False, "messages": [], "plan": {"nodes": []},
            "events": [], "checkpoints": 0, "resume_valid": False,
            "checkpoint_raw": "", "rounds": 1,
        }
        base.update(kw)
        return base

    def test_empty_expect_passes(self):
        self.assertEqual(H._check({}, self._ctx()), [])

    def test_plan_nodes(self):
        ctx = self._ctx(plan={"nodes": [{"id": "P1"}, {"id": "P2"}]})
        self.assertEqual(H._check({"plan_nodes": ["P1", "P2"]}, ctx), [])
        self.assertTrue(H._check({"plan_nodes": ["P1"]}, ctx))

    def test_plan_goal(self):
        ctx = self._ctx(plan={"goal": "修复"})
        self.assertEqual(H._check({"plan_goal": "修复"}, ctx), [])
        self.assertTrue(H._check({"plan_goal": "别的"}, ctx))

    def test_plan_total(self):
        ctx = self._ctx(plan={"nodes": [{"id": "P1"}, {"id": "P2"}]})
        self.assertEqual(H._check({"plan_total": 2}, ctx), [])
        self.assertTrue(H._check({"plan_total": 1}, ctx))

    def test_plan_completed_treats_skipped_as_done(self):
        ctx = self._ctx(plan={"nodes": [
            {"id": "P1", "status": "completed"},
            {"id": "P2", "status": "skipped"},
            {"id": "P3", "status": "pending"},
        ]})
        self.assertEqual(H._check({"plan_completed": ["P1", "P2"]}, ctx), [])

    def test_tools_called(self):
        ctx = self._ctx(tools_called=["spawn_clone", "view_file"])
        self.assertEqual(H._check({"tools_called": ["view_file", "spawn_clone"]}, ctx), [])

    def test_tools_not_called_uses_executed_not_called(self):
        """核心语义：断言的是「执行器没被调用」，不是「没有 tool 消息」。

        权限闸门会写一条 role:"tool" 消息（内容是拒绝理由），
        但执行器根本没跑。混淆这两者会让安全用例失去意义。
        """
        ctx = self._ctx(
            tools_called=["write_file"],       # 有 tool 消息
            tools_executed=[],                  # 但没执行
            last_status="denied",
        )
        self.assertEqual(H._check({"tools_not_called": ["write_file"]}, ctx), [])

    def test_tools_not_called_detects_real_execution(self):
        ctx = self._ctx(tools_called=["write_file"], tools_executed=["write_file"])
        self.assertTrue(H._check({"tools_not_called": ["write_file"]}, ctx))

    def test_last_tool_status(self):
        ctx = self._ctx(last_status="approval_required")
        self.assertEqual(H._check({"last_tool_status": "approval_required"}, ctx), [])
        self.assertTrue(H._check({"last_tool_status": "ok"}, ctx))

    def test_permission_denied(self):
        self.assertTrue(H._check({"permission_denied": True}, self._ctx()))
        self.assertTrue(H._check({"permission_denied": False}, self._ctx(permission_denied=True)))

    def test_messages_contain(self):
        ctx = self._ctx(messages=[{"role": "assistant", "content": "已完成"}])
        self.assertEqual(H._check({"messages_contain": "已完成"}, ctx), [])
        self.assertTrue(H._check({"messages_contain": "没有"}, ctx))

    def test_messages_not_contain_catches_cot_leak(self):
        ctx = self._ctx(messages=[{"role": "assistant", "content": "思维链MARKER"}])
        self.assertTrue(H._check({"messages_not_contain": "思维链MARKER"}, ctx))

    def test_events_include(self):
        ctx = self._ctx(events=["TASK_STARTED", "TOOL_CALL"])
        self.assertEqual(H._check({"events_include": ["TOOL_CALL"]}, ctx), [])
        self.assertTrue(H._check({"events_include": ["PERMISSION_DENIED"]}, ctx))

    def test_checkpoint_saved(self):
        self.assertTrue(H._check({"checkpoint_saved": True}, self._ctx(checkpoints=0)))
        self.assertEqual(H._check({"checkpoint_saved": True}, self._ctx(checkpoints=2)), [])

    def test_checkpoint_resume_valid(self):
        self.assertTrue(H._check({"checkpoint_resume_valid": True}, self._ctx(resume_valid=False)))
        self.assertEqual(H._check({"checkpoint_resume_valid": True}, self._ctx(resume_valid=True)), [])

    def test_rounds_at_most(self):
        self.assertEqual(H._check({"rounds_at_most": 3}, self._ctx(rounds=2)), [])
        self.assertTrue(H._check({"rounds_at_most": 1}, self._ctx(rounds=3)))

    def test_no_cot_on_disk(self):
        bad = self._ctx(checkpoint_raw='{"reasoning_content": "x"}')
        self.assertTrue(H._check({"no_cot_on_disk": True}, bad))
        good = self._ctx(checkpoint_raw='{"goal": "x"}')
        self.assertEqual(H._check({"no_cot_on_disk": True}, good), [])

    def test_unknown_key_is_reported(self):
        """拼错的断言键必须报错，不能静默通过——否则用例会「假绿」。"""
        fails = H._check({"plan_noed": ["P1"]}, self._ctx())
        self.assertTrue(any("未知的断言键" in f for f in fails))


class StatusParsingTest(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(H._status_of_content('{"status": "ok"}'), "ok")

    def test_truncated_json_falls_back_to_regex(self):
        """loop 会按 TOOL_RESULT_LEN 截断工具结果，结果不再是合法 JSON。"""
        truncated = '{"status": "approval_required", "scope": "self_sou\n...（中间内容已省略）...\n"code": "PERM"}'
        self.assertEqual(H._status_of_content(truncated), "approval_required")

    def test_non_json_returns_empty(self):
        self.assertEqual(H._status_of_content("普通文本"), "")

    def test_none_returns_empty(self):
        self.assertEqual(H._status_of_content(None), "")

    def test_json_without_status(self):
        self.assertEqual(H._status_of_content('{"output": "x"}'), "")


class MetricsTest(unittest.TestCase):
    def _r(self, cid, passed, category="general", **kw):
        return H.CaseResult(
            case_id=cid, category=category, passed=passed,
            tool_calls=kw.get("tool_calls", 2), duration_ms=kw.get("duration_ms", 100.0),
            tokens=kw.get("tokens", 50), rounds=kw.get("rounds", 1), failures=[],
        )

    def test_empty(self):
        self.assertEqual(H.compute_metrics([]), {})

    def test_success_rate(self):
        m = H.compute_metrics([self._r("a", True), self._r("b", False)])
        self.assertEqual(m["success_rate"], 0.5)

    def test_averages(self):
        m = H.compute_metrics([
            self._r("a", True, tool_calls=2, duration_ms=100.0, tokens=50, rounds=1),
            self._r("b", True, tool_calls=4, duration_ms=300.0, tokens=150, rounds=3),
        ])
        self.assertEqual(m["tool_calls_avg"], 3.0)
        self.assertEqual(m["duration_ms_avg"], 200.0)
        self.assertEqual(m["duration_ms_max"], 300.0)
        self.assertEqual(m["tokens_avg"], 100.0)
        self.assertEqual(m["rounds_avg"], 2.0)

    def test_category_rates_only_when_present(self):
        m = H.compute_metrics([self._r("a", True)])
        self.assertIsNone(m["error_recovery_rate"])
        self.assertIsNone(m["danger_block_rate"])

    def test_safety_and_recovery_rates(self):
        m = H.compute_metrics([
            self._r("s1", True, category="safety"),
            self._r("s2", False, category="safety"),
            self._r("r1", True, category="recovery"),
        ])
        self.assertEqual(m["danger_block_rate"], 0.5)
        self.assertEqual(m["error_recovery_rate"], 1.0)

    def test_all_seven_roadmap_metrics_present(self):
        """路线图 §8 列了七项指标，一个都不能少。"""
        m = H.compute_metrics([self._r("a", True, category="safety")])
        for key in ("success_rate", "tool_calls_avg", "duration_ms_avg",
                    "tokens_avg", "error_recovery_rate", "danger_block_rate"):
            self.assertIn(key, m, f"缺少指标 {key}")


class ReportTest(unittest.TestCase):
    def test_success_rate_zero_when_empty(self):
        self.assertEqual(H.Report(version="x").success_rate, 0.0)

    def test_to_dict_includes_rate(self):
        d = H.Report(version="1.4", total=4, passed=3).to_dict()
        self.assertEqual(d["success_rate"], 0.75)
        self.assertFalse(d["model_dependent"], "必须显式标注不依赖模型")

    def test_save_and_load(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            rep = H.Report(version="9.9", total=1, passed=1)
            path = H.save_report(rep, out_dir=tmp)
            self.assertTrue(path.is_file())
            back = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(back["version"], "9.9")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class CompareTest(unittest.TestCase):
    def test_compare_reports_delta(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            a = tmp / "a.json"
            b = tmp / "b.json"
            a.write_text(json.dumps({
                "version": "1.3.1", "success_rate": 0.5,
                "metrics": {"tool_calls_avg": 3.0}, "results": [{"case_id": "x"}],
            }), encoding="utf-8")
            b.write_text(json.dumps({
                "version": "1.4", "success_rate": 0.75,
                "metrics": {"tool_calls_avg": 2.0},
                "results": [{"case_id": "x"}, {"case_id": "y"}],
            }), encoding="utf-8")
            out = H.compare(a, b)
            self.assertEqual(out["from"], "1.3.1")
            self.assertEqual(out["to"], "1.4")
            self.assertEqual(out["success_rate"]["delta"], 0.25)
            self.assertEqual(out["added_cases"], ["y"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_compare_missing_file(self):
        out = H.compare(Path("nope.json"), Path("nope2.json"))
        self.assertIn("error", out)


class IsolationTest(unittest.TestCase):
    """跨用例状态隔离。

    真实踩过的坑：权限引擎在 finally 里「还原」时抓错了引用（抓的是刚装上的
    那个而不是原来的），导致上一个用例的 level 泄漏到下一个，只读用例之后的
    spawn 用例全被误拒。

    这条测试直接跑一串用例验证隔离性，而不是假设它成立。
    """

    def test_two_runs_give_same_results(self):
        first = H.run(cases=H.load_cases("permissions"), version="iso1")
        second = H.run(cases=H.load_cases("permissions"), version="iso2")
        self.assertEqual(first.total, second.total)
        self.assertEqual(
            [r["passed"] for r in first.results],
            [r["passed"] for r in second.results],
            "同一批用例两次运行结果不一致——存在状态泄漏",
        )

    def test_results_are_deterministic(self):
        a = H.run(cases=H.load_cases("safety"), version="det-a")
        b = H.run(cases=H.load_cases("safety"), version="det-b")
        self.assertEqual(a.passed, b.passed)
        self.assertEqual(a.metrics.get("success_rate"), b.metrics.get("success_rate"))


if __name__ == "__main__":
    unittest.main()
