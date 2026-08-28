"""
AntNest UI · 聊天 + 工蚁监控 + 设置（已接入真实核心）

界面范式：
- 左侧：和蚁后聊天的窗口
- 右侧：主页/监控（当前任务、子任务、工蚁状态、结构化日志）
- 右上角：设置入口（LLM / AntNest / MCP / Skills）

数据来源：antnest_bridge.AntNestCore —— 零侵入包装 AntNest.py。
UI 只做「订阅事件 → 改状态 → 局部刷新」，不含任何业务逻辑。
出问题看 .antnest/ui_trace.log，按 #序号 + S阶段 定位。

运行：uv run python prototype_antnest.py
"""
import sys
import os
import traceback
import json
import threading

# ---------------------------------------------------------------------------
# 单实例锁：防止多开
# ---------------------------------------------------------------------------
_SINGLETON_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".antnest", ".lock")
_LOCK_STALE_SECONDS = 6 * 3600  # 锁超过 6 小时视为陈旧残留（闪退/断电/重启后）

def _lock_is_stale(lock_ts):
    """锁时间戳距今超过阈值即判定陈旧。无时间戳（旧格式）也视为陈旧。"""
    try:
        lock_ts = float(lock_ts or 0)
    except (TypeError, ValueError):
        return True
    if lock_ts <= 0:
        return True
    return (time.time() - lock_ts) > _LOCK_STALE_SECONDS


def _singleton_alive(pid):
    """探测 PID 是否存活；非 Windows 直接看 /proc。"""
    try:
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        return os.path.exists(f"/proc/{pid}")
    except Exception:
        return False


def _clear_stale_lock():
    try:
        if os.path.exists(_SINGLETON_LOCK_FILE):
            os.remove(_SINGLETON_LOCK_FILE)
    except Exception:
        pass


def _check_singleton():
    """检查是否已有实例在运行。如果有，弹窗提示并退出。
    自愈：闪退/断电/重启后的陈旧锁自动清除——进程不可达、锁龄超阈值、
    或上次启动异常（startup_error.log 存在）都会放行，绝不因残留锁挡住启动。
    """
    lock_dir = os.path.dirname(_SINGLETON_LOCK_FILE)
    os.makedirs(lock_dir, exist_ok=True)

    # 上次会话异常退出 → 清锁放行（崩溃残留的锁唯一来源就是异常退出）
    err_log = os.path.join(lock_dir, "startup_error.log")
    if os.path.exists(err_log):
        try: os.remove(err_log)
        except Exception: pass
        _clear_stale_lock()

    if os.path.exists(_SINGLETON_LOCK_FILE):
        old_pid, lock_ts = None, None
        try:
            with open(_SINGLETON_LOCK_FILE, "r") as f:
                lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
            if lines:
                old_pid = int(lines[0])
            if len(lines) > 1:
                lock_ts = lines[1]
        except (ValueError, IOError):
            pass

        stale = _lock_is_stale(lock_ts)
        alive = _singleton_alive(old_pid) if old_pid else False
        if alive and not stale:
            # 进程真的在跑且锁未过期 → 才提示已在运行
            try:
                import ctypes
                import ctypes.wintypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"AntNest 已经在运行中（PID: {old_pid}）。\n请先关闭现有窗口，或从系统托盘打开。",
                    "AntNest",
                    0x40 | 0x0  # MB_ICONINFORMATION | MB_OK
                )
            except Exception:
                print(f"AntNest 已经在运行中（PID: {old_pid}）")
            sys.exit(0)
        if not alive or stale:
            # 进程已死或锁陈旧 → 清锁，允许本次启动
            _clear_stale_lock()

    # 写入当前 PID + 启动时间戳（供下次启动判断锁是否陈旧）
    try:
        with open(_SINGLETON_LOCK_FILE, "w") as f:
            f.write(f"{os.getpid()}\n{time.time()}\n")
    except Exception:
        pass

def _release_singleton():
    """释放单实例锁"""
    try:
        if os.path.exists(_SINGLETON_LOCK_FILE):
            os.remove(_SINGLETON_LOCK_FILE)
    except Exception:
        pass

# 启动时检查单实例
_check_singleton()

# ---------------------------------------------------------------------------
# 全局致命错误钩子：任何崩溃（含导入期 / 启动前）都弹窗 + 写日志，
# 绝不让 pythonw 静默退出导致「双击 exe 啥也没有」。
# 必须在其它 import 之前安装，才能罩住它们的导入错误。
# ---------------------------------------------------------------------------
def _show_fatal(err_text: str) -> None:
    try:
        _d = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".antnest")
        os.makedirs(_d, exist_ok=True)
        with open(os.path.join(_d, "startup_error.log"), "w", encoding="utf-8") as _f:
            _f.write(err_text)
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "AntNest 启动失败：\n" + err_text[-2000:],
                "AntNest",
                0x10,
            )
        except Exception:
            pass


def _global_excepthook(et, ev, tb):
    _show_fatal("".join(traceback.format_exception(et, ev, tb)))


sys.excepthook = _global_excepthook

import base64
import html as _h
import json
import mimetypes
import re
import threading
import time
import urllib.request
import webbrowser


def _user_data_dir():
    """安装版可写状态根目录：Windows → %LOCALAPPDATA%/AntNest；其他 → ~/.local/share/AntNest。"""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = os.path.join(base, "AntNest")
    os.makedirs(d, exist_ok=True)
    return d


# 仅当以「已安装」模式启动（启动器设 ANT_INSTALLED=1）时，把可写状态迁到用户数据目录；
# dev 模式（仓库内直接 uv run）保持写本地，避免污染/混淆。环境变量已设则尊重之。
if os.environ.get("ANT_INSTALLED") == "1":
    _DATA = _user_data_dir()
    os.environ.setdefault("ANT_HOME", os.path.join(_DATA, ".antnest"))
    os.environ.setdefault("ANT_CONFIG_PATH", os.path.join(_DATA, "config.json"))
    os.environ.setdefault("ANT_UI_CONFIG_PATH", os.path.join(_DATA, "ui_config.json"))
    os.environ.setdefault("ANT_TRACE_DIR", os.path.join(_DATA, "trace"))

from phtmlwin import Win, ui
import antnest_bridge as bridge
from ui_assets_loader import load_css, load_js, load_image_data_uri
import ui_render
import ctypes

# 检测管理员权限
try:
    _is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
except Exception:
    _is_admin = False

# 应用/交互图标（favicon）：窗口图标由 antnest.ico 承担，页面 favicon 与顶栏品牌
# 统一使用 favicon.ico（data-URI）与 48px 透明 PNG（app-icon.png），多端一致。
def _load_favicon_data_uri():
    try:
        return load_image_data_uri("favicon.ico")
    except Exception:
        return ""

try:
    _APP_ICON_SRC = load_image_data_uri("app-icon.png")
except Exception:
    _APP_ICON_SRC = ""

app = Win(title="AntNest · 在暗面构建", width=1260, height=800,
          gui=os.environ.get("ANT_WEBVIEW_GUI") or None,
          icon=os.path.join(os.path.dirname(os.path.abspath(__file__)), "antnest.ico"),
          favicon=_load_favicon_data_uri())

# 设置管理员模式标记
app._is_admin = _is_admin

core = bridge.AntNestCore()

