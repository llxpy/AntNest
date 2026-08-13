# -*- coding: utf-8 -*-
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_compat import (
    apply_recommendations,
    recommend_model_settings,
    sanitize_messages_for_api,
)


def _mock_models_open(*_args, **_kwargs):
    import json

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


class ApiCompatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        with mock.patch("urllib.request.urlopen", _mock_models_open):
            import AntNest as an

            cls.an = importlib.reload(an)

    def test_minimax_request_is_openai_subset(self):
        self.an.ANT_BASE_URL = "https://api.minimax.chat/v1"
        data = self.an._build_request_data([{"role": "user", "content": "hi"}])
        self.assertNotIn("thinking", data)
        self.assertNotIn("top_k", data)
        self.assertNotIn("chat_template_kwargs", data)
        self.assertIn("model", data)

    def test_deepseek_uses_adaptive_thinking(self):
        self.an.ANT_BASE_URL = "https://api.deepseek.com/v1"
        self.an.THINKING_MODE = "auto"
        data = self.an._build_request_data([{"role": "user", "content": "hi"}], thinking=True)
        self.assertEqual(data.get("thinking"), {"type": "adaptive"})

    def test_detect_model_len_fallback_on_http_error(self):
        def _fail(*_a, **_k):
            import urllib.error

            raise urllib.error.HTTPError("url", 404, "nope", None, None)

        with mock.patch("urllib.request.urlopen", _fail):
            cap = self.an.detect_model_len()
        self.assertEqual(cap, self.an.DEFAULT_TOKEN_CAP)

    def test_skip_model_check(self):
        self.an.SKIP_MODEL_CHECK = True
        try:
            cap = self.an.detect_model_len()
            self.assertEqual(cap, self.an.DEFAULT_TOKEN_CAP)
        finally:
            self.an.SKIP_MODEL_CHECK = False

    def test_kimi_temperature_must_be_one(self):
        from api_compat import effective_temperature, resolve_api_profile

        self.an.ANT_BASE_URL = "https://api.moonshot.cn/v1"
        self.an.ANT_MODEL_NAME = "kimi-k2.6"
        data = self.an._build_request_data([{"role": "user", "content": "hi"}])
        self.assertEqual(data["temperature"], 1.0)

        self.an.ANT_BASE_URL = "https://proxy.example/v1"
        data2 = self.an._build_request_data([{"role": "user", "content": "hi"}])
        self.assertEqual(data2["temperature"], 1.0)

        self.assertEqual(resolve_api_profile("https://api.moonshot.cn/v1"), "kimi")
        self.assertEqual(effective_temperature(0.6, "kimi", "kimi-k2.6"), 1.0)

    def test_recommend_kimi_and_minimax(self):
        kimi = recommend_model_settings("https://api.moonshot.cn/v1", "kimi-k2.6")
        self.assertEqual(kimi["thinking_mode"], "off")

        mm = recommend_model_settings("https://api.minimax.chat/v1", "MiniMax-M2.5")
        self.assertEqual(mm["thinking_mode"], "off")
        self.assertEqual(mm["skip_model_check"], "true")

    def test_urlopen_retries_429(self):
        import io
        import urllib.error

        calls = {"n": 0}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        def fake_urlopen(_req):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.HTTPError(
                    "url",
                    429,
                    "busy",
                    {"Retry-After": "1"},
                    io.BytesIO(b'{"error":"overloaded"}'),
                )
            return _Resp()

        with mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch(
            "AntNest.time.sleep"
        ):
            req = __import__("urllib").request.Request("https://example.com/v1/chat/completions")
            out = self.an._urlopen_with_retry(req, max_retries=3)
        self.assertIsNotNone(out)
        self.assertEqual(calls["n"], 3)


class ApiCompatModuleTest(unittest.TestCase):
    def test_apply_recommendations_updates_settings(self):
        base = {"llm_model": "kimi-k2.6", "thinking_mode": "auto", "skip_model_check": "false"}
        patched, rec = apply_recommendations(base, "https://api.moonshot.cn/v1", "kimi-k2.6")
        self.assertEqual(patched["thinking_mode"], "off")
        self.assertIn("Kimi", rec["hint"])

    def test_sanitize_empty_assistant_for_kimi(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
                ],
            },
        ]
        out = sanitize_messages_for_api(msgs, "kimi")
        self.assertEqual(out[1]["content"], " ")
        self.assertNotIn("reasoning_content", out[1])

    def test_sanitize_keeps_reasoning_for_deepseek(self):
        msgs = [{"role": "assistant", "content": "ok", "reasoning_content": "think"}]
        out = sanitize_messages_for_api(msgs, "deepseek")
        self.assertEqual(out[0]["reasoning_content"], "think")


if __name__ == "__main__":
    unittest.main()
