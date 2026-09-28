# -*- coding: utf-8 -*-
"""Step 1 验收：llm_chat_stream 必须捕获 finish_reason。

这个测试存在的唯一理由：finish_reason 挂在 choices[0] 上而不在 delta 里，
而流式协议的**终止块是 {"delta": {}, "finish_reason": "stop"}**。
antnest_llm 的解析里有 `delta = choices[0].get("delta", {})` 紧跟
`if not delta: continue`——把读取 finish_reason 的代码放在这两行之后，
测试会全绿而实际覆盖为零（路径根本不可达）。

所以本测试必须用真实的 SSE 字节流驱动，且**必须包含 delta 为空的终止块**。
用 mock 掉 resp 的 dict 返回值是测不出来的，那样恰好绕过了要防的那个 bug。
"""
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ANT_API_KEY", "test-key")  # 绕开 antnest_config 的 import 期退出

import antnest_config  # noqa: E402


def sse(*chunks) -> bytes:
    """把若干 chunk 序列化成 SSE 字节流（与真实服务端同格式）。"""
    out = []
    for c in chunks:
        out.append(b"data: " + json.dumps(c, ensure_ascii=False).encode("utf-8") + b"\n\n")
    out.append(b"data: [DONE]\n\n")
    return b"".join(out)


def delta_chunk(text=None, reasoning=None):
    d = {}
    if text is not None:
        d["content"] = text
    if reasoning is not None:
        d["reasoning_content"] = reasoning
    return {"choices": [{"index": 0, "delta": d}]}


def terminal(reason):
    """终止块：delta 为空 —— 这正是会让 finish_reason 读不到的那个形状。"""
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


class _FakeResp:
    """最小 urllib 响应替身：支持按行迭代 + close。"""

    def __init__(self, payload: bytes):
        self._lines = payload.splitlines(keepends=True)
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


class FinishReasonTest(unittest.TestCase):
    def _call(self, chunks):
        import antnest_llm
        with mock.patch.object(antnest_config.urllib.request, "urlopen",
                               return_value=_FakeResp(sse(*chunks))):
            return antnest_llm.llm_chat_stream([{"role": "user", "content": "hi"}])

    def test_stop_is_captured(self):
        _m, _u, fr = self._call([delta_chunk("你好"), terminal("stop")])
        self.assertEqual("stop", fr)

    def test_length_is_captured(self):
        """被 token 上限截断 —— 用户看到「没有回复」的头号成因。"""
        _m, _u, fr = self._call([delta_chunk("半句话"), terminal("length")])
        self.assertEqual("length", fr)

    def test_content_filter_is_captured(self):
        _m, _u, fr = self._call([terminal("content_filter")])
        self.assertEqual("content_filter", fr)

    def test_insufficient_system_resource_is_captured(self):
        """DeepSeek 侧特有值；漏掉就会落进 empty_response 兜底。"""
        _m, _u, fr = self._call([terminal("insufficient_system_resource")])
        self.assertEqual("insufficient_system_resource", fr)

    def test_terminal_chunk_has_empty_delta(self):
        """守住前提：若哪天终止块不再带空 delta，本测试的前提就变了。"""
        self.assertEqual({}, terminal("stop")["choices"][0]["delta"])

    def test_finish_reason_read_precedes_delta_shortcircuit(self):
        """静态守护：读取点必须排在 `if not delta` 之前。

        动态测试在读取点写错时会「全绿零覆盖」，所以再加一条源码顺序断言
        作为第二道防线。
        """
        src = io.open(ROOT / "antnest_llm.py", encoding="utf-8").read()
        read = src.index('choices[0].get("finish_reason")')
        short = src.index("if not delta:")
        self.assertLess(read, short,
                        "finish_reason 的读取点在 `if not delta` 之后，"
                        "终止块会被短路掉，永远读不到")

    def test_last_finish_reason_wins(self):
        """流中途若有多个非空 finish_reason，取最后一个。"""
        _m, _u, fr = self._call([
            delta_chunk("x"),
            {"choices": [{"index": 0, "delta": {"content": "y"},
                          "finish_reason": "stop"}]},
            terminal("length"),
        ])
        self.assertEqual("length", fr)

    def test_absent_finish_reason_yields_empty_string(self):
        """某些端点不提供该字段；必须是空串而不是 None/抛错。"""
        _m, _u, fr = self._call([delta_chunk("只有内容")])
        self.assertEqual("", fr)

    def test_message_has_no_private_keys(self):
        """finish_reason 绝不能混进 message —— 它会被原样发回 API。"""
        m, _u, fr = self._call([delta_chunk("内容"), terminal("length")])
        self.assertEqual("length", fr)
        self.assertNotIn("finish_reason", m)
        self.assertNotIn("_finish_reason", m)
        self.assertEqual({"role", "content"}, set(m) & {"role", "content", "finish_reason"})


