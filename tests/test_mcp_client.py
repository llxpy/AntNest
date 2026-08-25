# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_client as mc


class _FakeStdin:
    def __init__(self):
        self.buf = []

    def write(self, s):
        self.buf.append(s)

    def flush(self):
        pass

    def close(self):
        pass


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)
        self._i = 0

    def readline(self):
        if self._i >= len(self._lines):
            return ""
        line = self._lines[self._i]
        self._i += 1
        return line + "\n"


class McpHubTest(unittest.TestCase):
    def test_load_config_missing_file(self):
        hub = mc.McpHub()
        with self.assertRaises(mc.McpError) as ctx:
            hub.load_config("/no/such/mcp.json")
        self.assertIn("不存在", str(ctx.exception))

    def test_load_config_invalid_structure(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"not_servers": {}}, f)
            path = f.name
        try:
            hub = mc.McpHub()
            with self.assertRaises(mc.McpError) as ctx:
                hub.load_config(path)
            self.assertIn("servers", str(ctx.exception))
        finally:
            os.unlink(path)

    @mock.patch.object(mc, "McpSession")
    def test_load_config_registers_tools(self, mock_sess_cls):
        sess = mock.MagicMock()
        sess.list_tools.return_value = [
            {"name": "grep", "description": "search files"},
        ]
        mock_sess_cls.return_value = sess

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(
                {
                    "servers": {
                        "demo": {
                            "command": "python",
                            "args": ["-c", "pass"],
                            "env": {},
                        }
                    }
                },
                f,
            )
            path = f.name
        try:
            hub = mc.McpHub()
            hub.load_config(path)
            self.assertIn("demo", hub.sessions)
            self.assertEqual(hub.tool_index.get("grep"), ("demo", "grep"))
            mock_sess_cls.assert_called_once()
            sess.initialize.assert_called()
        finally:
            os.unlink(path)

    def test_call_unknown_server(self):
        hub = mc.McpHub()
        with self.assertRaises(mc.McpError) as ctx:
            hub.call("nope", "tool")
        self.assertIn("未知", str(ctx.exception))

    def test_session_call_tool_text_content(self):
        init_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "1"}})
        call_resp = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": "pong"}]},
            }
        )
        fake_stdout = _FakeStdout([init_resp, call_resp])
        fake_stdin = _FakeStdin()
        fake_proc = mock.MagicMock()
        fake_proc.stdin = fake_stdin
        fake_proc.stdout = fake_stdout

        with mock.patch.object(mc.subprocess, "Popen", return_value=fake_proc):
            sess = mc.McpSession("demo", "echo")
            sess._initialized = False
            sess._next_id = 1
            out = sess.call_tool("ping", {})
        self.assertEqual(out, "pong")


class McpBridgeInitTest(unittest.TestCase):
    def test_init_mcp_skips_missing_config(self):
        import importlib
        import antnest_bridge as bridge

        importlib.reload(bridge)
        core = bridge.AntNestCore()
        core.mod = mock.MagicMock()
        core.mod.MCP_ENABLED = True
        core.mod.MCP_HUB = object()
        core._init_mcp({"mcp_enabled": "true", "mcp_config": "missing-file-xyz.json"})
        self.assertFalse(core.mod.MCP_ENABLED)
        self.assertIsNone(core.mod.MCP_HUB)


class McpRemoteSessionTest(unittest.TestCase):
    """远程 MCP（http / sse）会话：JSON 与 SSE data: 响应解析。"""

    def _with_http(self, bodies, fn):
        class _FakeResp:
            headers = {}
            def __init__(self, body): self._body = body
            def read(self): return self._body
            def __enter__(self): return self
            def __exit__(self, *_a): return False
        it = iter(bodies)
        def _urlopen(req, timeout=None):
            return _FakeResp(next(it))
        with mock.patch("mcp_client.urllib.request.urlopen", side_effect=_urlopen):
            sess = mc.HttpMcpSession("remote", "https://mcp.example.com/mcp")
            return fn(sess)

    def test_http_session_json_responses(self):
        init = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}).encode()
        empty = b""
        tools = json.dumps({
            "jsonrpc": "2.0", "id": 3,
            "result": {"tools": [{"name": "fetch_page", "description": "抓取网页"}]},
        }).encode()
        def _check(sess):
            self.assertEqual(sess.list_tools()[0]["name"], "fetch_page")
        self._with_http([init, empty, tools], _check)

    def test_http_session_sse_data_body(self):
        # 有些远端以 SSE data: 行返回 JSON-RPC 响应
        init = b"data: " + json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}
        ).encode() + b"\n\n"
        empty = b""
        tools = (b"data: " + json.dumps({
            "jsonrpc": "2.0", "id": 3,
            "result": {"tools": [{"name": "sse_tool", "description": "sse tool"}]},
        }).encode() + b"\n\n")
        def _check(sess):
            self.assertEqual(sess.list_tools()[0]["name"], "sse_tool")
        self._with_http([init, empty, tools], _check)

    def test_http_session_requires_http_url(self):
        with self.assertRaises(mc.McpError):
            mc.HttpMcpSession("bad", "not-a-url")

    def test_sse_session_requires_http_url(self):
        with self.assertRaises(mc.McpError):
            mc.SseMcpSession("bad", "nope")


