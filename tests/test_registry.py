# -*- coding: utf-8 -*-
"""AntNest · 工具注册表测试

注册表的价值全在「不腐烂」。所以本文件的主要断言不是「某个函数返回了什么」，
而是**四种派生物之间必须保持一致**，以及 prompt 里的工具清单不能和注册表脱节
（历史上 `prompts/queen_system.md` 与 `antnest_loop` 的正则都已经烂了）。
"""
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import antnest_registry as reg  # noqa: E402
from antnest_permissions import PermLevel  # noqa: E402


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


class SpecIntegrityTest(unittest.TestCase):
    def test_names_unique_and_wellformed(self):
        names = reg.all_names()
        self.assertEqual(len(names), len(set(names)))
        for n in names:
            self.assertRegex(n, r"^[a-z][a-z0-9_]*$")

    def test_levels_assigned(self):
        for spec in reg.TOOL_SPECS:
            self.assertIsInstance(spec.level, PermLevel, f"{spec.name} 未分配等级")

    def test_every_spec_has_summary(self):
        for spec in reg.TOOL_SPECS:
            self.assertTrue(spec.summary, f"{spec.name} 缺少 summary")

    def test_no_duplicate_schema_attr(self):
        attrs = [s.schema_attr for s in reg.TOOL_SPECS]
        self.assertEqual(len(attrs), len(set(attrs)))

    def test_no_duplicate_executor_attr(self):
        attrs = [s.executor_attr for s in reg.TOOL_SPECS]
        self.assertEqual(len(attrs), len(set(attrs)))


class VisibilityTest(unittest.TestCase):
    def test_normal_excludes_worker_only(self):
        names = {s.name for s in reg.exposed_specs()}
        self.assertNotIn("run_cli", names)
        self.assertNotIn("run_python", names)

    def test_normal_excludes_mcp_when_off(self):
        names = {s.name for s in reg.exposed_specs(mcp_on=False)}
        self.assertNotIn("mcp_call", names)
        self.assertNotIn("mcp_list_tools", names)

    def test_mcp_included_when_on(self):
        names = {s.name for s in reg.exposed_specs(mcp_on=True)}
        self.assertIn("mcp_call", names)
        self.assertIn("mcp_list_tools", names)

    def test_compact_panic_keeps_only_two(self):
        names = [s.name for s in reg.exposed_specs(compact_panic=True)]
        self.assertEqual(names, ["spawn_clone", "leave_memory_hints"])

    def test_leave_memory_hints_is_panic_only(self):
        """回归：常态下不得暴露 leave_memory_hints。

        它的实现要求 messages 里存在 COMPACT_PROMPT 标记
        （antnest_memory.leave_memory_hints:59-66），常态暴露只会让模型反复调用
        后拿到错误，并可能干扰正常压缩流程。v1.4 接线时曾一度把它暴露到常态，
        由本用例钉死。
        """
        self.assertTrue(reg.get("leave_memory_hints").panic_only)
        normal = {s.name for s in reg.exposed_specs()}
        self.assertNotIn("leave_memory_hints", normal)
        self.assertIn("leave_memory_hints", {s.name for s in reg.exposed_specs(compact_panic=True)})

    def test_visibility_is_identical_to_v131_handwritten_list(self):
        """回归：派生结果必须与 v1.3.1 的手写列表逐项相等。

        v1.3.1 的 antnest_queen.get_queen_tools 常态返回这 11 个（MCP 关闭时）。
        派生逻辑若无意改动工具可见性，会直接改变每回合发给 LLM 的 tools 数组，
        进而改变模型行为——这类漂移很难在运行时察觉。
        """
        legacy = [
            "spawn_clone", "get_task_status", "view_file", "list_dir",
            "grep_files", "write_file", "search_replace", "web_fetch",
            "register_tool", "list_tools", "get_tool_source",
        ]
        got = [s.name for s in reg.exposed_specs(mcp_on=False)]
        self.assertEqual(
            set(got), set(legacy),
            f"常态可见集合变了：新增 {set(got) - set(legacy)}，消失 {set(legacy) - set(got)}",
        )

    def test_legacy_order_is_preserved_when_sorted_by_first_use(self):
        """顺序不参与 LLM 语义，但保持与旧列表一致便于人工 diff。"""
        legacy = [
            "spawn_clone", "get_task_status", "view_file", "list_dir",
            "grep_files", "write_file", "search_replace", "web_fetch",
            "register_tool", "list_tools", "get_tool_source",
        ]
        self.assertEqual([s.name for s in reg.exposed_specs(mcp_on=False)], legacy)

    def test_registry_order_is_stable(self):
        first = [s.name for s in reg.exposed_specs(mcp_on=True)]
        second = [s.name for s in reg.exposed_specs(mcp_on=True)]
        self.assertEqual(first, second)


