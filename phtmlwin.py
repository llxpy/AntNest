"""
PHtmlWin — 用写 HTML 的轻松感写 Python 桌面 UI。

核心思路
--------
- 用一套 Pythonic 的元素 DSL（ui.div[ ui.h1("...") ]）描述界面，像写 HTML 一样直观。
- 渲染成真实桌面窗口：优先用 pywebview（原生 OS webview，轻量）；
  没有 pywebview 时自动回退到「标准库本地服务器 + 浏览器」，零三方依赖也能跑。
- Python 侧可绑定事件（button 点击等）并实时改 DOM（app.update(selector, html)）。

这不是从零造渲染引擎，而是把「HTML 字符串 / webview 窗口 / Python↔JS 桥」包成
一层好用的 API。定位上对标 NiceGUI / Flet / Eel，但更轻、且默认带暗面构建设计 token。

依赖
----
- 桌面窗口模式：pip install pywebview（可选，没有就走浏览器回退）。
- 库本身 import 不依赖任何三方包；bridge / 服务器全用标准库。
"""
from __future__ import annotations

import html as _html
import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

__all__ = ["El", "Win", "ui", "el"]


# --------------------------------------------------------------------------
# 元素 DSL
# --------------------------------------------------------------------------
class _Text:
    def __init__(self, text: str) -> None:
        self.text = text

    def render(self) -> str:
        return _html.escape(str(self.text))


class El:
    """一个 HTML 元素。支持 ui.div(cls="x")[ child, child ] 这种写法。"""

    VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, tag: str, **attrs) -> None:
        self.tag = tag
        self.attrs: dict = {}
        self._bind = None  # (event, route)
        self.children: list = []
        for k, v in attrs.items():
            if k == "cls":
                self.attrs["class"] = v
            elif k == "on" and isinstance(v, tuple) and len(v) == 2:
                self._bind = v
            elif k == "bind" and isinstance(v, tuple) and len(v) == 2:
                self._bind = v
            elif k == "onclick" and isinstance(v, str):
                # 含 JS 调用语法 → 原生 onclick；简单标识符 → 走路由绑定
                if any(c in v for c in "() ."):
                    self.attrs["onclick"] = v
                else:
                    self._bind = ("click", v)
            elif k == "oninput" and isinstance(v, str):
                self._bind = ("input", v)
            elif k == "onchange" and isinstance(v, str):
                self._bind = ("change", v)
            else:
                self.attrs[k] = v

    def __call__(self, *kids):
        self.children.extend(kids)
        return self

    def __getitem__(self, kids):
        if not isinstance(kids, tuple):
            kids = (kids,)
        self.children.extend(kids)
        return self

    def bind(self, event: str, route: str):
        """显式绑定事件到已注册的 route。例：ui.button("Go").bind("click","start")"""
        self._bind = (event, route)
        return self

    def render(self) -> str:
        attrs = dict(self.attrs)
        if self._bind:
            ev, route = self._bind
            attrs["data-phw-bind"] = f"{ev}:{route}"
        attr_str = "".join(
            f' {k}="{_html.escape(str(v), quote=True)}"' for k, v in attrs.items()
        )
        if self.tag in self.VOID:
            return f"<{self.tag}{attr_str}/>"
        inner = "".join(
            c.render() if hasattr(c, "render") else _html.escape(str(c))
            for c in self.children
        )
        return f"<{self.tag}{attr_str}>{inner}</{self.tag}>"


def _mk(tag: str):
    def factory(*children, **attrs):
        e = El(tag, **attrs)
        if children:
            e(*children)
        return e

    factory.__name__ = tag
    return factory


