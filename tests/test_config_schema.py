# -*- coding: utf-8 -*-
"""antnest_config_schema 单元测试。"""
import unittest

from antnest_config_schema import (
    ApiConfig,
    AgentConfig,
    AppConfig,
    validate_config,
)


class ConfigSchemaTest(unittest.TestCase):
    def test_valid_config(self):
        cfg = {
            "api": {
                "base_url": "https://api.deepseek.com/v1",
                "model_name": "deepseek-chat",
                "thinking_mode": "auto",
                "skip_model_check": True,
            },
            "agent": {
                "max_depth": 2,
                "max_clones": {"0": 10, "1": 5, "2": 3},
                "compact_threshold": 0.8,
            },
        }
        result = validate_config(cfg)
        self.assertIsInstance(result, AppConfig)
        self.assertEqual(result.api.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(result.api.model_name, "deepseek-chat")
        self.assertTrue(result.api.skip_model_check)
        self.assertEqual(result.agent.max_depth, 2)
        self.assertEqual(result.agent.compact_threshold, 0.8)
        self.assertEqual(result.agent.max_clones, {"0": 10, "1": 5, "2": 3})

    def test_temperature_validated(self):
        ok = validate_config({"api": {"temperature": 1.2}})
        self.assertEqual(ok.api.temperature, 1.2)
        bad = validate_config({"api": {"temperature": 5.0}})
        self.assertNotEqual(bad.api.temperature, 5.0)  # 越界回退默认/钳制
        self.assertTrue(any("temperature" in p for p in bad.problems))

    def test_invalid_minmax_clamped(self):
        """越界数值应被钳制到合法范围。"""
        cfg = {
            "agent": {
                "max_depth": 99,               # 超上限
                "compact_threshold": 2.5,      # 超上限
                "tool_result_max_len": -5,     # 超下限
            }
        }
        result = validate_config(cfg)
        self.assertEqual(result.agent.max_depth, 10)
        self.assertEqual(result.agent.compact_threshold, 1.0)
        self.assertEqual(result.agent.tool_result_max_len, 100)
        self.assertTrue(len(result.problems) > 0)

    def test_invalid_http_url(self):
        """无协议前缀的 URL 应被标记。"""
        cfg = {"api": {"base_url": "api.deepseek.com/v1"}}
        result = validate_config(cfg)
        self.assertEqual(result.api.base_url, "api.deepseek.com/v1")
        self.assertTrue(any("base_url" in p for p in result.problems))

    def test_invalid_thinking_mode(self):
        """无效 thinking_mode 应回退 auto。"""
        cfg = {"api": {"thinking_mode": "bogus"}}
        result = validate_config(cfg)
        self.assertEqual(result.api.thinking_mode, "auto")

    def test_unknown_keys_tracked(self):
        """未识别字段应被跟踪而不崩溃。"""
        cfg = {"api": {"some_new_field": 1}, "agent": {"future_field": 2}}
        result = validate_config(cfg)
        self.assertIn("some_new_field", result.unknown_api_keys)
        self.assertIn("future_field", result.unknown_agent_keys)

    def test_wrong_types_safely_fallback(self):
        """类型错误的字段应回退默认值。"""
        cfg = {
            "api": {"skip_model_check": "not-a-bool", "default_token_cap": "abc"},
            "agent": {"max_clones": "not-a-dict"},
        }
        result = validate_config(cfg)
        self.assertFalse(result.api.skip_model_check)
        self.assertEqual(result.api.default_token_cap, 128000)
        self.assertEqual(result.agent.max_clones, {"0": 10, "1": 5, "2": 3})

    def test_empty_config_uses_defaults(self):
        result = validate_config({})
        self.assertEqual(result.api.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(result.agent.max_depth, 2)
        self.assertTrue(result.agent.restore_on_restart)

    def test_none_config_uses_defaults(self):
        result = validate_config(None)
        self.assertEqual(result.agent.worker_timeout, 300)
        self.assertEqual(result.agent.tool_result_max_len, 8000)


if __name__ == "__main__":
    unittest.main()