class MalformedPatternTest(unittest.TestCase):
    def test_covers_all_specs_not_just_exposed(self):
        """刻意宽于 exposed 集合。

        历史上手写正则是 13 个（比 exposed 的 11 个多）。若按 exposed 收窄，
        leave_memory_hints 会失去检测——而它只在 COMPACT_PANIC 下暴露，模型
        可以用纯文本伪造它的 JSON 调用绕过压缩。
        """
        pattern = reg.malformed_call_pattern()
        for spec in reg.TOOL_SPECS:
            self.assertIn(spec.name, pattern)

    def test_regex_detects_plaintext_fake_call(self):
        rx = reg.malformed_call_regex()
        self.assertTrue(rx.search('{"name": "spawn_clone", "arguments": {"command":"x"}}'))
        self.assertTrue(rx.search('{"NAME":"WRITE_FILE","ARGUMENTS":{}}'))

    def test_regex_ignores_prose(self):
        rx = reg.malformed_call_regex()
        self.assertIsNone(rx.search('我想调用 spawn_clone 工具去看看文件'))
        self.assertIsNone(rx.search('{"name": "unknown_tool", "arguments": {}}'))


class PromptCatalogTest(unittest.TestCase):
    def test_lists_every_exposed_tool(self):
        text = reg.prompt_catalog(mcp_on=True)
        for spec in reg.exposed_specs(mcp_on=True):
            self.assertIn(spec.name, text, f"prompt 目录漏了 {spec.name}")

    def test_omits_worker_only_tools(self):
        text = reg.prompt_catalog(mcp_on=True)
        self.assertNotIn("- run_cli：", text)
        self.assertNotIn("- run_python：", text)

    def test_panic_catalog_is_explicit(self):
        text = reg.prompt_catalog(compact_panic=True)
        self.assertIn("压缩", text)


class QueenPromptFileTest(unittest.TestCase):
    """prompts/queen_system.md 的工具清单必须与注册表一致。

    这是「prompt 漂移」的唯一防线。历史上该文件少列了
    register_tool / list_tools / get_tool_source 三项，模型因此不知道工坊存在。
    """

    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / "prompts" / "queen_system.md").read_text(encoding="utf-8")
        cls.section = cls.text.split("# 工具调用说明", 1)[-1].split("\n# ", 1)[0]

    def test_every_exposed_tool_documented(self):
        for name in reg.prompt_catalog_lines():
            self.assertIn(
                name, self.section,
                f"queen_system.md 的工具清单缺少 {name}（注册表里有）",
            )

    def test_no_undocumented_exposed_tools(self):
        # 反向：prompt 里宣称可用、但注册表里没有的工具会让模型白调用一次
        declared = set(reg.prompt_catalog_lines())
        for spec in reg.TOOL_SPECS:
            if not spec.queen_visible:
                continue
            self.assertIn(spec.name, declared)

    def test_denied_status_documented(self):
        """prompt 必须解释新词汇，否则模型会把 denied 当成工具坏了然后重试。"""
        self.assertIn("denied", self.text)


