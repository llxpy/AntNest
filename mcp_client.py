# -*- coding: utf-8 -*-
"""
mcp_client — 最小 MCP stdio 客户端（JSON-RPC 2.0）

配置示例 mcp.json:
{
  "servers": {
    "fs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "E:/proj"],
      "env": {}
    }
  }
}
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path


class McpError(Exception):
    pass


class McpSession:
    def __init__(self, name: str, command: str, args: list | None = None, env: dict | None = None):
        self.name = name
        self._proc = subprocess.Popen(
            [command] + list(args or []),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, **{str(k): str(v) for k, v in (env or {}).items()}},
            bufsize=1,
        )
        self._lock = threading.Lock()
        self._next_id = 1
        self._initialized = False

    def _request(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        if not self._proc.stdin or not self._proc.stdout:
            raise McpError("MCP 进程管道不可用")
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
            if params is not None:
                payload["params"] = params
            line = json.dumps(payload, ensure_ascii=False)
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
            deadline = __import__("time").time() + timeout
            while __import__("time").time() < deadline:
                raw = self._proc.stdout.readline()
                if not raw:
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == req_id:
                    if "error" in msg:
                        raise McpError(str(msg["error"]))
                    return msg.get("result") or {}
            raise McpError(f"MCP 请求超时：{method}")

    def initialize(self):
        if self._initialized:
            return
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "AntNest", "version": "1.2.1"},
            },
        )
        self._notify("notifications/initialized", {})
        self._initialized = True

    def _notify(self, method: str, params: dict | None = None) -> None:
        if not self._proc.stdin:
            raise McpError("MCP 进程管道不可用")
        with self._lock:
            payload: dict = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                payload["params"] = params
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()

    def list_tools(self) -> list[dict]:
        self.initialize()
        result = self._request("tools/list", {})
        tools = result.get("tools") or []
        return tools if isinstance(tools, list) else []

    def call_tool(self, tool_name: str, arguments: dict | None = None) -> str:
        self.initialize()
        result = self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
        )
        content = result.get("content") or []
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        if parts:
            return "\n".join(parts)
        return json.dumps(result, ensure_ascii=False)

    def close(self):
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
        except Exception:
            pass


class McpHub:
    def __init__(self):
        self.sessions: dict[str, McpSession] = {}
        self.tool_index: dict[str, tuple[str, str]] = {}  # tool_name -> (server, tool)

    def load_config(self, config_path: str) -> None:
        self.close()
        path = Path(os.path.expandvars(os.path.expanduser(config_path)))
        if not path.is_file():
            raise McpError(f"MCP 配置文件不存在：{path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        servers = data.get("servers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            raise McpError("MCP 配置需包含 servers 对象")
        for name, cfg in servers.items():
            if not isinstance(cfg, dict):
                continue
            cmd = cfg.get("command")
            if not cmd:
                continue
            sess = McpSession(name, cmd, cfg.get("args") or [], cfg.get("env") or {})
            sess.initialize()
            self.sessions[name] = sess
            for tool in sess.list_tools():
                tname = tool.get("name")
                if tname:
                    self.tool_index[tname] = (name, tname)

    def call(self, server: str, tool: str, arguments: dict | None = None) -> str:
        sess = self.sessions.get(server)
        if not sess:
            raise McpError(f"未知 MCP 服务器：{server}")
        return sess.call_tool(tool, arguments or {})

    def list_catalog(self) -> list[dict]:
        out = []
        for server, sess in self.sessions.items():
            for tool in sess.list_tools():
                out.append(
                    {
                        "server": server,
                        "name": tool.get("name"),
                        "description": (tool.get("description") or "")[:200],
                    }
                )
        return out

    def close(self):
        for sess in self.sessions.values():
            try:
                sess.close()
            except Exception:
                pass
        self.sessions.clear()
        self.tool_index.clear()


_hub: McpHub | None = None


def get_hub() -> McpHub:
    global _hub
    if _hub is None:
        _hub = McpHub()
    return _hub