class LoopResultTest(unittest.TestCase):
    def test_loop_result_defaults(self):
        from antnest_loop import LoopResult
        r = LoopResult()
        self.assertEqual("replied", r.outcome)
        self.assertEqual(0, r.rounds)
        self.assertEqual([], r.finish_reasons)
        self.assertEqual([], r.retry_notes)
        self.assertIsNone(r.error)

    def test_to_dict_is_json_safe(self):
        from antnest_loop import LoopResult
        r = LoopResult()
        r.outcome = "empty"
        r.rounds = 3
        r.finish_reasons.append("length")
        d = r.to_dict()
        self.assertEqual(
            {"outcome": "empty", "rounds": 3,
             "finish_reasons": ["length"], "retry_notes": []}, d)
        json.dumps(d)  # 必须可序列化

    def test_to_dict_copies_lists(self):
        """to_dict 返回副本，改它不应影响原对象。"""
        from antnest_loop import LoopResult
        r = LoopResult()
        d = r.to_dict()
        d["finish_reasons"].append("x")
        self.assertEqual([], r.finish_reasons)

    def test_all_outcomes_are_annotated_in_source(self):
        """7 种结局都必须在源码里有标注，缺一个就诊断不出来。"""
        import ast
        src = io.open(ROOT / "antnest_loop.py", encoding="utf-8").read()
        tree = ast.parse(src)
        fn = next(f for f in tree.body if isinstance(f, ast.FunctionDef)
                  and f.name == "agent_single_loop")
        got = set()
        for node in ast.walk(fn):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Attribute)
                    and node.targets[0].attr == "outcome"
                    and isinstance(node.value, ast.Constant)):
                got.add(node.value.value)
        self.assertLessEqual(
            {"replied", "empty", "max_rounds", "cancelled", "denied",
             "approval", "error"}, got, f"缺少结局标注: {got}")


class OutcomeReachabilityTest(unittest.TestCase):
    """每条 outcome 赋值都必须可达。

    实施过程中三次把代码插到了不可达位置（break 之后、except 块内、
    dict 字面量内），三次都通过了 ast.parse —— 语法检查完全测不出来。
    """

    def test_no_outcome_after_unconditional_break(self):
        import ast
        src = io.open(ROOT / "antnest_loop.py", encoding="utf-8").read()
        tree = ast.parse(src)
        fn = next(f for f in tree.body if isinstance(f, ast.FunctionDef)
                  and f.name == "agent_single_loop")

        parents = {}
        for p in ast.walk(fn):
            for c in ast.iter_child_nodes(p):
                parents[c] = p

        for node in ast.walk(fn):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Attribute)
                    and node.targets[0].attr == "outcome"):
                continue
            kinds, cur = [], node
            while cur in parents:
                cur = parents[cur]
                kinds.append(type(cur))
            self.assertNotIn(ast.Dict, kinds,
                             f"line {node.lineno}: outcome 被插进了字典字面量")
            # 找同 block 的后继语句
            sibs = None
            for p in [node] + [cur]:
                for f in ("body", "orelse", "finalbody"):
                    b = getattr(p, f, None)
                    if isinstance(b, list) and node in b:
                        sibs = b
                        break
                if sibs:
                    break
            if sibs:
                i = sibs.index(node)
                if i + 1 < len(sibs) and isinstance(sibs[i + 1], (ast.Break, ast.Return)):
                    self.assertNotIn(ast.If, kinds,
                                     f"line {node.lineno}: outcome 之后紧跟 "
                                     f"{type(sibs[i+1]).__name__}，不可达")

    def test_loop_returns_result_at_function_level(self):
        """return 必须在函数层（while 之后），不是 inside except/if。"""
        import ast
        src = io.open(ROOT / "antnest_loop.py", encoding="utf-8").read()
        fn = next(f for f in ast.parse(src).body
                  if isinstance(f, ast.FunctionDef) and f.name == "agent_single_loop")
        rets = [n for n in fn.body if isinstance(n, ast.Return)]
        self.assertEqual(1, len(rets), "函数层应有且仅有一个 return")
        self.assertIsInstance(rets[0].value, ast.Name)
        self.assertEqual("_result", rets[0].value.id)


if __name__ == "__main__":
    unittest.main()