# 当前版本：从 pyproject.toml 读，单一事实来源；发版时只改 pyproject.toml
def _read_app_version():
    try:
        _pp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyproject.toml")
        with open(_pp, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line.startswith("version"):
                    return _line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "0.0.0"
APP_VERSION = _read_app_version()

# 蚁后二次元形象（透明底 PNG，随 ui_assets/queen-avatar.png 分发）：
# 以 data-URI 注入页面，顶栏品牌图与聊天气泡头像统一用它；读取失败回退 SVG。
# 蚁后立绘（透明底全图），用于设置抽屉顶部与启动页空白位展示，不作头像。
try:
    _QUEEN_PORTRAIT_SRC = load_image_data_uri("queen-portrait.webp")
except Exception:
    _QUEEN_PORTRAIT_SRC = ""


# 收款二维码目录（assets/donate/，打包后随 app 分发，不依赖本机路径）。
# 点击「赞助作者」读取该目录里 WX/ZFB 二维码并展示；只匹配文件名含
# wx/wechat/微信/zfb/alipay/支付宝 的图片，敏感/无关图片自动过滤。
def _sponsor_dir():
    _base = os.path.dirname(os.path.abspath(__file__))
    for _c in (os.path.join(_base, "assets", "donate"), os.path.join(_base, "..", "assets", "donate")):
        if os.path.isdir(_c):
            return os.path.abspath(_c)
    return os.path.join(_base, "assets", "donate")
SPONSOR_DIR = _sponsor_dir()


def _version_tuple(v):
    _parts = []
    for _p in re.split(r"[.\-+]", str(v or "")):
        _m = re.match(r"\d+", _p)
        _parts.append(int(_m.group()) if _m else 0)
    return tuple(_parts)


def _version_gt(a, b):
    return _version_tuple(a) > _version_tuple(b)


def _classify_command(command):
    """从原始 shell 命令中提取人类可读的操作描述。"""
    cmd = (command or "").strip()
    if not cmd:
        return "执行命令"
    c = cmd.lower()
    # PowerShell 文件操作
    if re.search(r"Get-ChildItem", cmd, re.I):
        m = re.search(r"Get-ChildItem\s+['\"]?([^'\"\s]+)", cmd, re.I)
        return f"列出目录：{m.group(1)}" if m else "列出目录"
    if re.search(r"Get-Content", cmd, re.I):
        m = re.search(r"Get-Content\s+['\"]?([^'\"\s]+)", cmd, re.I)
        return f"读取文件：{m.group(1)}" if m else "读取文件"
    if re.search(r"Set-Content|Out-File|Add-Content", cmd, re.I):
        m = re.search(r"(?:Set-Content|Out-File|Add-Content)\s+['\"]?([^'\"\s]+)", cmd, re.I)
        return f"写入文件：{m.group(1)}" if m else "写入文件"
    # Python
    if re.search(r"python\s+", c) or re.search(r"python\.exe\s+", c):
        m = re.search(r"python(?:\.exe)?\s+(?:-\S+\s+)*['\"]?([^'\"\s]+\.py)", c)
        return f"执行 Python：{m.group(1)}" if m else "执行 Python 脚本"
    # Git
    if re.search(r"git\s+", c):
        m = re.search(r"git\s+(\w+)", c)
        if m:
            sub = m.group(1)
            desc_map = {
                "add": "暂存文件", "commit": "提交变更", "push": "推送代码",
                "pull": "拉取代码", "clone": "克隆仓库", "checkout": "切换分支",
                "merge": "合并分支", "status": "查看状态", "diff": "查看差异",
                "log": "查看日志", "branch": "管理分支", "fetch": "获取远程更新",
                "stash": "暂存工作", "reset": "重置变更",
            }
            return f"Git {desc_map.get(sub, sub)}"
        return "执行 Git 操作"
    # npm / node
    if re.search(r"npm\s+", c):
        m = re.search(r"npm\s+(\w+)", c)
        return f"npm {m.group(1)}" if m else "执行 npm"
    # docker
    if re.search(r"docker\s+", c):
        m = re.search(r"docker\s+(\w+)", c)
        return f"Docker {m.group(1)}" if m else "执行 Docker 操作"
    # 测试/构建
    if re.search(r"pytest|unittest|go test|cargo test", c):
        return "运行测试"
    if re.search(r"make|cmake|cargo build|go build|mvn|gradle", c):
        return "构建项目"
    # pip
    if re.search(r"pip\s+", c):
        m = re.search(r"pip\s+(\w+)\s+(\S+)", c)
        if m and m.group(1) == "install":
            return f"安装依赖：{m.group(2)}"
        return f"pip {m.group(1)}" if m else "执行 pip"
    # 通用 shell
    if re.search(r"Write-Output|echo\s+", cmd, re.I):
        return "输出内容"
    # 截取前40字作为兜底描述
    return cmd[:40].replace("\n", " ") + ("…" if len(cmd) > 40 else "")


def check_update(silent=True):
    """后台检查 GitHub Release 是否有新版本；有则弹提示。失败/离线/限流/无新版均静默。
    silent=False 时为手动点击「检查更新」按钮，会显示检查结果。"""
    if silent:
        time.sleep(4)  # 等窗口真正就绪，避免 run_js 在窗口创建前被丢弃
    try:
        _url = "https://api.github.com/repos/llxpy/AntNest/releases/latest"
        _req = urllib.request.Request(_url, headers={"User-Agent": "AntNest"})
        with urllib.request.urlopen(_req, timeout=8) as _resp:
            _data = json.loads(_resp.read().decode("utf-8"))
        _tag = (str(_data.get("tag_name") or "").lstrip("vV")) or None
        _html = _data.get("html_url", "") or ""
        if _tag and _version_gt(_tag, APP_VERSION):
            app.run_js(
                f"showUpdateBanner({json.dumps(_tag)}, {json.dumps(_html)}, {json.dumps(APP_VERSION)});"
            )
        elif not silent and _tag:
            app.run_js(
                f"(function(){{ var btn=document.getElementById('btn-check-update');"
                f"if(btn){{btn.textContent='已是最新';btn.disabled=false;}}"
                f"setTimeout(function(){{if(btn)btn.textContent='⬆ 检查更新';}},2000);}})();"
            )
        elif not silent:
            app.run_js(
                "(function(){{ var btn=document.getElementById('btn-check-update');"
                "if(btn){btn.textContent='检查失败';btn.disabled=false;}"
                "setTimeout(function(){if(btn)btn.textContent='⬆ 检查更新';},2000);}})();"
            )
    except Exception:
        if not silent:
            app.run_js(
                "(function(){{ var btn=document.getElementById('btn-check-update');"
                "if(btn){btn.textContent='网络异常';btn.disabled=false;}"
                "setTimeout(function(){if(btn)btn.textContent='⬆ 检查更新';},2000);}})();"
            )

# ------------------------------------------------------------------ 设计 token
app.css(load_css())

# ------------------------------------------------------------------ 运行时状态
STATE = {"task": "尚未下达任务"}
SUBTASKS = []          # [{id, title, worker, status, msg}]
WORKERS = []           # [{id, name, status, task, note, artifacts}]
_SESSION_ID = "default"  # 当前会话 id（对话记录用；默认与旧版单会话兼容）
CHATS = []             # [{"role": "user"|"queen", "text": str, "reasoning": str}]
_STREAM_BUF = {"active": False, "text": "", "reasoning": ""}
_LOG_DOM_COUNT = 0     # 已追加到 DOM 的日志条数（避免整页刷新打断选中）
LOGS = []              # [(time, tag, text)]
CHAT_DRAFT = {"v": ""}
SELECTED_SKILL = ""    # 聊天框左侧选中的 skill
SELECTED_IMAGE = None  # {b64, mime} or None
SKILLS_CACHE = []      # 缓存的 skills 列表
MAX_LOGS = 500
MAX_CHATS = 500           # 内存中保留的聊天条数上限
MAX_CHATS_RENDER = 300    # DOM 渲染的聊天条数上限
MAX_PANEL_ITEMS = 6       # 监控面板内子任务/工蚁卡片渲染条数上限（防长列表卡顿）
MAX_MODAL_ITEMS = 80      # 展开模态内子任务/工蚁卡片渲染条数上限（防任务多卡顿）

# 配置由 bridge 统一管理：
#   LLM / agent 参数 → config.json（AntNest 的嵌套结构，原样保留）
#   外观 / UI-only   → ui_config.json
SETTINGS, APPEARANCE = bridge.load_settings()


def _ensure_memory_files():
    """自检#1 修复：确保 NEST.md / hints.md 存在——
    记忆线索层在文件缺失时会静默失效（显示"无"其实是文件不存在），
    启动时补占位文件，让检索层始终有可建索引的载体。"""
    home = os.environ.get("ANT_HOME") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".antnest")
    for d in (home, os.path.join(os.getcwd(), ".antnest")):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
    for p in (
        os.path.join(home, "NEST.md"),
        os.path.join(os.getcwd(), ".antnest", "hints.md"),
    ):
        if not os.path.exists(p):
            try:
                open(p, "w", encoding="utf-8").write("")
            except Exception:
                pass


_ensure_memory_files()


# ---------------------------------------------------------------------------
# 会话自动加载：启动时恢复上次对话
# ---------------------------------------------------------------------------
def _auto_load_last_session():
    """启动时自动加载最近一次会话的对话记录到 CHATS。"""
    global CHATS, _SESSION_ID
    try:
        import antnest_session as _sm
        m = core.mod
        if not m:
            return
        sessions = _sm.list_project_sessions(m.PROJECT_DIR, m.SESSION_DIR)
        if not sessions:
            return
        # 取最近一个会话
        last = sessions[0]
        _SESSION_ID = last.get("id", "default")
        session_file = _sm.get_session_file(m.PROJECT_DIR, m.SESSION_DIR, _SESSION_ID)
        if not os.path.exists(session_file):
            return
        with open(session_file, "r", encoding="utf-8") as f:
            messages = json.load(f)
        if not isinstance(messages, list):
            return
        # 从 messages 中提取 user/assistant 对话
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user" and content:
                CHATS.append({"role": "user", "text": content, "reasoning": ""})
            elif role == "assistant" and content:
                reasoning = msg.get("reasoning_content", "") or ""
                if msg.get("stop_snapshot"):
                    content = "⏹ 已保存的中断摘要\n" + content
                CHATS.append({"role": "queen", "text": content, "reasoning": reasoning})
        # 限制条数
        if len(CHATS) > MAX_CHATS:
            CHATS[:] = CHATS[-MAX_CHATS:]
        bridge.trace("UI", "info", f"自动加载会话 [{_SESSION_ID}]，{len(CHATS)} 条对话")
    except Exception as e:
        bridge.trace("UI", "warn", f"自动加载会话失败：{e}")

_auto_load_last_session()


# ------------------------------------------------------------------ 事件 → UI
# 核心跑在后台线程，事件会高频到达（流式输出）。这里做 150ms 合并刷新，
# 避免每行日志都触发一次 evaluate_js 把 webview 打爆。
_dirty = set()
_dirty_lock = threading.Lock()
_timer = {"t": None}


def _mark(*parts):
    with _dirty_lock:
        _dirty.update(parts)
        if _timer["t"] is None:
            t = threading.Timer(0.15, _flush)
            t.daemon = True
            _timer["t"] = t
            t.start()


def _flush():
    with _dirty_lock:
        parts = set(_dirty)
        _dirty.clear()
        _timer["t"] = None
    try:
        if "chat" in parts:
            if _STREAM_BUF.get("active"):
                pass  # 流式进行中由 JS 增量更新，避免 innerHTML 全量替换
            else:
                # 只渲染最近 MAX_CHATS_RENDER 条，防止长会话 DOM 无限膨胀导致卡死
                app.update("#chat-msgs", ui_render.render_chat(CHATS[-MAX_CHATS_RENDER:]))
                app.run_js("scrollChat();")
        if "log" in parts:
            # 状态卡只更新自身（任务/时间/状态/存活），不再连带重灌展开模态——
            # 模态网格只在 subtasks/workers 变更时同步，任务多时避免每 150ms 全量重建卡顿。
            _render_status_card()
        if "subtasks" in parts:
            app.update("#subtasks", _subtasks_html(limit=MAX_PANEL_ITEMS))
            app.update("#subtasks-modal-list", _subtasks_html(limit=MAX_MODAL_ITEMS))
        if "workers" in parts:
            app.update("#workers", _workers_html(limit=MAX_PANEL_ITEMS))
            app.update("#workers-modal-list", _workers_html(limit=MAX_MODAL_ITEMS))
        if "task" in parts:
            app.update("#task-title", _esc(STATE["task"]))
        if "skills" in parts:
            _render_skills_list()
    except Exception as e:
        bridge.trace("UI", "error", f"刷新失败：{e}")


def _append_logs_to_dom():
    global _LOG_DOM_COUNT
    if _LOG_DOM_COUNT >= len(LOGS):
        return
    chunk = LOGS[_LOG_DOM_COUNT:]
    _LOG_DOM_COUNT = len(LOGS)
    html = "".join(
        f'<div class="log-line">'
        f'<div class="log-time">{_esc(t)}</div>'
        f'<div class="log-tag {_esc(tag)}">{_esc(tag.upper())}</div>'
        f'<div class="log-body">{_esc(text)}</div>'
        f'</div>'
        for t, tag, text in chunk
    )
    app.run_js(
        f"(function(){{var l=document.querySelector('#log'); if(!l)return;"
        f"l.insertAdjacentHTML('beforeend',{json.dumps(html)});"
        f"l.scrollTop=l.scrollHeight;}})();"
    )


def _set_pill(cls, text):
    app.run_js(
        "var p=document.querySelector('#status-pill');"
        f"if(p){{p.className='pill {cls}';p.textContent={json.dumps(text)};}}"
    )


def _push_log(tag, text):
    global _LOG_DOM_COUNT
    LOGS.append((time.strftime("%H:%M:%S"), tag, text))
    if len(LOGS) > MAX_LOGS:
        del LOGS[: len(LOGS) - MAX_LOGS]
        if _LOG_DOM_COUNT > 0:
            # 最旧的条数被裁掉后 DOM 与列表错位（新日志会永远不显示），
            # 清空 DOM，由下一次 flush 全量重建
            _LOG_DOM_COUNT = 0
            app.run_js("var l=document.querySelector('#log'); if(l) l.innerHTML='';")


def _upsert(seq, item, key="id"):
    for i, old in enumerate(seq):
        if old.get(key) == item.get(key):
            seq[i] = {**old, **item}
            return
    seq.append(item)


