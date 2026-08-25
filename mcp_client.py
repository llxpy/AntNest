# -*- coding: utf-8 -*-
"""
mcp_client — 轻量 MCP 客户端（JSON-RPC 2.0），零第三方依赖。

支持三种传输：
  1. stdio（本地子进程，原行为不变）：
       {
         "servers": {
           "fs": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "E:/proj"], "env": {} }
         }
       }
  2. http / Streamable HTTP（远端 URL，例如魔搭/ModelScope MCP、OpenAI 兼容远端）：
       {
         "servers": {
           "modelscope": {
             "url": "https://mcp.modelscope.cn/...",
             "type": "http",
             "headers": { "Authorization": "Bearer ...", "X-Custom": "..." }
           }
         }
       }
  3. sse（经典 Server-Sent Events 传输，GET /sse 建流 + POST endpoint 请求）：
       {
         "servers": {
           "remote": { "url": "https://example.com/mcp", "type": "sse", "headers": {} }
         }
       }

每台服务器独立会话；McpHub 支持多服务器并行加载，工具名冲突自动生成
`<server>__<tool>` 别名，并保留首台归属，避免互相覆盖。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


class McpError(Exception):
    pass


def _json_lines_response(raw: str):
    """body 可能是纯 JSON，也可能是 SSE data: 行集合；返回最后一条带 id 的消息。"""
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("data:"):
        msgs = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                frag = line[5:].strip()
                if frag:
                    try:
                        msgs.append(json.loads(frag))
                    except json.JSONDecodeError:
                        continue
        if not msgs:
            return {}
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("id") is not None:
                return m
        return msgs[-1] if isinstance(msgs[-1], dict) else {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


# ==================== 1. stdio 传输（原实现保留） ====================
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
            **(  {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            ),
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
            deadline = time.time() + timeout
            while time.time() < deadline:
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


# ==================== 2. http / Streamable HTTP 传输 ====================
class HttpMcpSession:
    """远端 MCP（Streamable HTTP）：JSON-RPC 通过 POST 发送到 url。

    兼容两种响应：纯 JSON，或 SSE data: 行。若服务器在 initialize 响应中给出
    sessionId（或响应头 Mcp-Session-Id），会带上会话头继续交互。
    """

    def __init__(self, name: str, url: str, headers: dict | None = None, timeout: float = 60):
        self.name = name
        self.url = (url or "").strip().rstrip("/")
        if not self.url:
            raise McpError(f"[{name}] 缺少 url")
        if "://" not in self.url:
            raise McpError(f"[{name}] url 必须是 http/https 链接：{self.url}")
        self._headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self._timeout = timeout
        self._lock = threading.Lock()
        self._next_id = 1
        self._initialized = False
        self._session_id = None

    def _post(self, payload: dict, timeout: float | None = None) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self._timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id") if resp.headers else None
                if sid and not self._session_id:
                    self._session_id = sid
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            msg = _json_lines_response(raw)
            if msg:
                return msg
            raise McpError(f"[{self.name}] HTTP {e.code}: {raw[:300]}")
        return _json_lines_response(raw)

    def _request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        msg = self._post(payload, timeout)
        if msg.get("error"):
            raise McpError(str(msg["error"]))
        return msg.get("result") or {}

    def _notify(self, method: str, params: dict | None = None) -> None:
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._post(payload, timeout=10)
        except Exception:
            pass

    def initialize(self):
        if self._initialized:
            return
        init = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "AntNest", "version": "1.2.1"},
        }
        result = self._request("initialize", init)
        # Streamable HTTP 规范：服务器可通过 result.sessionId 下发会话 id；
        # 客户端若无会话头，需用该 id 重发一次 initialize。
        if not self._session_id and isinstance(result, dict):
            sid = result.get("sessionId")
            if sid:
                self._session_id = sid
                try:
                    self._request("initialize", init)
                except Exception:
                    pass
        self._notify("notifications/initialized", {})
        self._initialized = True

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
        # HTTP 无持久连接需要关闭
        pass


# ==================== 3. sse 传输（经典 Server-Sent Events） ====================
class SseMcpSession:
    """经典 SSE MCP：GET url（应能拿到 event stream）→ 服务器发 event: endpoint
    给出 POST 目标；客户端把 JSON-RPC POST 到该 endpoint，响应通过 SSE 的
    event: message / data: JSON 回来。
    """

    def __init__(self, name: str, url: str, headers: dict | None = None, timeout: float = 60):
        self.name = name
        self.url = (url or "").strip().rstrip("/")
        if not self.url or "://" not in self.url:
            raise McpError(f"[{name}] 缺少有效 url：{self.url}")
        self._headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self._timeout = timeout
        self._endpoint = None
        self._reader = None
        self._responses: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._next_id = 1
        self._initialized = False
        self._ready = threading.Event()
        self._start_reader()

    def _start_reader(self):
        req = urllib.request.Request(
            self.url,
            headers={**self._headers, "Accept": "text/event-stream"},
            method="GET",
        )
        self._reader = urllib.request.urlopen(req, timeout=self._timeout)
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
        event = None
        data_lines: list[str] = []
        try:
            for raw in self._reader:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line == "":
                    self._dispatch_event(event, data_lines)
                    event = None
                    data_lines = []
                elif line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
        except Exception:
            pass
        finally:
            try:
                self._reader.close()
            except Exception:
                pass

    def _dispatch_event(self, event, data_lines):
        if event == "endpoint":
            joined = "".join(data_lines).strip()
            if joined:
                self._endpoint = joined
                self._ready.set()
        elif event == "message":
            try:
                msg = json.loads("".join(data_lines))
            except json.JSONDecodeError:
                return
            if isinstance(msg, dict) and msg.get("id") is not None:
                with self._cond:
                    self._responses[msg["id"]] = msg
                    self._cond.notify_all()

    def _post(self, payload: dict, timeout: float | None = None) -> dict:
        if not self._ready.wait(timeout or self._timeout):
            raise McpError(f"[{self.name}] SSE endpoint 未就绪")
        if not self._endpoint:
            raise McpError(f"[{self.name}] 未获得 SSE endpoint")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._headers,
        }
        req = urllib.request.Request(self._endpoint, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout or self._timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        # 服务器可能直接在 POST 响应里返回（简单实现），否则等 SSE message 事件
        msg = _json_lines_response(raw)
        if msg.get("id") is not None:
            return msg
        return {}

    def _request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        msg = self._post(payload, timeout)
        if msg.get("error"):
            raise McpError(str(msg["error"]))
        if msg.get("result") is not None:
            return msg.get("result") or {}
        # 等待 SSE 事件流里的 message
        deadline = time.time() + (timeout or self._timeout)
        while time.time() < deadline:
            with self._cond:
                if req_id in self._responses:
                    resp = self._responses.pop(req_id)
                    break
                self._cond.wait(0.2)
        else:
            raise McpError(f"[{self.name}] SSE 请求超时：{method}")
        if resp.get("error"):
            raise McpError(str(resp["error"]))
        return resp.get("result") or {}

    def _notify(self, method: str, params: dict | None = None) -> None:
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._post(payload, timeout=10)
        except Exception:
            pass

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
            if self._reader:
                self._reader.close()
        except Exception:
            pass


# ==================== Hub：多服务器管理 ====================
def _build_session(name: str, cfg: dict):
    """按配置构建会话：url+type → 远程；command → stdio；否则报错。"""
    url = cfg.get("url")
    cmd = cfg.get("command")
    if url:
        typ = str(cfg.get("type") or "http").strip().lower()
        headers = cfg.get("headers") or {}
        if typ == "sse":
            return SseMcpSession(name, url, headers)
        return HttpMcpSession(name, url, headers)
    if cmd:
        return McpSession(name, cmd, cfg.get("args") or [], cfg.get("env") or {})
    raise McpError(f"[{name}] 配置需包含 url（远程）或 command（本地 stdio）")


class McpHub:
    def __init__(self):
        self.sessions: dict[str, object] = {}
        self.tool_index: dict[str, tuple[str, str]] = {}  # tool_key -> (server, tool)
        self.tool_meta: dict[str, str] = {}  # tool_key -> description
        self.server_status: dict[str, str] = {}  # server -> "ready"|"failed"|"starting"|"skipped"

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
            self.server_status[name] = "starting"
            try:
                sess = _build_session(name, cfg)
                sess.initialize()
                self.sessions[name] = sess
                for tool in sess.list_tools():
                    tname = tool.get("name")
                    if not tname:
                        continue
                    desc = (tool.get("description") or "")[:200]
                    if tname in self.tool_index:
                        # 多台服务器提供同名工具：生成 <server>__<tool> 别名，保留首台归属
                        alias = f"{name}__{tname}"
                        self.tool_index[alias] = (name, tname)
                        self.tool_meta[alias] = desc
                    else:
                        self.tool_index[tname] = (name, tname)
                        self.tool_meta[tname] = desc
                self.server_status[name] = "ready"
            except Exception as e:
                self.server_status[name] = "failed"
                import sys
                print(f"[MCP] server '{name}' init failed: {e}", file=sys.stderr)

    def call(self, server: str, tool: str, arguments: dict | None = None) -> str:
        sess = self.sessions.get(server)
        if not sess:
            raise McpError(f"未知 MCP 服务器：{server}")
        return sess.call_tool(tool, arguments or {})

    def list_catalog(self) -> list[dict]:
        out = []
        for key, (server, tool) in self.tool_index.items():
            sess = self.sessions.get(server)
            stype = type(sess).__name__ if sess else ""
            if "Http" in stype:
                stype = "http"
            elif "Sse" in stype:
                stype = "sse"
            elif "Mcp" in stype:
                stype = "stdio"
            else:
                stype = ""
            out.append(
                {
                    "server": server,
                    "name": tool,
                    "alias": key if key != tool else "",
                    "type": stype,
                    "description": self.tool_meta.get(key, ""),
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
        self.tool_meta.clear()
        self.server_status.clear()


_hub: McpHub | None = None


def get_hub() -> McpHub:
    global _hub
    if _hub is None:
        _hub = McpHub()
    return _hub