class _UI:
    TAGS = [
        "div", "span", "section", "aside", "main", "header", "footer", "nav",
        "h1", "h2", "h3", "h4", "p", "a", "button", "input", "img", "ul",
        "ol", "li", "pre", "code", "label", "form", "small", "strong", "em",
        "table", "tr", "td", "th", "br", "hr",
    ]

    def __getattr__(self, name: str):
        if name in self.TAGS:
            return _mk(name)
        raise AttributeError(f"ui 没有标签 {name!r}")

    def el(self, tag: str, **attrs) -> El:
        """任意标签：ui.el('video', src='x.mp4')"""
        return El(tag, **attrs)

    def raw(self, html_str: str):
        """直接塞原始 HTML（不经过转义）。"""

        class _Raw:
            def render(self) -> str:
                return html_str

        return _Raw()


ui = _UI()


def el(tag: str, **attrs) -> El:
    """模块级快捷：el('video', src='x')"""
    return El(tag, **attrs)


# --------------------------------------------------------------------------
# 桥接 JS（webview 模式 / 浏览器模式两种实现）
# --------------------------------------------------------------------------
_BRIDGE_TPL = """
(function(){
  var PHW = {
    _q: [],
    _ready: false,
    route: function(name, data){
      data = data || {};
      {MODE_ROUTE}
    },
    _flush: function(){
      PHW._ready = true;
      var q = PHW._q; PHW._q = [];
      q.forEach(function(a){ PHW.route(a[0], a[1]); });
    }
  };
  // 对外暴露：页面里的自定义 JS 用 window.PHW.route(name, data)
  window.PHW = PHW;
  window.pybridge = PHW;   // 兼容旧写法
  document.addEventListener('click', function(e){
    var t = e.target.closest('[data-phw-bind]'); if(!t) return;
    var p = (t.getAttribute('data-phw-bind')||'').split(':');
    if(p[0] !== 'click') return;
    PHW.route(p[1], {value: (t.value || t.textContent || '')});
  });
  ['input','change'].forEach(function(ev){
    document.addEventListener(ev, function(e){
      var t = e.target.closest('[data-phw-bind]'); if(!t) return;
      var p = (t.getAttribute('data-phw-bind')||'').split(':');
      if(p[0] !== ev) return;
      PHW.route(p[1], {value: t.value});
    }, true);
  });
  {SSE}
})();
"""

