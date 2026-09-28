# -*- coding: utf-8 -*-
"""回归测试：模型能力探测、画像注入与系统提示词占位符。"""

import importlib
import io
import json
import os
import sys
import urllib.error
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mock_models_open(*_args, **_kwargs):
    body = json.dumps({
        "data": [{"id": "deepseek-v4-flash", "context_length": 128000}]
    }).encode()

    class _Resp:
        status = 200

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    return _Resp()


class CapabilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        # 必须**清掉**而不是 setdefault：有 14 个测试文件用
        # os.environ.setdefault("ANT_SKIP_MODEL_CHECK", "1") 且从不清理，
        # 按字母序本模块排在它们之后，于是继承到一个「跳过能力检测」的
        # 环境，_ensure_model_cap 直接 return，画像永远是未检测兜底。
        # 症状：单跑本文件全绿，全量跑两条红。
        os.environ.pop("ANT_SKIP_MODEL_CHECK", None)
        # 环境变量之外还要复位模块态：antnest_config 在 import 期就把
        # SKIP_MODEL_CHECK 读成了模块全局，而下面 reload 的只是壳
        # （from antnest_config import * 是值拷贝，不会重跑 config 模块）。
        import antnest_config
        import model_capabilities
        importlib.reload(antnest_config)
        antnest_config.SKIP_MODEL_CHECK = False
        # 缓存是模块级、带 TTL 的共享状态，清掉才能让断言只反映本用例。
        model_capabilities._CACHE.clear()
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)
            cls.an.SKIP_MODEL_CHECK = False

    def test_detect_model_len_uses_detected_value(self):
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            cap = self.an.detect_model_len()
        self.assertEqual(cap, 128000)

    def test_detect_model_len_no_sys_exit_on_401(self):
        def _fail(*_a, **_k):
            raise urllib.error.HTTPError(
                "url", 401, "unauthorized", None,
                io.BytesIO(b'{"error":"unauthorized"}'),
            )

        with mock.patch("urllib.request.urlopen", _fail):
            cap = self.an.detect_model_len()
        self.assertEqual(cap, self.an.DEFAULT_TOKEN_CAP)

    def test_401_must_not_raise_system_exit(self):
        def _fail(*_a, **_k):
            raise urllib.error.HTTPError(
                "url", 401, "no", None, io.BytesIO(b"x")
            )

        with mock.patch("urllib.request.urlopen", _fail):
            try:
                self.an.detect_model_len()
            except SystemExit:
                self.fail("detect_model_len 在 401 时不应 sys.exit(1) 打死进程")
            except Exception as e:
                self.fail(f"detect_model_len 不应抛异常：{e}")

    def test_ensure_model_cap_fills_system_prompt(self):
        an = self.an
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            an._ensure_model_cap()
        self.assertIn("当前模型能力", an.SYSTEM_PROMPT)
        # 不在这里断言 "{model_capability}" 已被替换掉 —— 那是在测一个没人
        # 依赖的副作用：
        #   * 占位符是在**格式化时**由 model_capability_summary() 填的，
        #     所有消费方都走 SYSTEM_PROMPT.format(model_capability=...)；
        #   * _ensure_model_cap 里那句 .replace() 写的是 antnest_config 的
        #     模块全局 SYSTEM_PROMPT，而消费方（AntNest.py:160/231、
        #     antnest_bridge.py:832、antnest_memory.py:81）读的是壳里的副本
        #     —— from x import * 是值拷贝，这就是 AGENTS.md §9 的双命名空间。
        # 所以真正该断言的是「格式化后的提示词里有画像」，见下。
        filled = an.SYSTEM_PROMPT.format(
            nest_md="N", hints="H", env_info="E",
            model_capability=an.model_capability_summary(),
        )
        self.assertIn("deepseek-v4-flash", filled)
        self.assertIn("数学", filled)

    def test_ensure_model_cap_calibrates_token_cap(self):
        """"把画像注入 SYSTEM_PROMPT" 的实际效果是校准 TOKEN_CAP。"""
        an = self.an
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            an._ensure_model_cap()
        self.assertEqual(
            128000, an.TOKEN_CAP,
            "TOKEN_CAP 未按 /models 实测值校准",
        )

    def test_summarize_capability(self):
        mc = self.an.model_capabilities
        text = mc.summarize_capability({"model": "deepseek-v4-flash"})
        self.assertIn("数学", text)
        self.assertIn("代码", text)
        self.assertIn("写作", text)
        default = mc.summarize_capability({"model": "kimi-k2.6"})
        self.assertIn("默认画像", default)

    def test_capability_cached(self):
        # 自己先探测一次，不依赖 setUpClass 的副作用：model_capabilities 的
        # 缓存是模块级、带 TTL 的共享状态，全量套件里别的用例可能已经填过
        # 或清过它。依赖执行顺序的断言在单跑与全跑之间会给出不同结论。
        mc = self.an.model_capabilities
        base, model = self.an.ANT_BASE_URL, self.an.ANT_MODEL_NAME
        if not mc.capability_cached(base, model):
            with mock.patch("urllib.request.urlopen", _mock_models_open):
                mc.detect_capability(
                    base, model, "test-key",
                    default_cap=self.an.DEFAULT_TOKEN_CAP, timeout=6,
                )
        self.assertTrue(
            mc.capability_cached(base, model),
            "探测后仍未进缓存：capability_cached 与 detect_capability 不配对",
        )

    def test_fallback_template_has_placeholder(self):
        self.assertIn("{model_capability}", self.an._FALLBACK_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()