def _set_agent_status(text=""):
    """更新 Agent 状态条：有文本时显示，空字符串时隐藏。"""
    active = "active" if text else ""
    app.run_js(
        "var s=document.querySelector('#agent-status');"
        "var t=document.querySelector('#agent-status-text');"
        f"if(s) s.className='agent-status '+( {json.dumps(active)} );"
        f"if(t) t.textContent={json.dumps(text)};"
    )


def _auto_save_session():
    """每轮对话结束后自动保存到当前会话文件。"""
    m = core.mod
    if not m or not getattr(m, "messages", None):
        bridge.trace("UI", "warn", "自动保存跳过：core.mod 或 messages 为空")
        return
    try:
        m.save_session(m.messages, m.PROJECT_DIR, m.SESSION_DIR, _SESSION_ID)
        # 验证文件确实写入了
        import antnest_session as _sm
        sf = _sm.get_session_file(m.PROJECT_DIR, m.SESSION_DIR, _SESSION_ID)
        if os.path.exists(sf):
            sz = os.path.getsize(sf)
            bridge.trace("UI", "info", f"自动保存 [{_SESSION_ID}] 成功，{sz} 字节")
        else:
            bridge.trace("UI", "warn", f"自动保存 [{_SESSION_ID}] 调用完成但文件不存在: {sf}")
    except Exception as e:
        bridge.trace("UI", "warn", f"自动保存 [{_SESSION_ID}] 失败：{e}")
        _push_log("warn", f"自动保存会话失败：{e}")
        _mark("log")


def _apply_settings_patch(patch):
    """将模型推荐参数写入内存 SETTINGS 并同步到 core。"""
    if not patch:
        return
    changed = False
    for k, v in patch.items():
        if v is None or k not in SETTINGS:
            continue
        SETTINGS[k] = str(v)
        changed = True
    if changed:
        core.set_ui_settings(SETTINGS)


def _vision_enabled():
    """当前模型是否支持图片：必须「已配置 API Key 且模型支持图片」才放行。"""
    return bool((SETTINGS.get("llm_api_key") or "").strip()) and bool(
        bridge.supports_vision(SETTINGS.get("llm_model", ""), core._models_list)
    )


def _vision_reason():
    """图片按钮被禁用时的提示文案（无 key / 模型不支持）。"""
    if not (SETTINGS.get("llm_api_key") or "").strip():
        return "未配置 API Key，无法发送图片"
    if not bridge.supports_vision(SETTINGS.get("llm_model", ""), core._models_list):
        return "当前模型不支持图片识别"
    return ""


def on_core_event(kind, p):
    """唯一的事件入口。所有 UI 变化都从这里发生，便于回溯。"""
    global SKILLS_CACHE, _LOG_DOM_COUNT
    try:
        if kind == "chat":
            role = p.get("role") if p.get("role") in ("user", "queen") else "queen"
            chat_text = p.get("text", "")
            if p.get("stop_snapshot"):
                chat_text = "⏹ 已保存的中断摘要\n" + str(chat_text or "")
            reasoning_text = p.get("reasoning") or _STREAM_BUF.get("reasoning") or ""
            CHATS.append({
                "role": role,
                "text": chat_text,
                "reasoning": reasoning_text,
            })
            if role == "user":
                STATE["task"] = p.get("text", "")[:200]
                _mark("task")
            _mark("chat")

        elif kind == "stream":
            phase = p.get("phase", "")
            if phase == "start":
                _STREAM_BUF["active"] = True
                _STREAM_BUF["text"] = ""
                _STREAM_BUF["reasoning"] = ""
                app.run_js("startStreamBubble();")
            elif phase == "reasoning_start":
                _set_agent_status("蚁后正在思考…")
                app.run_js("ensureStreamThink();")
            elif phase == "reasoning":
                chunk = p.get("text", "")
                _STREAM_BUF["reasoning"] += chunk
                app.run_js(f"appendStreamThink({json.dumps(chunk)});")
            elif phase == "reasoning_end":
                _set_agent_status("蚁后正在组织回复…")
                app.run_js("closeStreamThink();")
            elif phase == "content":
                chunk = p.get("text", "")
                _STREAM_BUF["text"] += chunk
                app.run_js(f"appendStreamText({json.dumps(chunk)});")
            elif phase == "end":
                _STREAM_BUF["active"] = False
                app.run_js("finalizeStreamBubble();")
                _mark("chat")
            elif phase == "interrupted":
                _STREAM_BUF["active"] = False
                app.run_js(f"preserveInterruptedBubble({json.dumps(p.get('reasoning') or '')});")
                _mark("chat")
            elif phase == "usage":
                try:
                    u = json.loads(p.get("text") or "{}")
                    total = u.get("total_tokens", 0)
                    app.run_js(
                        "var e=document.getElementById('token-usage');"
                        f"if(e) e.textContent='⚡ {int(total):,} tokens';"
                    )
                except Exception:
                    pass

        elif kind == "thinking":
            # 深度思考既在聊天气泡内联展示，也在状态条给出实时反馈
            _set_agent_status("蚁后正在思考…")

        elif kind == "tool":
            st = p.get("state")
            name = p.get("name", "")
            if st == "start":
                args = (p.get("args") or "").strip()
                _set_agent_status(f"正在调用 {name} {args}".rstrip())
            elif st == "error":
                _set_agent_status(f"{name} 出错了，稍等…")
            else:
                _set_agent_status("蚁后正在处理结果…")

        elif kind == "profile":
            strategy = p.get("strategy") or {}
            prefs = p.get("preferences") or []
            _push_log(
                "sys",
                "动态画像已更新："
                f"偏好信号 {', '.join(prefs[-5:]) or '暂无'}；"
                f"验证 {float(strategy.get('verification', 0.55)):.2f}，"
                f"自主推进 {float(strategy.get('autonomy', 0.45)):.2f}，"
                f"高风险前询问 {float(strategy.get('clarification', 0.45)):.2f}",
            )
            _mark("log")

        elif kind == "skills":
            SKILLS_CACHE = p.get("list", [])
            _mark("skills")

        elif kind == "log":
            _push_log(p.get("tag", "sys"), p.get("text", ""))
            _mark("log")

        elif kind == "worker":
            _upsert(WORKERS, {
                "id": p["id"], "name": p.get("name", ""), "status": p.get("status", "run"),
                "task": p.get("task", ""), "note": p.get("note", ""),
                "artifacts": p.get("artifacts", []) or [],
            })
            _mark("workers")

        elif kind == "subtask":
            _upsert(SUBTASKS, {
                "id": p["id"], "title": p.get("title", ""), "worker": p.get("worker", ""),
                "status": p.get("status", "run"), "msg": p.get("msg", ""),
            })
            _mark("subtasks")

        elif kind == "status":
            st = p.get("state")
            if st == "loading":
                _set_pill("thinking", "载入核心")
            elif st == "ready":
                _set_pill("ok", "就绪")
                _push_log("sys", p.get("detail", "核心就绪"))
                # 核心刚起来，把 UI 里的设置和自定义 Agent 灌进去（早于第一次 LLM 调用）
                core.apply_settings(SETTINGS)
                core.apply_custom_agent(APPEARANCE.get("custom_agent", ""))
                core.set_ui_settings(SETTINGS)
                _mark("log")
            elif st == "error":
                _set_pill("fail", "核心异常")
                _push_log("warn", p.get("detail", "核心载入失败"))
                _mark("log")
            elif st == "busy":
                _push_log("warn", p.get("detail", "忙碌中"))
                _mark("log")

        elif kind == "verify":
            st = p.get("state")
            detail = p.get("detail", "")
            if st == "start":
                _set_pill("thinking", "校验中")
                _push_log("sys", detail)
            elif st == "ok":
                _set_pill("ok", "Key 有效")
                _push_log("sys", f"✓ {detail}")
                patch = p.get("settings_patch")
                if patch:
                    _apply_settings_patch(patch)
                    app.run_js(f"applyModelRecommendations({json.dumps(patch)});")
            else:
                _set_pill("fail", "Key 无效")
                _push_log("warn", f"✗ {detail}")
            if st in ("ok", "fail"):
                app.run_js(f"updateImageBtn({json.dumps(_vision_enabled())}, {json.dumps(_vision_reason())});")
            _mark("log")
            _flush()

        elif kind == "turn":
            if p.get("state") == "start":
                SUBTASKS.clear()
                WORKERS.clear()
                _set_pill("thinking", "thinking")
                _set_agent_status("蚁后开始执行任务…")
                app.run_js("setStopEnabled(true);")
                _mark("subtasks", "workers")
            else:
                _set_pill("ok", "就绪")
                _set_agent_status("")
                _auto_save_session()  # 每轮结束自动存会话记录
                if len(CHATS) > MAX_CHATS:
                    # 内存上限：只保留最近 MAX_CHATS 条（渲染只取最近 MAX_CHATS_RENDER 条）
                    del CHATS[: len(CHATS) - MAX_CHATS]
                app.run_js("setStopEnabled(false);")
                _flush()  # 收尾强制刷一次，避免最后一批事件卡在 debounce 里

        elif kind == "models":
            st = p.get("state")
            detail = p.get("detail", "")
            if st == "start":
                _set_pill("thinking", "检测模型")
                _push_log("sys", detail)
                _mark("log")
            elif st == "ok":
                _set_pill("ok", "模型就绪")
                _push_log("sys", f"✓ {detail}")
                models = p.get("list") or []
                patch = p.get("settings_patch")
                if patch:
                    _apply_settings_patch(patch)
                _render_models_list(models)
                app.run_js(f"openModelsModal({json.dumps(models)});")
                if patch:
                    app.run_js(f"applyModelRecommendations({json.dumps(patch)});")
                app.run_js(
                    f"updateImageBtn({json.dumps(_vision_enabled())}, {json.dumps(_vision_reason())});"
                )
                _mark("log")
            else:
                _set_pill("fail", "检测失败")
                _push_log("warn", f"✗ {detail}")
                app.run_js(f"setVerifyHint({json.dumps(detail)}, 'bad');")
                _mark("log")

        elif kind == "skill_list":
            SKILLS_CACHE = p.get("list", [])
            _render_skills_list()
    except Exception as e:
        bridge.trace("UI", "error", f"事件处理异常 kind={kind}: {e}")


core.subscribe(on_core_event)
core.set_ui_settings(SETTINGS)

# 启动即让 UI 配置压过宿主环境变量（AntNest 顶层就读掉配置，必须在 import 前）
_pushed = bridge.push_env(SETTINGS)

# 开场：核心懒加载（首次发送时才 import，避免开窗卡在网络校验上）
CHATS.append({
    "role": "queen",
    "text": "蚁后待命。把任务交给我，我会拆成子任务、派发工蚁隔离执行。\n"
            "首次发送会载入核心并校验模型，需要几秒。",
    "reasoning": "",
})
LOGS.append((time.strftime("%H:%M:%S"), "sys", "UI 已启动，核心将在首次发送任务时载入"))
if _pushed:
    LOGS.append((time.strftime("%H:%M:%S"), "sys",
                 f"已用设置覆盖环境变量：{', '.join(_pushed)}"))