class NoHandwrittenEnumerationTest(unittest.TestCase):
    """源码里不得再出现手写的工具名枚举。"""

    def test_loop_has_no_hardcoded_tool_alternation(self):
        src = (ROOT / "antnest_loop.py").read_text(encoding="utf-8")
        self.assertIn("antnest_registry.malformed_call_regex", src)
        # 旧实现：spawn_clone|get_task_status|view_file|... 的长 alternation
        self.assertNotIn("spawn_clone|get_task_status", src)
        self.assertNotIn("spawn_clone|get_task_status|view_file", src)

    def test_queen_has_no_hardcoded_schema_list(self):
        src = (ROOT / "antnest_queen.py").read_text(encoding="utf-8")
        self.assertIn("antnest_registry.visible_schemas", src)
        self.assertNotIn("_A().spawn_clone_schema,", src)

    def test_shell_has_no_hardcoded_executor_dict(self):
        src = (ROOT / "AntNest.py").read_text(encoding="utf-8")
        self.assertIn("antnest_registry.build_executors", src)
        self.assertNotIn('"spawn_clone": spawn_clone,', src)


class AntNestRegistryWiringTest(unittest.TestCase):
    """接线后的实际一致性（需要真正 import AntNest）。"""

    @classmethod
    def setUpClass(cls):
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)

    def test_invariants_hold(self):
        problems = reg.registry_invariants(self.an)
        self.assertEqual(problems, [], "注册表不变量被破坏：\n" + "\n".join(problems))

    def test_tool_executors_covers_all_specs(self):
        for spec in reg.TOOL_SPECS:
            self.assertIn(spec.name, self.an.tool_executors, f"{spec.name} 缺执行器")

    def test_exposed_subset_of_executors(self):
        """反向不变量：有 schema 却没有执行器的工具会在 fail-closed 下被全部拒绝。"""
        for spec in reg.exposed_specs(mcp_on=True):
            self.assertIn(spec.name, self.an.tool_executors)

    def test_get_queen_tools_matches_registry(self):
        got = [t["function"]["name"] for t in self.an.get_queen_tools()]
        want = [s.name for s in reg.exposed_specs(mcp_on=False)]
        self.assertEqual(got, want, "get_queen_tools 与注册表不一致")

    def test_panic_tools_match_registry(self):
        got = [t["function"]["name"] for t in self.an.get_queen_tools(True)]
        want = [s.name for s in reg.exposed_specs(compact_panic=True)]
        self.assertEqual(got, want)

    def test_schema_names_match_spec_names(self):
        for tool in self.an.get_queen_tools():
            name = tool["function"]["name"]
            self.assertIsNotNone(reg.get(name), f"{name} 有 schema 但未登记")
            self.assertEqual(reg.get(name).name, name)

    def test_every_tool_is_callable(self):
        for name, fn in self.an.tool_executors.items():
            self.assertTrue(callable(fn), f"{name} 的执行器不可调用")

    def test_levels_are_plausible(self):
        """等级必须与工具实际行为相符，否则权限模型形同虚设。"""
        expect = {
            "view_file": PermLevel.READ,
            "list_dir": PermLevel.READ,
            "grep_files": PermLevel.READ,
            "write_file": PermLevel.WRITE,
            "search_replace": PermLevel.WRITE,
            "spawn_clone": PermLevel.EXECUTE,
            "run_cli": PermLevel.EXECUTE,
            "run_python": PermLevel.EXECUTE,
            "web_fetch": PermLevel.NETWORK,
            "mcp_call": PermLevel.NETWORK,
        }
        for name, level in expect.items():
            spec = reg.get(name)
            self.assertIsNotNone(spec, f"{name} 未登记")
            self.assertEqual(spec.level, level, f"{name} 等级标注错误")

    def test_mutating_flag_is_set_for_writers(self):
        for name in ("write_file", "search_replace", "spawn_clone", "register_tool"):
            self.assertTrue(reg.get(name).mutating, f"{name} 应标记为 mutating")
        for name in ("view_file", "list_dir", "grep_files", "get_task_status"):
            self.assertFalse(reg.get(name).mutating, f"{name} 不应标记为 mutating")

    def test_antnest_reexports_registry_helpers(self):
        for attr in ("tool_executors", "get_queen_tools"):
            self.assertTrue(hasattr(self.an, attr))


if __name__ == "__main__":
    unittest.main()