_PAGE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
{css}
/* 管理员模式 IME 修复：确保输入法候选框能正常显示 */
/* Windows UAC 提升后 WebView2 的 IME 候选窗可能被遮挡 */
input, textarea, [contenteditable] {
    ime-mode: active !important;
    -webkit-ime-mode: active !important;
    -ms-ime-mode: active !important;
}
/* 确保输入框不使用 z-index 遮挡 IME 窗口 */
input:focus, textarea:focus {
    z-index: auto !important;
    position: relative !important;
}
/* 修复 WebView2 在管理员模式下的 IME 候选框位置 */
@supports (-webkit-overflow-scrolling: touch) {
    input, textarea {
        -webkit-ime-mode: active;
    }
}
</style>
</head>
<body>
{body}
<script>{bridge}</script>
</body>
</html>"""


# --------------------------------------------------------------------------
# Win —— 一个桌面窗口应用
# --------------------------------------------------------------------------
class Win:
    def __init__(self, title: str = "PHtmlWin", width: int = 1000,
                 height: int = 700, backend: str = None,
                 icon: str = None, gui: str = None) -> None:
        self.title = title
        self.width = width
        self.height = height
        self.icon = icon              # webview 窗口图标路径（.ico）
        self._css = ""
        self._body_children: list = []
        self._routes: dict = {}
        self._updates: list = []      # 浏览器模式下的 SSE 推送队列
        self._backend = backend       # "webview" | "browser" | None(自动)
        self._gui = gui               # pywebview 渲染后端：None=自动(Windows→edgechromium)
                                       #   / "edgechromium"(WebView2) / "cef"(CEF，IME 候选窗可靠)
        self._window = None
        self._is_admin = False        # 管理员模式标记（用于 IME 修复）

    # ---- 声明式 API ----
    def css(self, css: str):
        self._css += "\n" + css
        return self

    def body(self, *nodes):
        self._body_children.extend(nodes)
        return self

    def route(self, name: str):
        """装饰器：注册一个事件处理函数。@app.route("start") def f(data): ..."""
        def deco(fn):
            self._routes[name] = fn
            return fn
        return deco

    def run_js(self, js: str):
        """执行任意 JS（webview 模式丢独立线程；浏览器模式走 SSE 推送）。"""
        if self._backend == "webview" and self._window is not None:
            threading.Thread(
                target=self._window.evaluate_js, args=(js,), daemon=True
            ).start()
        else:
            self._updates.append(js)

    def update(self, selector: str, html: str):
        """实时把 selector 匹配元素的 innerHTML 替换为 html。"""
        self.run_js(
            f"(function(){{var el=document.querySelector({selector!r});"
            f" if(el) el.innerHTML={html!r};}})()"
        )

    # ---- 渲染 ----
    def _render_node(self, n):
        if hasattr(n, "render"):
            return n.render()
        return _html.escape(str(n))

    def render(self) -> str:
        if self._backend is None:
            self._backend = self._detect_backend()
        body = "".join(self._render_node(n) for n in self._body_children)
        page = _PAGE
        page = (page.replace("{title}", self.title)
                    .replace("{css}", self._css)
                    .replace("{body}", body)
                    .replace("{bridge}", self._bridge_js()))
        return page

    def _detect_backend(self) -> str:
        try:
            import webview  # noqa: F401
            return "webview"
        except Exception:
            return "browser"

    def _bridge_js(self) -> str:
        mode = "webview" if self._backend == "webview" else "browser"
        if mode == "webview":
            # pywebview 把 js_api 暴露成 window.pywebview.api.<方法名>（异步注入，
            # 页面刚加载时还不存在）。所以：没就绪先排队，pywebviewready 后统一冲刷。
            route_impl = (
                "if(window.pywebview && window.pywebview.api && window.pywebview.api.route){"
                "  window.pywebview.api.route(name, data);"
                "}else{"
                "  PHW._q.push([name, data]);"
                "}"
            )
            sse = ("window.addEventListener('pywebviewready', function(){ PHW._flush(); });"
                   "if(window.pywebview && window.pywebview.api){ PHW._flush(); }")
        else:
            route_impl = (
                "fetch('/api/route',{method:'POST',"
                "headers:{'Content-Type':'application/json'},"
                "body:JSON.stringify({route:name,data:data})})"
                ".then(function(r){return r.json();})"
                ".then(function(j){(j.updates||[]).forEach(function(u){eval(u);});});"
            )
            sse = ("var es=new EventSource('/api/events');"
                   "es.onmessage=function(e){try{eval(e.data);}catch(_){}};")
        return _BRIDGE_TPL.replace("{MODE_ROUTE}", route_impl).replace("{SSE}", sse)

    # ---- 运行 ----
    def run(self) -> None:
        if self._backend is None:
            self._backend = self._detect_backend()
        if self._backend == "webview":
            self._start_webview()
        else:
            self._start_browser()

    def _start_webview(self) -> None:
        import webview

        page = self.render()

        class Bridge:
            def __init__(self, app):
                self.app = app

            def route(self, name, data):
                fn = self.app._routes.get(name)
                if fn:
                    fn(data)

        kwargs = {"title": self.title, "html": page,
                  "width": self.width, "height": self.height,
                  "js_api": Bridge(self)}
        if self._gui:
            kwargs["gui"] = self._gui
        self._window = webview.create_window(**kwargs)
        start_kwargs = {}
        if self.icon:
            start_kwargs["icon"] = self.icon
        if self._gui:
            start_kwargs["gui"] = self._gui

        # 管理员模式 IME 修复：设置用户数据目录和 WebView2 参数
        # Windows UAC 提升后 WebView2 的 IME 候选窗无法正常显示
        # 解决方案：设置独立的用户数据目录并启用 IME 支持
        import ctypes
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            is_admin = False

        if is_admin:
            self._is_admin = True
            # 设置 WebView2 用户数据目录（避免与非管理员进程冲突）
            user_data_dir = os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "AntNest", "WebView2_Data_Admin"
            )
            os.makedirs(user_data_dir, exist_ok=True)
            os.environ["WEBVIEW2_USER_DATA_FOLDER"] = user_data_dir
            print(f"[PHtmlWin] 管理员模式：WebView2 用户数据目录 -> {user_data_dir}")
            print(f"[PHtmlWin] 管理员模式：IME 修复已启用")

        # 最小化到任务栏：关闭窗口时最小化而非退出
        self._minimize_on_close = True
        self._on_close_callback = None

        def _on_closing():
            """拦截关闭事件：最小化到任务栏而非退出"""
            if self._minimize_on_close and self._window:
                try:
                    # 最小化窗口
                    self._window.minimize()
                    return False  # 阻止关闭
                except Exception:
                    pass
            return True  # 允许关闭

        # 注册关闭回调
        try:
            self._window.events.closing += lambda: _on_closing()
        except Exception:
            pass

        try:
            webview.start(**start_kwargs)
        except Exception as e:
            import sys
            import traceback
            import os
            print(f"[PHtmlWin] WebView2 initialization failed: {e}", file=sys.stderr)
            print("[PHtmlWin] Falling back to browser mode...", file=sys.stderr)
            # 写入 startup_error.log，方便用户定位问题
            try:
                ant_home = os.environ.get("ANT_HOME") or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".antnest")
                os.makedirs(ant_home, exist_ok=True)
                log_path = os.path.join(ant_home, "startup_error.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(f"[PHtmlWin] WebView2 initialization failed\n")
                    f.write(f"Error: {e}\n")
                    f.write(f"Falling back to browser mode\n\n")
                    f.write(traceback.format_exc())
            except Exception:
                pass
            self._backend = "browser"
            self._start_browser()

    def _start_browser(self) -> None:
        app_self = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                p = urlparse(self.path)
                if p.path in ("/", ""):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(app_self.render().encode("utf-8"))
                elif p.path == "/api/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    try:
                        while True:
                            if app_self._updates:
                                u = app_self._updates.pop(0)
                                self.wfile.write(f"data: {json.dumps(u)}\n\n".encode())
                                self.wfile.flush()
                            else:
                                self.wfile.write(b": ping\n\n")
                                self.wfile.flush()
                            time.sleep(0.2)
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # 客户端断开，安静退出该 SSE 线程
                else:
                    self.send_error(404)

            def do_POST(self):
                p = urlparse(self.path)
                if p.path == "/api/route":
                    n = int(self.headers.get("Content-Length", 0) or 0)
                    raw = self.rfile.read(n) if n else b"{}"
                    payload = json.loads(raw) if raw else {}
                    name = payload.get("route")
                    data = payload.get("data", {})
                    fn = app_self._routes.get(name)
                    if fn:
                        fn(data)
                    ups = list(app_self._updates)
                    app_self._updates.clear()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"updates": ups}).encode())
                else:
                    self.send_error(404)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_address[1]
        print(f"PHtmlWin → http://127.0.0.1:{port}  (正在打开浏览器)")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        except Exception:
            pass
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            srv.shutdown()


if __name__ == "__main__":
    # 最小自测：不依赖显示，验证 DSL + 渲染 + bridge 生成
    a = Win("self-test")
    a.css("body{color:red}")
    a.body(ui.div(cls="card", id="x")[ui.h1("Hi"), ui.button("Go", onclick="start")])
    out = a.render()
    assert 'data-phw-bind="click:start"' in out
    assert "<h1>Hi</h1>" in out
    assert 'id="x"' in out
    print("PHtmlWin self-test OK, html length =", len(out))
