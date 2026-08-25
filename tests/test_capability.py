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
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)

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
        self.assertNotIn("{model_capability}", an.SYSTEM_PROMPT)
        filled = an.SYSTEM_PROMPT.format(
            nest_md="N", hints="H", env_info="E",
            model_capability=an.model_capability_summary(),
        )
        self.assertIn("deepseek-v4-flash", filled)
        self.assertIn("数学", filled)

    def test_summarize_capability(self):
        mc = self.an.model_capabilities
        text = mc.summarize_capability({"model": "deepseek-v4-flash"})
        self.assertIn("数学", text)
        self.assertIn("代码", text)
        self.assertIn("写作", text)
        default = mc.summarize_capability({"model": "kimi-k2.6"})
        self.assertIn("默认画像", default)

    def test_capability_cached(self):
        mc = self.an.model_capabilities
        self.assertTrue(
            mc.capability_cached(self.an.ANT_BASE_URL, self.an.ANT_MODEL_NAME)
        )

    def test_fallback_template_has_placeholder(self):
        self.assertIn("{model_capability}", self.an._FALLBACK_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()