if not (SETTINGS.get("llm_api_key") or "").strip():
    LOGS.append((time.strftime("%H:%M:%S"), "warn",
                 "尚未配置 API Key，请在「⚙ 设置」中填写后点「保存并校验」"))
_LOG_DOM_COUNT = len(LOGS)


# 启动后静默检查更新（仅提示，不自动下载/执行）
threading.Thread(target=check_update, daemon=True).start()

# 启动后校验 API，刷新图片按钮状态（避免未点「保存并校验」时无法粘贴截图）
if (SETTINGS.get("llm_api_key") or "").strip():
    core.verify_async(SETTINGS)


# ------------------------------------------------------------------ 渲染辅助
def _esc(s):

    return ui_render.esc(s)


def render_chat():
    return ui_render.render_chat(CHATS)


def _render_status_card():
    """结构化状态卡：当前任务 / 开始时间 / 工蚁创建与存活。不再流式滚动日志。"""
    import time as _t
    st_text = STATE.get("task", "尚未下达任务")
    start_text = getattr(_render_status_card, "_start", "—")
    state = "待命"
    if _STREAM_BUF.get("active"):
        state = "执行中"
    try:
        app.update("#st-task", _esc(st_text[:120]))
        app.update("#st-start", _esc(start_text))
        app.update("#st-state", _esc(state))
    except Exception:
        pass
    rows = []
    now = _t.time()
    for w in WORKERS:
        cid = w.get("id", "")
        name = w.get("name", "") or cid[:8]
        status = w.get("status", "run")
        alive = status in ("run", "idle")
        created = w.get("created_at", "")
        if not created:
            rows.append(
                f'<div class="status-worker"><span class="sw-name">{_esc(name)}</span>'
                f'<span class="pill {status}">{_esc(status)}</span>'
                f'<span class="sw-meta">存活:{("是" if alive else "否")}</span></div>'
            )
            continue
        rows.append(
            f'<div class="status-worker"><span class="sw-name">{_esc(name)}</span>'
            f'<span class="pill {status}">{_esc(status)}</span>'
            f'<span class="sw-meta">创建 {_esc(created)} · 存活:{("是" if alive else "否")}</span></div>'
        )
    html = "".join(rows) if rows else '<div class="muted" style="font-size:12px">暂无工蚁</div>'
    try:
        app.update("#st-workers", html)
    except Exception:
        pass


def render_log():
    return ui_render.render_log(LOGS)


_STATUS_TEXT = {"ok": "完成", "run": "执行中", "fail": "打回", "idle": "待命"}


# 注意：app.update() 替换的是 innerHTML，所以「容器」和「内容」必须分开，
# 否则每次刷新都会把带 id 的容器再套一层，产生嵌套。
def _clip(s, n=120):
    """长文本截断：超长时截取前 n 字并加省略号（防卡片撑破/膨胀）。"""
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def _subtasks_html(limit=None):
    if not SUBTASKS:
        empty = ui.div(cls="empty")[
            ui.raw("还没有子任务。<br>给蚁后下达任务后，拆分结果会出现在这里。")
        ].render()
        return empty
    shown = SUBTASKS if limit is None else SUBTASKS[:limit]
    total = len(SUBTASKS)
    out = []
    for i, s in enumerate(shown, 1):
        status = s.get("status", "run")
        pill = ui.span(cls=f"pill {status}")[_STATUS_TEXT.get(status, status)]
        out.append(
            ui.div(cls="subtask")[
                ui.div(cls="idx")[str(i)],
                ui.div(cls="body")[
                    ui.div(cls="top")[ui.div(cls="title")[_clip(s.get("title", ""), 60)], pill],
                    ui.div(cls="worker")[f"负责：{_clip(s.get('worker', ''), 40)}"],
                    ui.div(cls="muted")[f"↳ {_clip(s.get('msg', ''), 100)}"],
                ],
            ].render()
        )
    if limit is not None and total > len(shown):
        out.append(ui.div(cls="more-hint")[
            f"… 还有 {total - len(shown)} 个子任务，点「展开」查看全部"
        ].render())
    return "".join(out)


def _workers_html(limit=None):
    if not WORKERS:
        empty = ui.div(cls="empty")[
            ui.raw("蚁巢空置中。<br>蚁后派发 spawn_clone 时，工蚁会在这里出现。")
        ].render()
        return empty
    shown = WORKERS if limit is None else WORKERS[:limit]
    total = len(WORKERS)
    out = []
    for w in shown:
        status = w.get("status", "run")
        pill = ui.span(cls=f"pill {status}")[_STATUS_TEXT.get(status, status)]
        art = w.get("artifacts") or []
        art_html = ""
        if art:
            items = "".join(
                f'<span class="art-item" title="{_esc(a)}">{_esc(a.split("/")[-1][:30])}</span>'
                for a in art[:12]
            )
            art_html = f'<div class="art-list">📦 {items}</div>'
        out.append(
            ui.div(cls="worker-card")[
                ui.div(cls="top")[ui.div(cls="name")[_clip(w.get("name", ""), 50)], pill],
                ui.div(cls="meta")[f"当前：{_clip(w.get('task', ''), 80)}"],
                ui.div(cls="meta")[f"↳ {_clip(w.get('note', ''), 100)}"],
                ui.raw(art_html),
            ].render()
        )
    if limit is not None and total > len(shown):
        out.append(ui.div(cls="more-hint")[
            f"… 还有 {total - len(shown)} 只工蚁，点「展开」查看全部"
        ].render())
    return "".join(out)


def _subtasks():
    return ui.div(id="subtasks")[ui.raw(_subtasks_html())]


def _workers():
    return ui.div(id="workers")[ui.raw(_workers_html())]


def _field(key, label, value, full=False, code=False, browse=None, hint="", input_type="text", list_id=""):
    cls = "field-input" + (" code" if code else "")
    type_attr = f' type="{input_type}"' if input_type != "text" else ""
    list_attr = f' list="{list_id}"' if list_id else ""
    inp = ui.raw(
        f'<input class="{cls}" id="set-{key}" value="{_h.escape(str(value))}"{type_attr}{list_attr}>'
    )
    label_html = f'<div class="field-label">{_h.escape(label)}</div>'
    hint_html = f'<div class="field-hint">{_h.escape(hint)}</div>' if hint else ""
    if browse:
        return ui.div(cls=f"field {'full' if full else ''}")[
            ui.raw(label_html + hint_html),
            ui.div(cls="browse-row")[
                inp,
                ui.raw(f'<button type="button" class="btn ghost" onclick="{browse}">浏览…</button>'),
            ],
        ]
    return ui.div(cls=f"field {'full' if full else ''}")[
        ui.raw(label_html + hint_html),
        inp,
    ]


def _field_area(key, label, value, full=True, code=False, hint=""):
    cls = "field-input" + (" code" if code else "")
    label_html = f'<div class="field-label">{_h.escape(label)}</div>'
    hint_html = f'<div class="field-hint">{_h.escape(hint)}</div>' if hint else ""
    return ui.div(cls=f"field {'full' if full else ''}")[
        ui.raw(label_html + hint_html),
        ui.raw(
            f'<textarea class="{cls}" id="set-{key}">{_h.escape(str(value))}</textarea>'
        ),
    ]


def _toggle(key, label, value, hint=""):
    on = str(value).lower() in ("true", "1", "yes")
    hint_html = f'<div class="field-hint">{_h.escape(hint)}</div>' if hint else ""
    return ui.raw(
        f'<div class="field full">'
        f'<div class="toggle-row">'
        f'<div class="toggle-meta"><div class="field-label">{_h.escape(label)}</div>{hint_html}</div>'
        f'<label class="toggle">'
        f'<input type="checkbox" id="set-{key}-cb" {"checked" if on else ""} onchange="syncToggle(\'{key}\')">'
        f'<span class="toggle-ui"></span></label></div>'
        f'<input type="hidden" id="set-{key}" value="{"true" if on else "false"}">'
        f'</div>'
    )


def _settings_section(title, *children):
    inner = "".join(c.render() if hasattr(c, "render") else str(c) for c in children)
    return ui.raw(
        f'<section class="settings-section">'
        f'<div class="section-head"><span class="dot"></span>{_h.escape(title)}</div>'
        f'{inner}</section>'
    )


_THEME_OPTS = [
    ("dark", "黑色", "linear-gradient(135deg,#0a1120,#4CC9F0)"),
    ("light", "白色", "linear-gradient(135deg,#eef2f7,#1a73e8)"),
    ("custom", "自定义", "conic-gradient(from 180deg,#4CC9F0,#ffffff,#4CC9F0)"),
]


def _theme_buttons():
    parts = []
    for key, label, sw in _THEME_OPTS:
        active = " active" if APPEARANCE["theme"] == key else ""
        parts.append(
            f'<button class="theme-btn{active}" value="{key}" onclick="setTheme(\'{key}\')">'
            f'<span class="sw" style="background:{sw}"></span>{label}</button>'
        )
    return ui.raw('<div class="theme-row">' + "".join(parts) + '</div>')


def _skill_options():
    opts = ['<option value="">不使用 Skill</option>']
    for s in SKILLS_CACHE:
        selected = " selected" if s["name"] == SELECTED_SKILL else ""
        opts.append(f'<option value="{_h.escape(s["name"])}"{selected}>{_h.escape(s["name"])}</option>')
    return "".join(opts)


def _skills_modal():
    return ui.div(
        cls="modal settings-modal skills-drawer",
        id="skills-modal",
        onclick="if(event.target===this) closeSkillsModal()",
    )[
        ui.div(cls="modal-card settings-card", onclick="event.stopPropagation()")[
            ui.div(cls="settings-header")[
                ui.div()[
                    ui.h2()["已安装的 Skills"],
                    ui.raw('<p class="settings-sub">选中 Skill 注入上下文，让蚁后获得专项能力</p>'),
                ],
                ui.raw('<button type="button" class="modal-close" onclick="closeSkillsModal()" title="关闭">×</button>'),
            ],
            ui.div(cls="settings-body skills-list")[ui.raw(_skills_list_html())],
            ui.div(cls="settings-footer")[
                ui.raw('<button type="button" class="btn ghost" onclick="refreshSkills()">刷新</button>'),
                ui.raw('<button type="button" class="btn" onclick="closeSkillsModal()">完成</button>'),
            ],
        ],
    ]