class _FakeStream:
    """SseMcpSession 启动时的假 SSE 流：空迭代 + 可关闭。"""
    def __iter__(self):
        return iter(())
    def close(self):
        pass


class McpBuildSessionTest(unittest.TestCase):
    def test_build_session_routes(self):
        self.assertIsInstance(mc._build_session("a", {"url": "https://x/mcp", "type": "http"}), mc.HttpMcpSession)
        with mock.patch("mcp_client.urllib.request.urlopen", return_value=_FakeStream()):
            self.assertIsInstance(mc._build_session("b", {"url": "https://x/sse", "type": "sse"}), mc.SseMcpSession)
        self.assertIsInstance(mc._build_session("c", {"command": "python"}), mc.McpSession)
        self.assertIsInstance(mc._build_session("d", {"url": "https://x/mcp"}), mc.HttpMcpSession)
        with self.assertRaises(mc.McpError):
            mc._build_session("e", {})


class McpHubMultiServerTest(unittest.TestCase):
    def test_load_config_multiple_servers(self):
        sess_a = mock.MagicMock()
        sess_a.list_tools.return_value = [{"name": "tool_a", "description": "a"}]
        sess_b = mock.MagicMock()
        sess_b.list_tools.return_value = [{"name": "tool_b", "description": "b"}]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"servers": {"srv_a": {"url": "https://a/mcp"}, "srv_b": {"command": "py"}}}, f)
            path = f.name
        try:
            with mock.patch("mcp_client._build_session", side_effect=[sess_a, sess_b]):
                hub = mc.McpHub()
                hub.load_config(path)
            self.assertEqual(set(hub.sessions), {"srv_a", "srv_b"})
            self.assertEqual(hub.tool_index["tool_a"], ("srv_a", "tool_a"))
            self.assertEqual(hub.tool_index["tool_b"], ("srv_b", "tool_b"))
            self.assertEqual(hub.server_status["srv_a"], "ready")
            self.assertEqual(hub.server_status["srv_b"], "ready")
        finally:
            os.unlink(path)

    def test_tool_name_conflict_generates_alias(self):
        sess_a = mock.MagicMock()
        sess_a.list_tools.return_value = [{"name": "grep", "description": "server a grep"}]
        sess_b = mock.MagicMock()
        sess_b.list_tools.return_value = [{"name": "grep", "description": "server b grep"}]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"servers": {"srv_a": {"url": "https://a/mcp"}, "srv_b": {"command": "py"}}}, f)
            path = f.name
        try:
            with mock.patch("mcp_client._build_session", side_effect=[sess_a, sess_b]):
                hub = mc.McpHub()
                hub.load_config(path)
            self.assertEqual(hub.tool_index["grep"], ("srv_a", "grep"))          # 首台保留
            self.assertEqual(hub.tool_index["srv_b__grep"], ("srv_b", "grep"))  # 别名
            catalog = {item["alias"]: item for item in hub.list_catalog()}
            self.assertIn("srv_b__grep", catalog)
        finally:
            os.unlink(path)

    def test_remote_failure_does_not_block_others(self):
        def _fail(name, cfg):
            raise mc.McpError(f"{name} init failed")
        sess_b = mock.MagicMock()
        sess_b.list_tools.return_value = [{"name": "ok_tool", "description": "ok"}]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"servers": {"bad": {"url": "https://bad/mcp"}, "good": {"command": "py"}}}, f)
            path = f.name
        try:
            with mock.patch("mcp_client._build_session", side_effect=[_fail, sess_b]):
                hub = mc.McpHub()
                hub.load_config(path)
            self.assertEqual(hub.server_status["bad"], "failed")
            self.assertEqual(hub.server_status["good"], "ready")
            self.assertIn("ok_tool", hub.tool_index)
        finally:
            os.unlink(path)


class AntNestMcpToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("ANT_API_KEY", "test-key")
        os.environ.setdefault("ANT_MODEL_NAME", "deepseek-v4-flash")
        body = json.dumps({"data": [{"id": "deepseek-v4-flash", "context_length": 128000}]}).encode()

        class _Resp:
            status = 200

            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        with mock.patch("urllib.request.urlopen", lambda *a, **k: _Resp()):
            import importlib
            import AntNest as an

            cls.an = importlib.reload(an)

    def test_mcp_call_disabled(self):
        self.an.MCP_ENABLED = False
        self.an.MCP_HUB = None
        out = json.loads(self.an.mcp_call("demo", "ping", "{}"))
        self.assertEqual(out["status"], "error")
        self.assertIn("未启用", out["error"])


if __name__ == "__main__":
    unittest.main()
