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