def _models_list_html(models=None):
    models = models or []
    if not models:
        return '<div class="empty">尚未检测。请填写 Base URL 与 API Key 后点「检测模型列表」。</div>'
    parts = []
    current = SETTINGS.get("llm_model", "")
    for m in models:
        mid = m.get("id", "") if isinstance(m, dict) else str(m)
        if not mid:
            continue
        vision = m.get("vision") if isinstance(m, dict) else False
        rec = m.get("rec") if isinstance(m, dict) else {}
        tag = '<span class="skill-tag">vision</span>' if vision else ""
        if (isinstance(rec, dict) and rec.get("thinking_mode")
                and str(rec["thinking_mode"]).lower() not in ("auto", "", "none")):
            tag += f'<span class="skill-tag">thinking:{_h.escape(str(rec["thinking_mode"]))}</span>'
        active = " active" if mid == current else ""
        parts.append(
            f'<button type="button" class="model-item{active}" onclick="pickModel({json.dumps(mid)})">'
            f'<span class="skill-name">{_h.escape(mid)}{tag}</span></button>'
        )
    return '<div class="model-list">' + "".join(parts) + "</div>"


def _models_modal():
    return ui.div(
        cls="modal models-modal",
        id="models-modal",
        onclick="if(event.target===this) closeModelsModal()",
    )[
        ui.raw('<button type="button" class="modal-close global" onclick="closeModelsModal()" title="关闭">×</button>'),
        ui.div(cls="modal-card", onclick="event.stopPropagation()")[
            ui.h2()["可用模型列表"],
            ui.raw('<p class="muted" style="font-size:13px;margin:-8px 0 12px">点击模型名填入设置；带 vision 标签的支持图片</p>'),
            ui.div(id="models-list")[ui.raw(_models_list_html())],
            ui.div(cls="modal-actions")[
                ui.raw('<button class="btn ghost" onclick="onListModels()">重新检测</button>'),
                ui.raw('<button class="btn" onclick="closeModelsModal()">关闭</button>'),
            ],
        ],
    ]


def _render_models_list(models=None):
    app.update("#models-list", _models_list_html(models))
    if not models:
        return
    opts = "".join(
        f'<option value="{_h.escape(m.get("id", "") if isinstance(m, dict) else str(m))}"></option>'
        for m in models
        if (m.get("id") if isinstance(m, dict) else str(m))
    )
    app.run_js(f"var d=document.getElementById('model-list-options'); if(d) d.innerHTML={json.dumps(opts)};")


def _skills_list_html():
    if not SKILLS_CACHE:
        return '<div class="empty">未找到 Skills。请在「设置」中配置正确的 Skills 目录，然后点刷新。</div>'
    parts = []
    for s in SKILLS_CACHE:
        parts.append(
            f'<div class="skill-item">'
            f'<div class="skill-name">{_h.escape(s["name"])}<span class="skill-tag">{s["type"]}</span></div>'
            f'<div class="skill-meta">{_h.escape(s["path"])}</div>'
            f'<div class="skill-desc">{_h.escape(s["description"])}</div>'
            f'</div>'
        )
    return '<div class="skill-list">' + "".join(parts) + '</div>'


def _render_skills_list():
    app.update("#skills-list", _skills_list_html())
    # 同步刷新聊天框 skill 下拉选项（只在内容真正变化时才改 innerHTML，
    # 避免用户正点开下拉框时 async refresh 把它强制关闭造成「闪一下消失」）。
    opts = _skill_options()
    sel = json.dumps(SELECTED_SKILL)
    app.run_js(
        "var s=document.getElementById('skill-select');"
        "if(s){"
        f"  var newOpts={json.dumps(opts)};"
        "  if(s.innerHTML!==newOpts){ s.innerHTML=newOpts; }"
        f"  if(s.value!=={sel}){{ s.value={sel}; }}"
        "}"
    )


def _settings_modal():
    llm = ui.div(cls="settings-grid")[
        _field("llm_base_url", "API Base URL", SETTINGS["llm_base_url"], code=True,
               hint="OpenAI 兼容接口地址（DeepSeek / MiniMax / OpenAI 等）"),
        _field("llm_model", "模型名称", SETTINGS["llm_model"], code=True,
               hint="可点「检测模型列表」从远端拉取；不支持 /models 的服务请手填",
               list_id="model-list-options"),
        ui.raw('<datalist id="model-list-options"></datalist>'),
        _field("llm_api_key", "API Key", SETTINGS["llm_api_key"], full=True, code=True,
               hint="仅保存在本机（独立密钥文件，不进 config.json）", input_type="password"),
        _field("thinking_mode", "Thinking 模式", SETTINGS.get("thinking_mode", "auto"), code=True,
               hint="auto=按提供商自动 / on=强制 / off=关闭（MiniMax 建议 off 或 auto）"),
        _toggle("skip_model_check", "跳过 /models 检测", SETTINGS.get("skip_model_check", "false"),
                hint="MiniMax 等不支持模型列表的 API 请开启"),
    ]
    agent = ui.div(cls="settings-grid")[
        _field("max_depth", "最大递归深度", SETTINGS["max_depth"], code=True,
               hint="工蚁嵌套层数上限，推荐 2"),
        _field("max_clones", "每层并发数", SETTINGS["max_clones"], full=True, code=True,
               hint='JSON 格式，如 {"0":10,"1":5,"2":3}'),
    ]
    integrate = ui.div(cls="settings-grid")[
        _toggle("mcp_enabled", "启用 MCP", SETTINGS["mcp_enabled"],
                hint="启用后加载 mcp.json 中的 MCP 服务器，蚁后可调用 mcp_call / mcp_list_tools"),
        _field("mcp_config", "MCP 配置文件", SETTINGS["mcp_config"], code=True,
               hint="相对路径或绝对路径"),
        _toggle("skills_enabled", "启用 Skills", SETTINGS["skills_enabled"],
                hint="选中 Skill 时将 SKILL.md 正文注入本轮上下文"),
        _field("skills_dir", "Skills 目录", SETTINGS["skills_dir"], full=True, code=True,
               hint="包含 SKILL.md 的文件夹", browse="onBrowseSkillsDir()"),
    ]
    appear = ui.div(cls="settings-grid")[
        ui.div(cls="field full")[
            ui.raw('<div class="field-label">主题</div><div class="field-hint">点击即时预览</div>'),
            _theme_buttons(),
        ui.raw(
            '<div class="field full" id="custom-color-row" style="display:none">'
            '<div class="field-label">自定义主题色</div>'
            '<input type="color" id="set-custom-color" value="#4CC9F0"'
            ' style="width:100%;height:36px;border-radius:var(--radius-sm);border:1px solid var(--card-border);background:var(--surface);"'
            ' oninput="applyCustomColor(this.value)">'            '</div>'
        ),
        ],
        _field("bg_image", "背景图片", APPEARANCE["bg_image"], full=True, code=True,
               hint="URL 或本地路径，留空使用主题渐变"),
    ]
    custom = _field_area(
        "custom_agent", "自定义 Agent 行为偏好", APPEARANCE["custom_agent"], code=True,
        hint="可描述表达风格、主动程度和验证习惯；只影响行为偏好，不改变工具权限或安全确认。",
    )
    return ui.div(cls="modal settings-modal", id="settings-modal", onclick="if(event.target===this) closeSettings()")[
        ui.div(cls="modal-card settings-card", onclick="event.stopPropagation()")[
            ui.div(cls="settings-header")[
                ui.div()[
                    ui.h2()["设置"],
                    ui.raw('<p class="settings-sub">配置模型连接、蚁巢参数、集成、外观与 Agent 行为偏好</p>'),
                ],
                ui.raw('<button type="button" class="modal-close" onclick="closeSettings()" title="关闭">×</button>'),
            ],
            ui.div(cls="settings-body")[
                _settings_section("LLM 连接", llm),
                _settings_section("蚁巢参数", agent),
                _settings_section("集成", integrate),
                _settings_section("外观", appear),
                _settings_section("自定义 Agent", custom),
                ui.raw(f'<input type="hidden" id="set-theme" value="{APPEARANCE["theme"]}">'),
            ],
            ui.div(cls="settings-footer")[
                ui.raw('<span class="verify-hint" id="verify-hint"></span>'),
                ui.raw('<button type="button" class="btn ghost" onclick="openSkillsModal()">管理 Skills</button>'),
                ui.raw('<button type="button" class="btn ghost" onclick="onListModels()">检测模型列表</button>'),
                ui.raw('<button type="button" class="btn ghost" onclick="onTestApi()">测试连接</button>'),
                ui.raw('<button type="button" class="btn ghost" onclick="closeSettings()">取消</button>'),
                ui.raw('<button type="button" class="btn" onclick="onSaveSettings()">保存并校验</button>'),
            ],
        ],
    ]


# ------------------------------------------------------------------ 交互
@app.route("chat_input")
def on_chat_input(data):
    CHAT_DRAFT["v"] = (data or {}).get("value", "")


@app.route("skill_change")
def on_skill_change(data):
    global SELECTED_SKILL
    SELECTED_SKILL = (data or {}).get("value", "")


@app.route("image_attach")
def on_image_attach(data):
    global SELECTED_IMAGE
    b64 = (data or {}).get("b64", "")
    mime = (data or {}).get("mime", "image/png")
    if b64:
        SELECTED_IMAGE = {"b64": b64, "mime": mime}
    else:
        SELECTED_IMAGE = None


@app.route("stop")
def on_stop(data):
    ok, err = core.stop()
    if ok:
        _push_log("warn", "正在强行停止…")
        _mark("log")
    elif err == "idle":
        _push_log("sys", "当前没有进行中的任务")
        _mark("log")



def _workspaces_file():
    """工作空间记录文件：位于 ANT_HOME（记忆/会话同目录），多实例共享。"""
    home = os.environ.get("ANT_HOME") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".antnest")
    try:
        os.makedirs(home, exist_ok=True)
    except Exception:
        pass
    return os.path.join(home, "workspaces.json")


