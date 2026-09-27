# -*- coding: utf-8 -*-
"""antnest_errors 单元测试：分层异常体系 + 错误码 + 向后兼容。"""
import unittest

from antnest_errors import (
    AntNestError,
    ApiError,
    ConfigError,
    NetworkError,
    SafetyError,
    ToolError,
    WorkerError,
    as_antnest_error,
)


class ErrorHierarchyTest(unittest.TestCase):
    def test_all_errors_are_antnest_errors(self):
        """所有业务异常都应继承 AntNestError。"""
        for cls in (ApiError, ConfigError, NetworkError, SafetyError, ToolError, WorkerError):
            self.assertTrue(issubclass(cls, AntNestError), f"{cls.__name__} not subclass of AntNestError")

    def test_default_codes(self):
        self.assertEqual(ApiError("x").code, "API-001")
        self.assertEqual(ConfigError("x").code, "CFG-001")
        self.assertEqual(SafetyError("x").code, "SEC-001")

    def test_safety_error_is_value_error(self):
        """SafetyError 必须保持 ValueError 兼容（旧代码捕获 ValueError）。"""
        err = SafetyError("path escape")
        self.assertIsInstance(err, ValueError)
        with self.assertRaises(ValueError):
            raise err

    def test_error_dict(self):
        err = ApiError("boom", code="API-002", user_msg="模型服务不可用")
        d = err.to_dict()
        self.assertEqual(d["code"], "API-002")
        self.assertEqual(d["user_msg"], "模型服务不可用")
        self.assertEqual(d["error"], "boom")

    def test_http_status_errors(self):
        """常见 HTTP 错误码应可被识别。"""
        err = ApiError("LLM调用失败，HTTP 429：过载", user_msg="过载")
        self.assertEqual(err.code, "API-001")
        self.assertIn("429", str(err))

    def test_think_repeat_error(self):
        from antnest_llm import ThinkRepeatError
        self.assertTrue(issubclass(ThinkRepeatError, AntNestError))

    def test_as_antnest_error_passthrough(self):
        """已 AntNestError 的原样返回。"""
        orig = ApiError("keep me")
        wrapped = as_antnest_error(orig)
        self.assertIs(wrapped, orig)

    def test_as_antnest_error_wraps(self):
        """裸异常包装为 BaseError 并保留 cause。"""
        bare = ValueError("raw")
        wrapped = as_antnest_error(bare)
        self.assertIsInstance(wrapped, AntNestError)
        self.assertIsNot(wrapped, bare)

    def test_exception_chain_preserved(self):
        """from e 应保留异常链。"""
        try:
            try:
                raise ValueError("original")
            except ValueError as e:
                raise ApiError("wrapped") from e
        except ApiError as e:
            self.assertIsInstance(e.__cause__, ValueError)


class CompatibilityTest(unittest.TestCase):
    def test_mcp_error_is_antnest(self):
        """MCP 错误应纳入统一异常体系而不破坏现有捕获。"""
        from mcp_client import McpError
        self.assertTrue(issubclass(McpError, AntNestError))
        e = McpError("timeout")
        self.assertIn("timeout", str(e))
        self.assertEqual(e.code, "MCP-001")


if __name__ == "__main__":
    unittest.main()