def _load_workspaces():
    """读取工作空间列表 {current: str, list: [{name, path}]}。"""
    try:
        with open(_workspaces_file(), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            lst = [w for w in d.get("list", []) if w and w.get("path")]
            cur = d.get("current") or None
            if not cur and lst:
                cur = lst[0]["path"]
            return {"current": cur, "list": lst}
    except Exception:
        pass
    return {"current": None, "list": []}


def _save_workspaces(d):
    try:
        with open(_workspaces_file(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _ws_name(path):
    """工作空间显示名：目录名；根目录用盘符。"""
    if not path:
        return ""
    p = os.path.basename(path.rstrip("\\/")) or path
    return p


@app.route("ws_list")
def on_ws_list(data):
    """返回工作空间列表到前端渲染。"""
    d = _load_workspaces()
    lst = [{"name": _ws_name(w.get("path")), "path": w.get("path")} for w in d["list"]]
    app.run_js(f"renderWorkspaces({json.dumps({'current': d.get('current'), 'list': lst})});")


@app.route("ws_switch")
def on_ws_switch(data):
    """切换工作空间：改目录 → 若核心已加载则重连 → 清空上下文 → 重置会话。"""
    path = ((data or {}).get("path") or "").strip()
    if not path or not os.path.isdir(path):
        app.run_js("setWsHint('工作空间不存在或无权限访问');")
        return
    if core.busy:
        app.run_js("setWsHint('蚁后正在执行，请等本轮结束再切换');")
        return
    global _SESSION_ID
    d = _load_workspaces()
    d["current"] = path
    _save_workspaces(d)
    try:
        os.chdir(path)
    except Exception as e:
        app.run_js(f"setWsHint({json.dumps('无法切换目录：' + str(e))});")
        return
    # 清空上下文 / 监控
    CHATS.clear()
    CHATS.append({"role": "queen", "text": f"已切换到工作空间「{_ws_name(path)}」。", "reasoning": ""})
    SUBTASKS.clear()
    WORKERS.clear()
    STATE["task"] = "尚未下达任务"
    _SESSION_ID = "default"
    bridge.trace("UI", "info", f"切换工作空间 -> {path}")
    # 若核心已加载（PROJECT_DIR 已定格），重连让新工作区生效
    if core.ready or core.mod:
        def _reload():
            core.ready = False
            core.load_error = ""
            ok, err = core.ensure_loaded(SETTINGS)
            if ok:
                core.apply_settings(SETTINGS)
                core.apply_custom_agent(APPEARANCE.get("custom_agent", ""))
                core.set_ui_settings(SETTINGS)
                _flush()
        threading.Thread(target=_reload, daemon=True).start()
    _mark("chat", "subtasks", "workers", "task")
    app.run_js("scrollChat(); refreshSessions(); wsListRefresh();")
    _flush()


@app.route("ws_add")
def on_ws_add(data):
    """添加工作空间：用文件夹选择器，或由前端传 path。"""
    path = ((data or {}).get("path") or "").strip()
    if not path:
        path = bridge.browse_folder(os.getcwd())
    if not path:
        return
    d = _load_workspaces()
    lst = d["list"]
    if not any(w.get("path") == path for w in lst):
        lst.append({"name": _ws_name(path), "path": path})
    d["list"] = lst
    _save_workspaces(d)
    app.run_js("wsListRefresh();")


@app.route("ws_remove")
def on_ws_remove(data):
    """从列表移除工作空间（不删目录）。"""
    path = ((data or {}).get("path") or "").strip()
    d = _load_workspaces()
    d["list"] = [w for w in d["list"] if w.get("path") != path]
    if d.get("current") == path:
        d["current"] = d["list"][0]["path"] if d["list"] else None
    _save_workspaces(d)
    app.run_js("wsListRefresh();")



@app.route("restore_window")
def on_restore_window(data):
    """还原主窗口（从迷你窗点「还原窗口」）。"""
    app.show()


@app.route("mini_send")
def on_mini_send(data):
    """迷你窗快速发送：同主输入框发送。"""
    v = ((data or {}).get("value") or "").strip()
    if not v:
        return
    ok, err = core.send(v)
    if not ok:
        bridge.trace("UI", "warn", f"mini_send 未发送：{err}")
    # 发送成功后还原窗口，让用户看到蚁后开始思考
    app.show()


@app.route("quit")
def on_quit(data):
    """真正退出应用：释放锁、保存会话、关闭窗口。"""
    _push_log("sys", "正在退出 AntNest…")
    _mark("log")
    # 保存当前会话
    try:
        _auto_save_session()
    except Exception:
        pass
    # 释放单实例锁
    _release_singleton()
    # 允许关闭窗口
    app._minimize_on_close = False
    # 关闭窗口
    try:
        if app._window:
            app._window.destroy()
    except Exception:
        pass


@app.route("send")
def on_send(data):
    global SELECTED_IMAGE
    bridge.trace("UI", "debug", f"收到 send 路由：数据类型={type(data).__name__} 数据={data!r}")
    # pywebview 偶发会把参数序列化成 JSON 字符串，这里做容错
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {}
    data = data or {}
    msg = (data.get("value") or CHAT_DRAFT["v"]).strip()
    image_b64 = data.get("image_b64") or (SELECTED_IMAGE["b64"] if SELECTED_IMAGE else None)
    image_mime = data.get("image_mime") or (SELECTED_IMAGE["mime"] if SELECTED_IMAGE else "image/png")
    skill = data.get("skill") or SELECTED_SKILL
    if not msg and not image_b64:
        bridge.trace("UI", "warn", "send 路由收到空消息，忽略")
        return
    CHAT_DRAFT["v"] = ""
    SELECTED_IMAGE = None
    app.run_js("clearInputAndImage();")
    ok, err = core.send(msg, image_b64=image_b64, image_mime=image_mime, skill=skill or "")
    if not ok:
        bridge.trace("UI", "warn", f"core.send 未发送：{err}")


@app.route("dispatch")
def on_dispatch(data):
    """重连核心：改完设置后不用重启窗口。"""
    if core.busy:
        _push_log("warn", "蚁后正在执行，无法重连")
        _mark("log")
        return

    def _reload():
        core.ready = False
        core.load_error = ""
        _push_log("sys", "正在重连核心…")
        _mark("log")
        ok, err = core.ensure_loaded(SETTINGS)
        if ok:
            core.apply_settings(SETTINGS)
            core.apply_custom_agent(APPEARANCE.get("custom_agent", ""))
            core.set_ui_settings(SETTINGS)
        _flush()

    threading.Thread(target=_reload, daemon=True).start()


@app.route("reset")
def on_reset(data):
    """休眠：清空上下文与监控面板。"""
    if core.busy:
        _push_log("warn", "蚁后正在执行，请等本轮结束再休眠")
        _mark("log")
        return
    core.reset()
    SUBTASKS.clear()
    WORKERS.clear()
    CHATS.clear()
    CHATS.append({
        "role": "queen",
        "text": "上下文已清空，蚁后重新待命。",
        "reasoning": "",
    })
    STATE["task"] = "尚未下达任务"
    _mark("chat", "log", "subtasks", "workers", "task")
    _flush()


def _session_paths():
    """会话目录解析：不依赖核心懒加载，直接按环境/默认推导。
    返回 (project_dir, session_dir)，供列表/查看/删除会话使用。"""
    m = core.mod
    if m:
        return m.PROJECT_DIR, m.SESSION_DIR
    home = os.environ.get("ANT_HOME") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".antnest")
    return os.getcwd(), os.path.join(home, "sessions")


@app.route("sessions_list")
def on_sessions_list(data):
    """对话记录：列出当前项目的所有历史会话（不依赖核心是否已加载）。"""
    import antnest_session as _sm
    try:
        proj, sdir = _session_paths()
        lst = _sm.list_project_sessions(proj, sdir)
        bridge.trace("UI", "info", f"sessions_list 返回 {len(lst)} 个会话")
        app.run_js(f"renderSessions({json.dumps(lst)});")
    except Exception as e:
        bridge.trace("UI", "warn", f"加载会话列表失败：{e}")
        app.run_js(f"setSessionsHint({json.dumps('列表加载失败：' + str(e))});")


def _chats_from_messages(msgs):
    """把 messages 转换成 CHATS 结构（供重渲染）。"""
    chats = []
    for msg in msgs or []:
        if not isinstance(msg, dict) or msg.get("role") not in ("user", "assistant"):
            continue
        chats.append({
            "role": "user" if msg["role"] == "user" else "queen",
            "text": (("⏹ 已保存的中断摘要\n" + (msg.get("content") or ""))
                     if msg.get("stop_snapshot") else (msg.get("content") or "")),
            "reasoning": msg.get("reasoning_content") or "",
        })
    return chats


def _load_session_into_core(sid):
    """读取会话文件并替换 core.messages，成功返回 True。"""
    m = core.mod
    if not m:
        return False
    try:
        msgs = m.load_session(session_id=sid)
        if not msgs:
            return False
        m.messages = msgs
        return True
    except Exception as e:
        bridge.trace("UI", "warn", f"加载会话失败：{e}")
        return False


@app.route("session_load")
def on_session_load(data):
    """加载指定会话并重渲染聊天区。"""
    sid = (data or {}).get("id", "")
    if not sid:
        return
    if core.busy:
        _push_log("warn", "蚁后正在执行，请等本轮结束再切换会话")
        _mark("log")
        return
    if not _load_session_into_core(sid):
        app.run_js(f"setSessionsHint({json.dumps('会话加载失败或不存在')});")
        return
    global _SESSION_ID
    _SESSION_ID = sid
    CHATS.clear()
    CHATS.extend(_chats_from_messages(core.mod.messages))
    if not CHATS:
        CHATS.append({"role": "queen", "text": "已恢复历史会话（无消息）。", "reasoning": ""})
    STATE["task"] = "（已加载历史会话）"
    SUBTASKS.clear()
    WORKERS.clear()
    _mark("chat", "subtasks", "workers", "task")
    app.run_js("closeSessionsModal(); scrollChat();")
    _flush()


@app.route("session_delete")
def on_session_delete(data):
    """删除指定会话（不依赖核心是否已加载——修复之前核心未启动时删除无效）。"""
    sid = (data or {}).get("id", "")
    if not sid:
        return
    import antnest_session as _sm
    try:
        proj, sdir = _session_paths()
        ok = _sm.delete_session(proj, sdir, sid)
        bridge.trace("UI", "info", f"删除会话 {sid}: {'成功' if ok else '失败/不存在'}")
        if not ok:
            app.run_js("setSessionsHint('删除失败或会话不存在');")
    except Exception as e:
        bridge.trace("UI", "warn", f"删除会话失败：{e}")
        app.run_js("setSessionsHint('删除失败：" + str(e) + "');")
    app.run_js("refreshSessions();")


@app.route("session_view")
def on_session_view(data):
    """只读查看会话内容（不替换当前上下文）——对话记录的默认动作是查看。"""
    sid = (data or {}).get("id", "")
    if not sid:
        return
    import antnest_session as _sm
    msgs = []
    try:
        proj, sdir = _session_paths()
        sf = _sm.get_session_file(proj, sdir, sid)
        if os.path.exists(sf):
            with open(sf, encoding="utf-8") as f:
                raw = json.load(f)
            msgs = _chats_from_messages(raw) if isinstance(raw, list) else []
    except Exception as e:
        bridge.trace("UI", "warn", f"读取会话内容失败：{e}")
    app.run_js(f"renderSessionView({json.dumps(msgs)}); openSessionViewModal();")


@app.route("session_new")
def on_session_new(data):
    """新建会话：清空当前上下文并切换到新会话 id。"""
    m = core.mod
    if not m:
        return
    global _SESSION_ID
    _SESSION_ID = time.strftime("%Y%m%d-%H%M%S")
    try:
        from pathlib import Path as _P
        nest = _P(m.NEST_FILE).read_text(encoding="utf-8") if _P(m.NEST_FILE).exists() else ""
        hints = _P(m.HINT_FILE).read_text(encoding="utf-8") if _P(m.HINT_FILE).exists() else ""
        m.messages = [{
            "role": "system",
            "content": m.SYSTEM_PROMPT.format(
                nest_md=nest or "无", hints=hints or "无", env_info=m.ENV_INFO,
                model_capability=m.model_capability_summary(),
            ),
        }]
    except Exception as e:
        bridge.trace("UI", "warn", f"新建会话失败：{e}")
        return
    CHATS.clear()
    CHATS.append({"role": "queen", "text": "已新建会话，蚁后重新待命。", "reasoning": ""})
    STATE["task"] = "尚未下达任务"
    SUBTASKS.clear()
    WORKERS.clear()
    _mark("chat", "subtasks", "workers", "task")
    app.run_js("closeSessionsModal(); scrollChat();")
    _flush()


@app.route("toggle_settings")
def on_toggle_settings(data):
    app.run_js("var m=document.querySelector('#settings-modal'); if(m) m.classList.toggle('show');")


@app.route("close_settings")
def on_close_settings(data):
    app.run_js("var m=document.querySelector('#settings-modal'); if(m) m.classList.remove('show');")


@app.route("browse_skills_dir")
def on_browse_skills_dir(data):
    path = bridge.browse_folder(SETTINGS.get("skills_dir", ""))
    if path:
        SETTINGS["skills_dir"] = path
        app.run_js(f"setSkillsDir({json.dumps(path)});")
        # 同步刷新 skill 列表与下拉框
        _refresh_skills()


@app.route("refresh_skills")
def on_refresh_skills(data):
    _refresh_skills()


def _refresh_skills():
    global SKILLS_CACHE
    SKILLS_CACHE = bridge.list_skills(SETTINGS.get("skills_dir", ""))
    _mark("skills")
    _flush()


@app.route("toggle_skills_modal")
def on_toggle_skills_modal(data):
    app.run_js("var m=document.querySelector('#skills-modal'); if(m) m.classList.toggle('show');")


@app.route("close_skills_modal")
def on_close_skills_modal(data):
    app.run_js("var m=document.querySelector('#skills-modal'); if(m) m.classList.remove('show');")


@app.route("open_release")
def on_open_release(data):
    """在系统默认浏览器打开 Release 页（更新提示条的「查看更新」）。"""
    url = (data or {}).get("url", "")
    if url:
        webbrowser.open(url)


@app.route("check_update")
def on_check_update(data):
    """手动检查更新：重启后台线程，结果通过弹窗告知。"""
    threading.Thread(target=lambda: check_update(silent=False), daemon=True).start()


@app.route("open_sponsor")
def on_open_sponsor(data):
    """读取本地收款二维码目录，把图片 base64 传给前端展示；无匹配则提示。"""
    _PAYMENT_KEYS = ("wx", "wechat", "微信", "zfb", "alipay", "支付宝")
    if not os.path.isdir(SPONSOR_DIR):
        _push_log("warn", f"未找到赞助图片目录：{SPONSOR_DIR}")
        _mark("log")
        _flush()
        return
    images = []
    for fn in sorted(os.listdir(SPONSOR_DIR)):
        ext = os.path.splitext(fn)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            continue
        if not any(k.lower() in fn.lower() for k in _PAYMENT_KEYS):
            continue
        path = os.path.join(SPONSOR_DIR, fn)
        try:
            with open(path, "rb") as f:
                raw = f.read()
            mime = mimetypes.guess_type(path)[0] or "image/png"
            images.append({
                "name": fn,
                "mime": mime,
                "b64": base64.b64encode(raw).decode("utf-8"),
            })
        except Exception as e:
            _push_log("warn", f"读取赞助图片 {fn} 失败：{e}")
    if not images:
        _push_log("warn", f"赞助目录中没有找到微信/支付宝收款二维码：{SPONSOR_DIR}")
        _mark("log")
        _flush()
        return
    app.run_js(f"openSponsorModal({json.dumps(images)});")


@app.route("pick_model")
def on_pick_model(data):
    """从模型列表选中后填入设置，并自动套用该模型的推荐参数。"""
    mid = (data or {}).get("value", "").strip()
    if not mid:
        return
    SETTINGS["llm_model"] = mid
    patched, rec = bridge.apply_recommendations(
        SETTINGS, SETTINGS.get("llm_base_url"), mid
    )
    patch = {k: patched[k] for k in ("thinking_mode", "skip_model_check") if k in patched}
    _apply_settings_patch(patch)
    app.run_js(f"applyModelRecommendations({json.dumps(patch)});")
    hint = rec.get("hint") or f"已选择模型：{mid}"
    app.run_js(f"setVerifyHint({json.dumps(hint)}, 'ok');")
    app.run_js(f"setModelSelect({json.dumps(mid)});")


@app.route("list_models")
def on_list_models(data):
    """设置面板：拉取 /models 并展示可选模型。"""
    s = (data or {}).get("settings") or SETTINGS

    def _done(result):
        app.run_js(
            "setVerifyHint(%s, %s);"
            % (json.dumps(result.get("msg", "")), json.dumps("ok" if result.get("ok") else "bad"))
        )

    core.list_models_async(s, on_done=_done)


@app.route("test_api")
def on_test_api(data):
    """设置面板里的「测试连接」：只校验，不保存、不改环境。"""
    s = (data or {}).get("settings") or {}

    def _done(ok, msg):
        app.run_js(
            "setVerifyHint(%s, %s);" % (json.dumps(msg), json.dumps("ok" if ok else "bad"))
        )

    core.verify_async(s, on_done=_done)


# 动态生成每个设置字段的 oninput 路由
for _key in SETTINGS:
    def _make_setter(k):
        def setter(data):
            SETTINGS[k] = (data or {}).get("value", SETTINGS[k])
        return setter
    app.route(f"set_{_key}")(_make_setter(_key))


@app.route("set_theme")
def on_set_theme(data):
    val = (data or {}).get("value", "dark")
    APPEARANCE["theme"] = val
    app.run_js(f"document.documentElement.setAttribute('data-theme','{val}');")
    app.run_js(
        "document.querySelectorAll('.theme-btn').forEach(function(b){b.classList.remove('active');});"
        "var a=document.querySelector('.theme-btn[value=\"" + val + "\"]'); if(a)a.classList.add('active');"
    )


@app.route("set_bg_image")
def on_set_bg_image(data):
    val = (data or {}).get("value", "")
    APPEARANCE["bg_image"] = val
    app.run_js("applyBg(" + json.dumps(val) + ");")


@app.route("set_custom_agent")
def on_set_custom_agent(data):
    APPEARANCE["custom_agent"] = (data or {}).get("value", "")


@app.route("save_settings")
def on_save_settings(data):
    data = data or {}
    # 保存按钮通过本地 JS 一次性收集所有字段；若个别字段没传，保留当前值
    settings = data.get("settings", {})
    appearance = data.get("appearance", {})
    SETTINGS.update({k: settings[k] for k in SETTINGS if k in settings})
    APPEARANCE.update({k: appearance[k] for k in APPEARANCE if k in appearance})
    # 应用外观
    app.run_js(f"document.documentElement.setAttribute('data-theme','{APPEARANCE['theme']}');")
    app.run_js("applyBg(" + json.dumps(APPEARANCE["bg_image"]) + ");")

    # 持久化：LLM/agent → config.json（嵌套，AntNest 直接消费）；外观 → ui_config.json
    ok, err = bridge.save_settings(SETTINGS, APPEARANCE)
    if ok:
        _push_log("sys", "设置已保存 → config.json + ui_config.json")
    else:
        _push_log("warn", f"配置保存失败：{err}")

    # 让 UI 里填的 Key 真正压过环境变量（AntNest 第 102-104 行 env 优先）
    changed = bridge.push_env(SETTINGS)
    if changed:
        _push_log("sys", f"已用设置覆盖环境变量：{', '.join(changed)}")

    # 热应用到已载入的核心（下一轮生效，不必重启）
    if core.ready:
        core.apply_settings(SETTINGS)
        core.apply_custom_agent(APPEARANCE.get("custom_agent", ""))
    core.set_ui_settings(SETTINGS)
    if core.ready:
        _push_log("sys", "已热应用到运行中的核心（下一轮生效）")
    _mark("log")
    _flush()

    # 保存即校验：真去请求 /models，把结果告诉用户，而不是默默存下
    core.verify_async(SETTINGS)


# ------------------------------------------------------------------ 组装
_init_theme = APPEARANCE["theme"]
_init_bg = APPEARANCE["bg_image"]
app.body(
    # 全局：蚁后立绘（透明底，固定左侧，不参与交互；所有页面可见）
    # 全局：背景图应用函数 + 启动时套用已存主题/背景 + 设置面板本地显隐
    ui.raw("<script>" + load_js() + "</script>"),
    # 启动立绘：应用打开时出现一次（splash），随后自动淡出
    ui.raw(
        '<div id="app-splash" class="app-splash" aria-hidden="false">'
        '<div class="splash-inner">'
        f'<img class="splash-portrait" src="{_QUEEN_PORTRAIT_SRC}" alt=""/>'
        '<div class="splash-glow"></div>'
        '<div class="splash-title">AntNest</div>'
        '<div class="splash-sub">蚁后待命 · 正在加载工作区</div>'
        '</div></div>'
    ),
    ui.raw(
        '<script>'
        '(function(){'
        '  function hide(){'
        '    var s=document.getElementById("app-splash"); if(!s) return;'
        '    s.classList.add("hide");'
        '    setTimeout(function(){ if(s.parentNode) s.parentNode.removeChild(s); }, 800);'
        '  }'
        '  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches){ setTimeout(hide, 600); }'
        '  else { setTimeout(hide, 2200); }'
        '  document.addEventListener("pointerdown", function once(){ hide(); document.removeEventListener("pointerdown", once); });'
        '})();'
        '</script>'
    ),

    ui.raw(
        f'<script>window.QUEEN_PORTRAIT={json.dumps(_QUEEN_PORTRAIT_SRC)};</script>'
    ),
    ui.raw(
        f'<script>var VISION_INITIAL={json.dumps(_vision_enabled())};'
        f'var VISION_REASON={json.dumps(_vision_reason())};'
        f'document.documentElement.setAttribute("data-theme","{_init_theme}");'
        f'applyBg({json.dumps(_init_bg)});'
        f'if(window.updateImageBtn) updateImageBtn(VISION_INITIAL, VISION_REASON);'
        f'window.MODEL_OPTIONS={json.dumps([SETTINGS.get("llm_model", "")])};'
        f'if(window.refreshModelSelect) refreshModelSelect();</script>'
    ),

    # 更新弹窗（居中卡片，默认隐藏；发现新版本时由 check_update 后台弹出）
    ui.raw(
        '<div id="update-banner" class="update-banner">'
        '<div class="ub-backdrop" onclick="hideUpdateBanner()"></div>'
        '<div class="ub-card">'
        '<div class="ub-title">发现新版本</div>'
        '<div class="ub-text"><span class="ub-dot"></span>'
        '<span id="update-text"></span></div>'
        '<div class="ub-actions">'
        '<button class="ub-close" onclick="hideUpdateBanner()">稍后再说</button>'
        '<button onclick="openRelease()">查看更新</button>'
        '</div></div></div>'
    ),

    ui.div(cls="topbar")[
        ui.div(cls="brand")[
            ui.raw(f'<img class="brand-logo" src="{_APP_ICON_SRC}" alt="AntNest"/>'),
            "AntNest",
        ],
        ui.span(cls="tag")["在暗面构建"],
        ui.div(cls="spacer"),
        ui.raw('<button class="btn ghost sponsor" onclick="phwCall(\'open_sponsor\',{})" title="赞助作者喵~~">'
               '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="margin-right:4px">'
               '<path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>'
               '</svg>赞助作者喵~~</button>'),
        ui.raw('<button class="btn ghost" onclick="checkForUpdate()" id="btn-check-update">⬆ 检查更新</button>'),
        ui.raw('<button class="btn ghost" onclick="openSessionsModal()" title="查看/恢复历史对话">🕘 对话记录</button>'),
        ui.raw('<button class="btn ghost" onclick="toggleSettings()">⚙ 设置</button>'),
        # (v1.3.0b) 顶栏「退出」按钮已移除 —— 关闭窗口即退出（on_quit 路由保留兜底）
        ui.span(cls="muted token-usage", id="token-usage", title="本会话 token 用量")[""],
        ui.span(cls="muted")["蚁后"],
        ui.span(cls="pill idle", id="status-pill")["待命"],
    ],

    ui.div(cls="main")[
        # 工作空间栏（最左）
        ui.div(cls="workspace-col")[
            ui.h2()["工作空间"],
            ui.raw('<div class="ws-hint" id="ws-hint">加载中…</div>'),
            ui.raw('<div id=\"ws-list\" class=\"ws-list\"></div>'),
            ui.raw('<button class="btn ghost ws-add" onclick="addWorkspace()">＋ 添加工作空间</button>'),
        ],
        # 左：聊天
        ui.div(cls="chat-col")[
            ui.div(cls="card chat")[
                ui.div(cls="chat-head")[
                    ui.h2()["和蚁后对话"],
                    ui.raw(
                        '<button type="button" class="btn stop" id="btn-stop" disabled '
                        'onclick="phwCall(\'stop\',{})">■ 停止</button>'
                    ),
                ],
                ui.div(cls="chat-msgs", id="chat-msgs")[ui.raw(render_chat())],
                ui.div(cls="agent-status", id="agent-status")[
                    ui.raw('<span class="pulse"></span>'),
                    ui.raw('<span class="status-text" id="agent-status-text"></span>'),
                ],
                ui.div(cls="chat-input-row")[
                    ui.raw(f'<select class="skill-select" id="skill-select" onchange="onSkillChange(this.value)" title="选择要附加的 Skill">{_skill_options()}</select>'),
                    ui.raw(f'<select class="model-select" id="model-select" onchange="onModelChange(this.value)" title="切换当前模型（即时生效，下一轮起用）"><option>{_h.escape(SETTINGS.get("llm_model", ""))}</option></select>'),
                    ui.div(cls="chat-input-wrap")[
                        ui.raw('<div class="image-preview-row" id="image-preview-row"></div>'),
                        ui.raw('<input class="chat-input" id="chat-input" autocomplete="off" '
                               'placeholder="给蚁后下达任务…（Enter 发送，可粘贴图片）" '
                               'oninput="phwCall(\'chat_input\',{value:this.value})" onpaste="onChatPaste(event)">'),
                        ui.raw('<div class="input-actions">'
                               '<button type="button" id="image-upload-btn" title="上传图片" onclick="document.getElementById(\'image-upload\').click()">'
                               '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                               '<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>'
                               '<circle cx="8.5" cy="8.5" r="1.5"/>'
                               '<polyline points="21 15 16 10 5 21"/>'
                               '</svg></button>'
                               '</div>'),
                        ui.raw('<input type="file" id="image-upload" accept="image/*" style="display:none" onchange="onImageFile(this)">'),
                    ],
                    ui.raw('<button type="button" class="btn" onclick="onSend()">发送</button>'),
                ],
            ],
        ],
        # 右：监控
        ui.div(cls="monitor-col")[
            ui.div(cls="card compact")[
                ui.h2()["当前任务"],
                ui.p(cls="muted", id="task-title")[STATE["task"]],
            ],
            ui.div(cls="monitor-panels")[
                ui.div(cls="card panel")[
                    ui.h2()["子任务"],
                    ui.raw('<button class="btn ghost btn-expand" onclick="openSubtasksModal()" title="展开查看全部子任务">展开</button>'),
                    _subtasks(),
                ],
                ui.div(cls="card panel")[
                    ui.h2()["工蚁"],
                    ui.raw('<button class="btn ghost btn-expand" onclick="openWorkersModal()" title="展开查看全部工蚁状态">展开</button>'),
                    _workers(),
                ],
            ],
            ui.div(cls="card log-card status-card")[
                ui.h2()["任务状态"],
                ui.div(cls="status-summary")[
                    ui.raw('<div class="status-row"><span class="status-label">当前任务</span><span class="status-value" id="st-task">—</span></div>'),
                    ui.raw('<div class="status-row"><span class="status-label">开始时间</span><span class="status-value" id="st-start">—</span></div>'),
                    ui.raw('<div class="status-row"><span class="status-label">状态</span><span class="status-value" id="st-state">待命</span></div>'),
                ],
                ui.div(cls="status-workers", id="st-workers")[
                    ui.raw('<div class="muted" style="font-size:12px">暂无工蚁</div>'),
                ],
                ui.div(cls="toolbar")[
                    ui.raw('<button class="btn ghost" onclick="phwCall(\'dispatch\')">↻ 重连核心</button>'),
                    ui.raw('<button class="btn ghost" onclick="phwCall(\'reset\')">■ 休眠</button>'),
                ],
            ],
        ],
    ],

    # 设置模态框
    _settings_modal(),
    # Skills 展示页
    _skills_modal(),
    _models_modal(),
    # 赞助作者二维码弹窗
    ui.raw(
        '<div id="sponsor-modal" class="sponsor-modal" onclick="if(event.target===this) closeSponsorModal()">'
        '<button type="button" class="modal-close global" onclick="closeSponsorModal()" title="关闭">×</button>'
        '<div class="sponsor-card">'
        '<h2>赞助作者喵~~</h2>'
        '<p class="muted" style="font-size:13px; margin:4px 0 0">感谢支持，任选一种方式扫码：</p>'
        '<div id="sponsor-images" class="qr-grid"></div>'
        '</div></div>'
    ),
    # 对话记录弹窗
    ui.raw(
        '<div id="sessions-modal" class="sponsor-modal" onclick="if(event.target===this) closeSessionsModal()">'
        '<button type="button" class="modal-close global" onclick="closeSessionsModal()" title="关闭">×</button>'
        '<div class="modal-card sessions-card">'
        '<div class="sessions-head">'
        '<h2>🕘 对话记录</h2>'
        '<button class="btn" onclick="newSession()">＋ 新建会话</button>'
        '</div>'
        '<p class="muted" id="sessions-hint" style="font-size:12px;margin:0 0 12px"></p>'
        '<div id="sessions-list" class="sessions-grid"><div class="muted">加载中…</div></div>'
        '</div></div>'
    ),
    # 右下角版本徽标
    # ---- 子任务 / 工蚁 展开模态（独立卡片弹窗） ----
    # 子任务展开模态
    ui.raw(
        '<div id="subtasks-modal" class="modal ops-modal" onclick="if(event.target===this) closeSubtasksModal()">'
        '<button type="button" class="modal-close global" onclick="closeSubtasksModal()" title="关闭">x</button>'
        '<div class="modal-card" onclick="event.stopPropagation()">'
        '<h2>子任务总览</h2>'
        '<p class="muted" style="font-size:13px;margin:-8px 0 12px">全部子任务执行状态</p>'
        '<div id="subtasks-modal-list" class="ops-grid"><div class="muted">加载中</div></div>'
        '</div></div>'
    ),
    # 工蚁展开模态
    ui.raw(
        '<div id="workers-modal" class="modal ops-modal" onclick="if(event.target===this) closeWorkersModal()">'
        '<button type="button" class="modal-close global" onclick="closeWorkersModal()" title="关闭">x</button>'
        '<div class="modal-card" onclick="event.stopPropagation()">'
        '<h2>工蚁总览</h2>'
        '<p class="muted" style="font-size:13px;margin:-8px 0 12px">工蚁创建时间与存活状态</p>'
        '<div id="workers-modal-list" class="ops-grid"><div class="muted">加载中</div></div>'
        '</div></div>'
    ),
    # 会话内容只读查看弹窗（对话记录默认动作=查看，不改当前上下文）
    ui.raw(
        '<div id="session-view-modal" class="modal" onclick="if(event.target===this) closeSessionViewModal()">'
        '<button type="button" class="modal-close global" onclick="closeSessionViewModal()" title="关闭">x</button>'
        '<div class="modal-card session-view-card" onclick="event.stopPropagation()">'
        '<h2>会话内容（只读）</h2>'
        '<p class="muted" style="font-size:13px;margin:-8px 0 12px">仅查看，不替换当前对话上下文</p>'
        '<div id="session-view-body" class="session-view-body"><div class="muted">加载中</div></div>'
        '</div></div>'
    ),
    ui.raw(
        '<div class="version-badge" id="app-version" '
        'title="当前版本 v' + APP_VERSION + '，点击检查更新" '
        'onclick="checkForUpdate()">v' + APP_VERSION + '</div>'
    ),
    # 右下角迷你常驻窗（关闭主窗口时出现）
    ui.raw(
        '<div id="mini-panel" class="mini-panel" style="display:none">'
        '<div class="mini-head">'
        '<span class="mini-logo">🐜</span>'
        '<span class="mini-title">AntNest 运行中</span>'
        '<button class="mini-close" onclick="quitApp()" title="退出 AntNest">×</button>'
        '</div>'
        '<div class="mini-state" id="mini-state">待命</div>'
        '<div class="mini-input-row">'
        '<input id="mini-input" placeholder="快速下达任务…" '
               'onkeydown="if(event.key===\'Enter\') miniSend()">'
        '<button class="btn" onclick="miniSend()">发送</button>'
        '</div>'
        '<button class="btn ghost mini-restore" onclick="restoreWindow()">↗ 还原窗口</button>'
        '</div>'
    ),
)

import atexit
# 确保退出时释放单实例锁
atexit.register(_release_singleton)

if __name__ == "__main__":
    # 所有未捕获异常（含导入期、启动前、app.run 运行期）由全局 sys.excepthook
    # 统一弹窗 + 写 .antnest/startup_error.log，不再静默死亡。
    try:
        app.run()
    finally:
        _release_singleton()
