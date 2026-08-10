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
import base64
import html as _h
import json
import mimetypes
import os
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

app = Win(title="AntNest · 在暗面构建", width=1260, height=800,
          gui=os.environ.get("ANT_WEBVIEW_GUI") or None,
          icon=os.path.join(os.path.dirname(os.path.abspath(__file__)), "antnest.ico"))

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

# 收款二维码目录（项目内 Page/，打包后随 app 分发，不依赖本机路径）。
# 点击「赞助作者」读取该目录里 WX/ZFB 二维码并展示；只匹配文件名含
# wx/wechat/微信/zfb/alipay/支付宝 的图片，敏感/无关图片自动过滤。
def _sponsor_dir():
    _base = os.path.dirname(os.path.abspath(__file__))
    for _c in (os.path.join(_base, "Page"), os.path.join(_base, "..", "Page")):
        if os.path.isdir(_c):
            return os.path.abspath(_c)
    return os.path.join(_base, "Page")
SPONSOR_DIR = _sponsor_dir()


def _version_tuple(v):
    _parts = []
    for _p in re.split(r"[.\-+]", str(v or "")):
        _m = re.match(r"\d+", _p)
        _parts.append(int(_m.group()) if _m else 0)
    return tuple(_parts)


def _version_gt(a, b):
    return _version_tuple(a) > _version_tuple(b)


def check_update():
    """后台检查 GitHub Release 是否有新版本；有则弹提示。失败/离线/限流/无新版均静默。"""
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
    except Exception:
        pass

# ------------------------------------------------------------------ 设计 token
app.css("""
:root, [data-theme="dark"]{
  --bg-top:#0a1120; --bg-bottom:#05080f; --accent:#4CC9F0;
  --card:rgba(17,25,40,0.72); --card-border:rgba(255,255,255,0.10);
  --text:#e6f1ff; --muted:#8892a4; --success:#3ddc97; --warn:#ff6b6b;
  --surface:rgba(255,255,255,0.02); --topbar-bg:rgba(10,17,32,0.5);
}
[data-theme="light"]{
  --bg-top:#eef2f7; --bg-bottom:#dbe3ee; --accent:#1a73e8;
  --card:rgba(255,255,255,0.82); --card-border:rgba(15,23,42,0.10);
  --text:#0f172a; --muted:#5b6776; --success:#16a34a; --warn:#dc2626;
  --surface:rgba(15,23,42,0.04); --topbar-bg:rgba(255,255,255,0.55);
}
[data-theme="midnight"]{
  --bg-top:#050505; --bg-bottom:#000000; --accent:#7dd3fc;
  --card:rgba(20,20,20,0.72); --card-border:rgba(255,255,255,0.10);
  --text:#f5f5f5; --muted:#9ca3af; --success:#4ade80; --warn:#f87171;
  --surface:rgba(255,255,255,0.03); --topbar-bg:rgba(0,0,0,0.5);
}
[data-theme="teal"]{
  --bg-top:#04181a; --bg-bottom:#020c0e; --accent:#2dd4bf;
  --card:rgba(13,38,40,0.72); --card-border:rgba(255,255,255,0.10);
  --text:#e6fffb; --muted:#7c9b98; --success:#34d399; --warn:#fb7185;
  --surface:rgba(255,255,255,0.02); --topbar-bg:rgba(4,24,26,0.5);
}
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0; color:var(--text); display:flex; flex-direction:column; height:100vh;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
  background:
    radial-gradient(1100px 760px at 78% -12%, rgba(76,201,240,0.10), transparent 60%),
    linear-gradient(160deg, var(--bg-top), var(--bg-bottom));
}
.topbar{display:flex; align-items:center; gap:12px; padding:14px 24px;
  border-bottom:1px solid var(--card-border); background:var(--topbar-bg); backdrop-filter:blur(8px); flex-shrink:0}
.brand{display:flex; align-items:center; font-size:20px; font-weight:700; letter-spacing:.5px}
.brand-logo{width:22px; height:22px; margin-right:8px; fill:var(--accent);
  filter:drop-shadow(0 0 5px var(--accent)); animation:breathe 2.4s ease-in-out infinite}
@keyframes breathe{0%,100%{opacity:.35}50%{opacity:1}}
.tag{font-size:12px; color:var(--muted); border:1px solid var(--card-border); border-radius:999px; padding:3px 10px}
.spacer{flex:1}
.pill{font-size:11px; padding:4px 11px; border-radius:999px; font-weight:700; white-space:nowrap;
  display:inline-flex; align-items:center; gap:5px; text-transform:uppercase; letter-spacing:.3px}
.pill::before{content:""; display:inline-block; width:6px; height:6px; border-radius:50%; background:currentColor}
.pill.thinking{color:var(--accent); border:1px solid rgba(76,201,240,.45); background:rgba(76,201,240,.10)}
.pill.ok{color:var(--success); border:1px solid rgba(61,220,151,.35); background:rgba(61,220,151,.12)}
.pill.fail{color:var(--warn); border:1px solid rgba(255,107,107,.35); background:rgba(255,107,107,.12)}
.pill.run{color:var(--accent); border:1px solid rgba(76,201,240,.35); background:rgba(76,201,240,.10)}
.pill.idle{color:var(--muted); border:1px solid var(--card-border); background:var(--surface)}
.empty{color:var(--muted); font-size:13px; padding:14px 4px; text-align:center; line-height:1.7}

.main{flex:1; display:grid; grid-template-columns:1fr 1fr; gap:18px; padding:18px 24px; overflow:hidden; min-height:0}
.card{background:var(--card); border:1px solid var(--card-border); border-radius:14px;
  padding:16px 18px; backdrop-filter:blur(10px); margin-bottom:14px}
.card h2{margin:0 0 12px; font-size:15px; font-weight:700; display:flex; align-items:center; gap:10px}
.muted{color:var(--muted); font-size:13px; margin:0}

/* 聊天栏 */
.chat-col{display:flex; flex-direction:column; min-height:0}
.chat{display:flex; flex-direction:column; min-height:0; flex:1}
.chat-msgs{flex:1; overflow:auto; display:flex; flex-direction:column; padding:6px 4px}
.bubble{max-width:82%; padding:10px 14px; border-radius:12px; margin-bottom:10px; line-height:1.55; font-size:14px; white-space:pre-wrap; word-break:break-word}
.bubble.user{align-self:flex-end; background:rgba(76,201,240,0.15); border:1px solid rgba(76,201,240,0.4)}
.bubble.queen{align-self:flex-start; background:rgba(255,255,255,0.04); border:1px solid var(--card-border)}
.chat-input-row{display:flex; gap:8px; margin-top:12px; flex-shrink:0}
.chat-input{flex:1; background:rgba(255,255,255,0.05); border:1px solid var(--card-border); border-radius:10px; padding:10px 42px 10px 12px; color:var(--text); font-size:14px; outline:none}
.chat-input::placeholder{color:var(--muted)}

/* 监控栏 */
.monitor-col{overflow:auto; min-height:0}
.subtask{display:flex; align-items:flex-start; gap:12px; padding:13px; border-radius:12px;
  border:1px solid var(--card-border); background:rgba(255,255,255,0.02); margin-bottom:10px}
.subtask .idx{display:flex; align-items:center; justify-content:center; width:26px; height:26px;
  border-radius:8px; background:rgba(76,201,240,0.10); color:var(--accent); font-size:12px; font-weight:700; flex-shrink:0}
.subtask .body{flex:1}
.subtask .top{display:flex; align-items:center; justify-content:space-between; gap:10px}
.subtask .title{font-weight:600; font-size:14px}
.subtask .body{flex:1; min-width:0}
.subtask .worker{font-size:12px; color:var(--muted); margin-top:4px}
.worker-card{padding:13px; border-radius:12px; border:1px solid var(--card-border); margin-bottom:10px; background:rgba(255,255,255,0.02)}
.worker-card .top{display:flex; align-items:center; gap:10px; margin-bottom:4px}
.worker-card .name{font-weight:600}
.worker-card .meta{font-size:12px; color:var(--muted); margin-top:2px}

/* 结构化日志 */
.log{display:flex; flex-direction:column; gap:8px; max-height:260px; overflow:auto}
.log-line{display:flex; gap:10px; align-items:baseline; padding:8px 10px; border-radius:8px; background:rgba(255,255,255,0.02); border-left:3px solid transparent; font-size:13px}
.log-line:hover{background:rgba(255,255,255,0.04)}
.log-time{font-family:ui-monospace,Menlo,monospace; color:var(--muted); font-size:12px; white-space:nowrap}
.log-tag{font-size:11px; padding:2px 7px; border-radius:6px; font-weight:600; white-space:nowrap}
.log-tag.sys{color:var(--accent); border:1px solid rgba(76,201,240,.4); background:rgba(76,201,240,.08)}
.log-tag.queen{color:#c792ea; border:1px solid rgba(199,146,234,.4); background:rgba(199,146,234,.08)}
.log-tag.worker{color:var(--success); border:1px solid rgba(61,220,151,.4); background:rgba(61,220,151,.08)}
.log-tag.warn{color:var(--warn); border:1px solid rgba(255,107,107,.4); background:rgba(255,107,107,.08)}
.log-body{flex:1; color:var(--text); line-height:1.5}

/* 滚动条：细、半透明、贴合暗色主题 */
::-webkit-scrollbar{width:8px; height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,0.16); border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,0.28)}
::-webkit-scrollbar-corner{background:transparent}
*{scrollbar-width:thin; scrollbar-color:rgba(255,255,255,0.16) transparent}

.toolbar{display:flex; gap:10px; margin:10px 0 12px}
.btn{border:1px solid rgba(76,201,240,0.5); color:var(--accent); background:rgba(76,201,240,0.08);
  border-radius:10px; padding:8px 13px; font-size:13px; cursor:pointer; transition:.15s}
.btn:hover{background:rgba(76,201,240,0.18)}
.btn.ghost{border-color:var(--card-border); color:var(--muted); background:transparent}
.btn.ghost:hover{color:var(--text); border-color:var(--muted)}

/* 设置面板（模态） */
.theme-row{display:flex; gap:10px; flex-wrap:wrap}
.theme-btn{display:flex; align-items:center; gap:8px; cursor:pointer; border:1px solid var(--card-border);
  border-radius:10px; padding:8px 13px; font-size:13px; color:var(--text); background:rgba(255,255,255,0.03);
  transition:.15s}
.theme-btn:hover{border-color:var(--muted); background:rgba(255,255,255,0.07)}
.theme-btn.active{border-color:var(--accent); background:rgba(76,201,240,0.12); box-shadow:0 0 0 1px var(--accent) inset}
.theme-btn .sw{width:15px; height:15px; border-radius:50%; border:1px solid rgba(255,255,255,0.35); box-shadow:0 0 0 1px rgba(0,0,0,0.3) inset}
.field textarea{background:rgba(255,255,255,0.05); border:1px solid var(--card-border); border-radius:8px;
  padding:8px 10px; color:var(--text); font-size:13px; outline:none; resize:vertical; min-height:84px; font-family:inherit; line-height:1.5}
.field textarea:focus{border-color:rgba(76,201,240,.5)}
.modal{display:none; position:fixed; inset:0; background:rgba(0,0,0,0.55); z-index:100;
  align-items:center; justify-content:center; padding:24px}
.modal.show{display:flex}
.modal-card{width:680px; max-width:92vw; max-height:90vh; overflow:auto; background:var(--card);
  border:1px solid var(--card-border); border-radius:16px; padding:22px 24px}
.modal-card h2{margin:0 0 18px; font-size:17px}
.modal-card h3{font-size:14px; margin:18px 0 10px; color:var(--accent)}
.settings-grid{display:grid; grid-template-columns:1fr 1fr; gap:14px}
.field{display:flex; flex-direction:column; gap:6px}
.field.full{grid-column:1/-1}
.field label{font-size:12px; color:var(--muted)}
.field input{background:rgba(255,255,255,0.05); border:1px solid var(--card-border); border-radius:8px;
  padding:8px 10px; color:var(--text); font-size:13px; outline:none}
.field input:focus{border-color:rgba(76,201,240,.5)}
.field input.code, .field textarea.code{font-family:ui-monospace,Menlo,Consolas,monospace}
.modal-card h3{margin:22px 0 10px; padding-top:16px; border-top:1px solid var(--card-border); font-size:14px; color:var(--accent)}
.modal-card h3:first-of-type{margin-top:0; padding-top:0; border-top:none}
.modal-actions{display:flex; align-items:center; justify-content:flex-end; gap:10px; margin-top:22px; padding-top:16px; border-top:1px solid var(--card-border)}
.verify-hint{flex:1; font-size:12px; color:var(--muted); line-height:1.5; text-align:left;
  word-break:break-word; max-height:40px; overflow:auto}
.verify-hint.ok{color:var(--success)}
.verify-hint.bad{color:var(--warn)}

/* 弹窗关闭按钮：全局固定右上角 */
.modal-close{width:28px; height:28px; display:flex; align-items:center; justify-content:center;
  border-radius:8px; border:1px solid transparent; background:transparent; color:var(--muted);
  font-size:20px; line-height:1; cursor:pointer}
.modal-close:hover{background:var(--surface); color:var(--text); border-color:var(--card-border)}
.modal-close.global{position:fixed; top:18px; right:22px; z-index:110;
  background:rgba(17,25,40,0.55); border-color:var(--card-border); backdrop-filter:blur(4px)}

/* 赞助按钮（右上角） */
.sponsor{display:flex; align-items:center; gap:5px; color:var(--accent)}
.sponsor:hover{color:#ff6b9d; border-color:rgba(255,107,157,.5)}

/* 赞助二维码弹窗 */
.sponsor-modal{display:none; position:fixed; inset:0; background:rgba(0,0,0,0.55); z-index:100;
  align-items:center; justify-content:center; padding:24px}
.sponsor-modal.show{display:flex}
.sponsor-card{width:420px; max-width:92vw; background:var(--card); border:1px solid var(--card-border);
  border-radius:16px; padding:22px 24px; text-align:center; position:relative}
.sponsor-card h2{margin:0 0 8px; font-size:17px}
.sponsor-card .qr-grid{display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:16px}
.sponsor-card .qr-item{display:flex; flex-direction:column; align-items:center; gap:6px}
.sponsor-card .qr-item img{width:100%; border-radius:10px; border:1px solid var(--card-border)}
.sponsor-card .qr-item span{font-size:12px; color:var(--muted)}

/* 图片上传按钮里的 SVG 图标 */
#image-upload-btn svg{width:16px; height:16px; pointer-events:none}

/* thinking 提示 */
.thinking-hint{font-size:12px; color:var(--muted); opacity:0; min-height:18px;
  margin:0 4px 6px; transition:opacity .2s; display:flex; align-items:center; gap:8px}
.thinking-hint.show{opacity:.85}
.thinking-hint .dot{width:6px; height:6px; border-radius:50%; background:var(--accent);
  animation:pulse 1s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.3}50%{opacity:1}}

/* 更新提示条 */
.update-banner{position:fixed; top:14px; left:50%; transform:translateX(-50%);
  z-index:50; display:none; align-items:center; gap:12px; max-width:90vw;
  padding:10px 14px; border-radius:12px;
  background:rgba(17,25,40,0.92); border:1px solid var(--accent);
  color:var(--text); font-size:13px; box-shadow:0 6px 24px rgba(0,0,0,.4)}
.update-banner .ub-text{display:flex; align-items:center; gap:8px}
.update-banner .ub-dot{width:7px; height:7px; border-radius:50%; background:var(--accent)}
.update-banner button{background:transparent; border:1px solid var(--card-border);
  color:var(--text); border-radius:8px; padding:5px 10px; font-size:12px; cursor:pointer}
.update-banner button:hover{border-color:var(--accent); color:var(--accent)}
.update-banner .ub-close{color:var(--muted)}

/* 聊天输入区：技能选择 + 输入框 + 图片 */
.chat-input-row{align-items:flex-end}
.skill-select{background:rgba(255,255,255,0.05); border:1px solid var(--card-border);
  border-radius:10px; padding:10px 12px; color:var(--text); font-size:13px; outline:none;
  max-width:140px; cursor:pointer}
.skill-select:focus{border-color:rgba(76,201,240,.5)}
.chat-input-wrap{flex:1; display:flex; flex-direction:column; gap:6px; position:relative}
.image-preview-row{display:flex; gap:8px; flex-wrap:wrap; padding:0 2px}
.image-preview{position:relative; width:64px; height:64px; border-radius:8px;
  overflow:hidden; border:1px solid var(--card-border); background:var(--surface)}
.image-preview img{width:100%; height:100%; object-fit:cover}
.image-preview .rm{position:absolute; top:2px; right:2px; width:18px; height:18px;
  background:rgba(0,0,0,0.6); color:#fff; border:none; border-radius:50%;
  font-size:12px; cursor:pointer; display:flex; align-items:center; justify-content:center}
.input-actions{position:absolute; right:8px; bottom:8px; display:flex; gap:6px}
.input-actions button{background:transparent; border:1px solid var(--card-border); color:var(--muted);
  border-radius:6px; width:26px; height:26px; font-size:14px; cursor:pointer; display:flex;
  align-items:center; justify-content:center; padding:0}
.input-actions button:hover{color:var(--accent); border-color:var(--accent)}
.input-actions button:disabled{color:var(--muted); border-color:var(--card-border); cursor:not-allowed; opacity:.4}
.input-actions button:disabled:hover{color:var(--muted); border-color:var(--card-border)}

/* Skills 目录浏览按钮 */
.browse-row{display:flex; gap:8px; align-items:center}
.browse-row input{flex:1}
.browse-row button{flex-shrink:0}

/* Skills 展示页 */
.skills-modal .skill-list{display:flex; flex-direction:column; gap:10px; max-height:55vh; overflow:auto; padding-right:4px}
.skills-modal .skill-item{padding:12px; border-radius:10px; border:1px solid var(--card-border);
  background:rgba(255,255,255,0.03)}
.skills-modal .skill-name{font-weight:600; font-size:14px; margin-bottom:4px; display:flex; align-items:center; gap:8px}
.skills-modal .skill-tag{font-size:10px; padding:2px 6px; border-radius:4px; border:1px solid var(--card-border); color:var(--muted)}
.skills-modal .skill-meta{font-size:12px; color:var(--muted); margin-bottom:4px}
.skills-modal .skill-desc{font-size:13px; color:var(--text); line-height:1.5}
.skills-modal .empty{text-align:center; padding:30px 10px}
""")

# ------------------------------------------------------------------ 运行时状态
STATE = {"task": "尚未下达任务"}
SUBTASKS = []          # [{id, title, worker, status, msg}]
WORKERS = []           # [{id, name, status, task, note}]
CHATS = []             # [(role, text)]
LOGS = []              # [(time, tag, text)]
CHAT_DRAFT = {"v": ""}
SELECTED_SKILL = ""    # 聊天框左侧选中的 skill
SELECTED_IMAGE = None  # {b64, mime} or None
SKILLS_CACHE = []      # 缓存的 skills 列表
MAX_LOGS = 500

# 配置由 bridge 统一管理：
#   LLM / agent 参数 → config.json（AntNest 的嵌套结构，原样保留）
#   外观 / UI-only   → ui_config.json
SETTINGS, APPEARANCE = bridge.load_settings()


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
            app.update("#chat-msgs", render_chat())
            app.run_js("var b=document.querySelector('#chat-msgs'); if(b)b.scrollTop=b.scrollHeight;")
        if "log" in parts:
            app.update("#log", render_log())
            app.run_js("var l=document.querySelector('#log'); if(l)l.scrollTop=l.scrollHeight;")
        if "subtasks" in parts:
            app.update("#subtasks", _subtasks_html())
        if "workers" in parts:
            app.update("#workers", _workers_html())
        if "task" in parts:
            app.update("#task-title", _esc(STATE["task"]))
        if "skills" in parts:
            _render_skills_list()
    except Exception as e:
        bridge.trace("UI", "error", f"刷新失败：{e}")


def _set_pill(cls, text):
    app.run_js(
        "var p=document.querySelector('#status-pill');"
        f"if(p){{p.className='pill {cls}';p.textContent={json.dumps(text)};}}"
    )


def _push_log(tag, text):
    LOGS.append((time.strftime("%H:%M:%S"), tag, text))
    if len(LOGS) > MAX_LOGS:
        del LOGS[: len(LOGS) - MAX_LOGS]


def _upsert(seq, item, key="id"):
    for i, old in enumerate(seq):
        if old.get(key) == item.get(key):
            seq[i] = {**old, **item}
            return
    seq.append(item)


def _set_thinking(text=""):
    cls = "thinking-hint" + (" show" if text else "")
    app.run_js(
        "var h=document.querySelector('#thinking-hint');"
        f"if(h){{h.className={json.dumps(cls)}; h.querySelector('.txt').textContent={json.dumps(text)};}}"
    )


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
    global SKILLS_CACHE
    try:
        if kind == "chat":
            role = p.get("role") if p.get("role") in ("user", "queen") else "queen"
            CHATS.append((role, p.get("text", "")))
            if role == "user":
                STATE["task"] = p.get("text", "")[:200]
                _mark("task")
            _mark("chat")

        elif kind == "thinking":
            _set_thinking(p.get("text", ""))

        elif kind == "skills":
            SKILLS_CACHE = p.get("list", [])
            _mark("skills")

        elif kind == "log":
            _push_log(p.get("tag", "sys"), p.get("text", ""))
            if core.busy and p.get("tag") == "queen":
                _set_thinking(p.get("text", "")[:90])
            _mark("log")

        elif kind == "worker":
            _upsert(WORKERS, {
                "id": p["id"], "name": p.get("name", ""), "status": p.get("status", "run"),
                "task": p.get("task", ""), "note": p.get("note", ""),
            })
            if p.get("status") == "run":
                _set_thinking(f"工蚁 {p.get('name', '')} 执行中：{p.get('task', '')[:60]}")
            elif p.get("status") in ("ok", "fail"):
                _set_thinking(f"工蚁 {p.get('name', '')} 已归巢")
            _mark("workers")

        elif kind == "subtask":
            _upsert(SUBTASKS, {
                "id": p["id"], "title": p.get("title", ""), "worker": p.get("worker", ""),
                "status": p.get("status", "run"), "msg": p.get("msg", ""),
            })
            if p.get("status") == "run":
                _set_thinking(f"派发子任务：{p.get('title', '')[:70]}")
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
            else:
                _set_pill("fail", "Key 无效")
                _push_log("warn", f"✗ {detail}")
            if st in ("ok", "fail"):
                app.run_js(f"updateImageBtn({json.dumps(_vision_enabled())}, {json.dumps(_vision_reason())});")
            _mark("log")
            _flush()

        elif kind == "turn":
            if p.get("state") == "start":
                _set_pill("thinking", "thinking")
                _set_thinking("蚁后正在思考…")
            else:
                _set_pill("ok", "就绪")
                _set_thinking("")
                _flush()  # 收尾强制刷一次，避免最后一批事件卡在 debounce 里

        elif kind == "skill_list":
            SKILLS_CACHE = p.get("list", [])
            _render_skills_list()
    except Exception as e:
        bridge.trace("UI", "error", f"事件处理异常 kind={kind}: {e}")


core.subscribe(on_core_event)

# 启动即让 UI 配置压过宿主环境变量（AntNest 顶层就读掉配置，必须在 import 前）
_pushed = bridge.push_env(SETTINGS)

# 开场：核心懒加载（首次发送时才 import，避免开窗卡在网络校验上）
CHATS.append((
    "queen",
    "蚁后待命。把任务交给我，我会拆成子任务、派发工蚁隔离执行。\n"
    "首次发送会载入核心并校验模型，需要几秒。",
))
LOGS.append((time.strftime("%H:%M:%S"), "sys", "UI 已启动，核心将在首次发送任务时载入"))
if _pushed:
    LOGS.append((time.strftime("%H:%M:%S"), "sys",
                 f"已用设置覆盖环境变量：{', '.join(_pushed)}"))
if not (SETTINGS.get("llm_api_key") or "").strip():
    LOGS.append((time.strftime("%H:%M:%S"), "warn",
                 "尚未配置 API Key，请在「⚙ 设置」中填写后点「保存并校验」"))


# 启动后静默检查更新（仅提示，不自动下载/执行）
threading.Thread(target=check_update, daemon=True).start()


# ------------------------------------------------------------------ 渲染辅助
def _esc(s):
    return _h.escape(str(s))


def render_chat():
    return "\n".join(
        f'<div class="bubble {role}">{_esc(text)}</div>'
        for role, text in CHATS
    )


def render_log():
    lines = []
    for time, tag, text in LOGS:
        lines.append(
            f'<div class="log-line">'
            f'<div class="log-time">{time}</div>'
            f'<div class="log-tag {tag}">{tag.upper()}</div>'
            f'<div class="log-body">{_esc(text)}</div>'
            f'</div>'
        )
    return "\n".join(lines)


_STATUS_TEXT = {"ok": "完成", "run": "执行中", "fail": "打回", "idle": "待命"}


# 注意：app.update() 替换的是 innerHTML，所以「容器」和「内容」必须分开，
# 否则每次刷新都会把带 id 的容器再套一层，产生嵌套。
def _subtasks_html():
    if not SUBTASKS:
        return ui.div(cls="empty")[
            ui.raw("还没有子任务。<br>给蚁后下达任务后，拆分结果会出现在这里。")
        ].render()
    out = []
    for i, s in enumerate(SUBTASKS, 1):
        status = s.get("status", "run")
        pill = ui.span(cls=f"pill {status}")[_STATUS_TEXT.get(status, status)]
        out.append(
            ui.div(cls="subtask")[
                ui.div(cls="idx")[str(i)],
                ui.div(cls="body")[
                    ui.div(cls="top")[ui.div(cls="title")[s.get("title", "")], pill],
                    ui.div(cls="worker")[f"负责：{s.get('worker', '')}"],
                    ui.div(cls="muted")[f"↳ {s.get('msg', '')}"],
                ],
            ].render()
        )
    return "".join(out)


def _workers_html():
    if not WORKERS:
        return ui.div(cls="empty")[
            ui.raw("蚁巢空置中。<br>蚁后派发 spawn_clone 时，工蚁会在这里出现。")
        ].render()
    out = []
    for w in WORKERS:
        status = w.get("status", "run")
        pill = ui.span(cls=f"pill {status}")[_STATUS_TEXT.get(status, status)]
        out.append(
            ui.div(cls="worker-card")[
                ui.div(cls="top")[ui.div(cls="name")[w.get("name", "")], pill],
                ui.div(cls="meta")[f"当前：{w.get('task', '')}"],
                ui.div(cls="meta")[f"↳ {w.get('note', '')}"],
            ].render()
        )
    return "".join(out)


def _subtasks():
    return ui.div(id="subtasks")[ui.raw(_subtasks_html())]


def _workers():
    return ui.div(id="workers")[ui.raw(_workers_html())]


def _field(key, label, value, full=False, code=False, browse=None):
    cls = "field-input" + (" code" if code else "")
    inner = [ui.input(cls=cls, id=f"set-{key}", value=value, oninput=f"set_{key}")]
    if browse:
        inner.append(
            ui.raw(f'<button type="button" class="btn ghost" onclick="{browse}">浏览…</button>')
        )
        return ui.div(cls=f"field {'full' if full else ''}")[
            ui.label()[label],
            ui.div(cls="browse-row")[inner[0], inner[1]],
        ]
    return ui.div(cls=f"field {'full' if full else ''}")[
        ui.label()[label],
        inner[0],
    ]


def _field_area(key, label, value, full=True, code=False):
    cls = "field-input" + (" code" if code else "")
    return ui.div(cls=f"field {'full' if full else ''}")[
        ui.label()[label],
        ui.el("textarea", cls=cls, id=f"set-{key}", oninput=f"set_{key}")[value],
    ]


_THEME_OPTS = [
    ("dark", "暗黑", "linear-gradient(135deg,#0a1120,#4CC9F0)"),
    ("light", "明亮", "linear-gradient(135deg,#eef2f7,#1a73e8)"),
    ("midnight", "午夜", "linear-gradient(135deg,#050505,#7dd3fc)"),
    ("teal", "青绿", "linear-gradient(135deg,#04181a,#2dd4bf)"),
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
    return ui.div(cls="modal skills-modal", id="skills-modal")[
        ui.div(cls="modal-card")[
            ui.h2()["已安装的 Skills"],
            ui.div(id="skills-list")[ui.raw(_skills_list_html())],
            ui.div(cls="modal-actions")[
                ui.raw('<button class="btn ghost" onclick="refreshSkills()">刷新</button>'),
                ui.raw('<button class="btn" onclick="closeSkillsModal()">关闭</button>'),
            ],
        ]
    ]


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
    return ui.div(cls="modal", id="settings-modal", onclick="if(event.target===this) closeSettings()")[
        ui.raw('<button type="button" class="modal-close global" onclick="closeSettings()" title="关闭设置">×</button>'),
        ui.div(cls="modal-card")[
            ui.h2()["设置"],
            ui.h3()["LLM"],
            ui.div(cls="settings-grid")[
                _field("llm_base_url", "API Base URL", SETTINGS["llm_base_url"], code=True),
                _field("llm_model", "模型", SETTINGS["llm_model"], code=True),
                _field("llm_api_key", "API Key", SETTINGS["llm_api_key"], full=True, code=True),
            ],
            ui.h3()["AntNest 参数"],
            ui.div(cls="settings-grid")[
                _field("max_depth", "最大递归深度", SETTINGS["max_depth"], code=True),
                _field("max_clones", "每层并发数 (JSON)", SETTINGS["max_clones"], full=True, code=True),
            ],
            ui.h3()["集成"],
            ui.div(cls="settings-grid")[
                _field("mcp_enabled", "MCP 启用 (true/false)", SETTINGS["mcp_enabled"]),
                _field("mcp_config", "MCP 配置文件", SETTINGS["mcp_config"], code=True),
                _field("skills_enabled", "Skills 启用 (true/false)", SETTINGS["skills_enabled"]),
                _field("skills_dir", "Skills 目录", SETTINGS["skills_dir"], code=True, browse="onBrowseSkillsDir()"),
            ],
            ui.h3()["外观"],
            ui.div(cls="settings-grid")[
                ui.div(cls="field full")[
                    ui.label()["主题（点击即时预览）"],
                    _theme_buttons(),
                ],
                _field("bg_image", "背景图片 URL / 本地路径（留空=主题渐变）", APPEARANCE["bg_image"], full=True, code=True),
            ],
            ui.h3()["自定义 Agent"],
            _field_area("custom_agent", "工蚁系统提示词 / 配置（自由文本或 JSON）", APPEARANCE["custom_agent"], code=True),
            ui.raw(f'<input type="hidden" id="set-theme" value="{APPEARANCE["theme"]}">'),
            ui.div(cls="modal-actions")[
                ui.raw('<span class="verify-hint" id="verify-hint"></span>'),
                ui.raw('<button class="btn ghost" onclick="openSkillsModal()">管理 Skills</button>'),
                ui.raw('<button class="btn ghost" onclick="onTestApi()">测试连接</button>'),
                ui.raw('<button class="btn ghost" onclick="closeSettings()">取消</button>'),
                ui.raw('<button class="btn" onclick="onSaveSettings()">保存并校验</button>'),
            ],
        ]
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
    # 附加 skill 前缀
    final_msg = msg
    if skill:
        final_msg = f"[Skill: {skill}] {msg}"
    ok, err = core.send(final_msg, image_b64=image_b64, image_mime=image_mime)
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
        ok, err = core.ensure_loaded()
        if ok:
            core.apply_settings(SETTINGS)
            core.apply_custom_agent(APPEARANCE.get("custom_agent", ""))
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
    CHATS.append(("queen", "上下文已清空，蚁后重新待命。"))
    STATE["task"] = "尚未下达任务"
    _mark("chat", "log", "subtasks", "workers", "task")
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
        _push_log("sys", "已热应用到运行中的核心（下一轮生效）")
    _mark("log")
    _flush()

    # 保存即校验：真去请求 /models，把结果告诉用户，而不是默默存下
    core.verify_async(SETTINGS)


# ------------------------------------------------------------------ 组装
_init_theme = APPEARANCE["theme"]
_init_bg = APPEARANCE["bg_image"]
app.body(
    # 全局：背景图应用函数 + 启动时套用已存主题/背景 + 设置面板本地显隐
    ui.raw('''
<script>
function applyBg(i){
  if(i){
    document.body.style.backgroundImage="linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(\\""+i+"\\")";
    document.body.style.backgroundSize="cover";
    document.body.style.backgroundPosition="center";
    document.body.style.backgroundAttachment="fixed";
  }else{
    document.body.style.backgroundImage="";
    document.body.style.backgroundSize="";
    document.body.style.backgroundPosition="";
    document.body.style.backgroundAttachment="";
  }
}
function phwCall(route, payload){
  console.log("[phwCall]", route, payload);
  if(window.PHW && window.PHW.route){ window.PHW.route(route, payload||{}); return; }
  console.warn("[phwCall] window.PHW 未就绪，回退 fetch（webview 模式下会失败）");
  fetch("/api/route",{method:"POST",headers:{"Content-Type":"application/json"},
       body:JSON.stringify({route:route,data:payload||{}})});
}
var selectedSkill="";
var selectedImage=null; // {b64, mime}
function onSkillChange(v){
  selectedSkill=v;
  phwCall("skill_change",{value:v});
}
function setSkillSelect(v){
  selectedSkill=v;
  var s=document.getElementById("skill-select");
  if(s) s.value=v;
}
function onImageFile(input){
  if(!canAttachImage()) return;
  var f=input.files && input.files[0];
  if(!f) return;
  attachImage(f);
  input.value="";
}
function canAttachImage(){
  var b=document.getElementById("image-upload-btn");
  return !!b && !b.disabled;
}
function updateImageBtn(enabled, reason){
  var b=document.getElementById("image-upload-btn");
  if(!b) return;
  b.disabled=!enabled;
  b.title = enabled ? "上传图片" : (reason || "当前模型不支持图片识别");
  if(!enabled && selectedImage){ removeImage(); }
}
function attachImage(file){
  if(!file.type.startsWith("image/")){
    alert("请选择图片文件"); return;
  }
  var reader=new FileReader();
  reader.onload=function(e){
    var data=e.target.result; // data:image/png;base64,....
    var idx=data.indexOf(",");
    var mime=data.slice(5, idx);
    var b64=data.slice(idx+1);
    selectedImage={b64:b64, mime:mime};
    phwCall("image_attach",{b64:b64, mime:mime});
    renderImagePreview();
  };
  reader.readAsDataURL(file);
}
function renderImagePreview(){
  var row=document.getElementById("image-preview-row");
  if(!row) return;
  if(!selectedImage){ row.innerHTML=""; return; }
  row.innerHTML='<div class="image-preview"><img src="data:'+selectedImage.mime+';base64,'+selectedImage.b64+'"><button class="rm" onclick="removeImage()">×</button></div>';
}
function removeImage(){
  selectedImage=null;
  phwCall("image_attach",{b64:"", mime:""});
  renderImagePreview();
}
function clearInputAndImage(){
  var i=document.getElementById("chat-input");
  if(i) i.value="";
  selectedImage=null;
  renderImagePreview();
}
function onChatPaste(e){
  if(!canAttachImage()) return;
  var cd=(e.clipboardData||e.originalEvent.clipboardData);
  if(!cd) return;
  var handled=false;
  if(cd.items && cd.items.length){
    for(var k=0;k<cd.items.length;k++){
      if(cd.items[k].type.indexOf("image")!==-1){
        e.preventDefault();
        attachImage(cd.items[k].getAsFile());
        handled=true; break;
      }
    }
  }
  if(!handled && cd.files && cd.files.length){
    for(var k=0;k<cd.files.length;k++){
      if(cd.files[k].type.indexOf("image")!==-1){
        e.preventDefault();
        attachImage(cd.files[k]);
        handled=true; break;
      }
    }
  }
}
document.addEventListener("paste", function(e){
  if(e.target && e.target.id==="chat-input") onChatPaste(e);
});
var _updateUrl="";
function showUpdateBanner(tag, url, cur){
  _updateUrl=url||"";
  var b=document.getElementById("update-banner");
  if(!b) return;
  var t=document.getElementById("update-text");
  if(t) t.textContent="发现新版本 v"+tag+"（当前 v"+cur+"）";
  b.style.display="flex";
}
function hideUpdateBanner(){
  var b=document.getElementById("update-banner");
  if(b) b.style.display="none";
}
function openRelease(){
  if(_updateUrl) phwCall("open_release",{url:_updateUrl});
}
function onSend(){
  var i=document.getElementById("chat-input");
  if(!i){ console.warn("[onSend] 找不到 #chat-input"); return; }
  var v=(i.value||"").trim();
  console.log("[onSend] value=", v, " skill=", selectedSkill, " image=", !!selectedImage);
  if(!v && !selectedImage){ console.log("[onSend] 空消息，忽略"); return; }
  i.value="";
  var payload={value:v, skill:selectedSkill};
  if(selectedImage && canAttachImage()){ payload.image_b64=selectedImage.b64; payload.image_mime=selectedImage.mime; }
  selectedImage=null;
  renderImagePreview();
  phwCall("send",payload);
}
document.addEventListener("keydown",function(e){
  // 输入法合成中（拼音确认候选）不要拦截 Enter，交给 IME 上屏；否则会发出半截拼音
  if(e.isComposing || e.keyCode === 229) return;
  if(e.key==="Enter" && !e.shiftKey && document.activeElement &&
     document.activeElement.id==="chat-input"){ e.preventDefault(); onSend(); }
});
// 针对「输入法检测不到输入框」：pywebview/WebView2 的 WinForms 宿主有时不把焦点交给
// WebView2 控件，导致 IME 无法在该 input 上激活（inline 拼音都不出现）。这里在 webview 就绪后、
// 以及点击聊天输入区时，强制把焦点塞进输入框，逼 IME 接管。
function focusChat(){
  var i=document.getElementById("chat-input");
  if(i){ try{ i.focus({preventScroll:true}); }catch(_){ i.focus(); } }
}
if(window.pywebview && window.pywebview.api){ focusChat(); }
window.addEventListener("pywebviewready", function(){ focusChat(); });
document.addEventListener("click", function(e){
  var t=e.target;
  // 点击按钮/输入框操作区时不抢焦点，避免 WebView2 上按钮点击被焦点切换吞掉
  if(t && (t.tagName==="BUTTON" || t.closest("button") || t.closest(".input-actions"))) return;
  if(t && (t.id==="chat-input" ||
          (t.closest && t.closest(".chat-input-row")))){ focusChat(); }
});
function toggleSettings(){var m=document.querySelector("#settings-modal"); if(m)m.classList.toggle("show");}
function closeSettings(){var m=document.querySelector("#settings-modal"); if(m)m.classList.remove("show");}
function openSkillsModal(){
  var m=document.querySelector("#skills-modal"); if(m)m.classList.add("show");
  refreshSkills();
}
function closeSkillsModal(){var m=document.querySelector("#skills-modal"); if(m)m.classList.remove("show");}
function toggleSkillsModal(){var m=document.querySelector("#skills-modal"); if(m)m.classList.toggle("show");}
function escHtml(s){
  return (s||"").replace(/[&<>\"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});
}
function openSponsorModal(images){
  var m=document.getElementById("sponsor-modal");
  var g=document.getElementById("sponsor-images");
  if(!m || !g) return;
  g.innerHTML=(images||[]).map(function(x){
    return '<div class="qr-item"><img src="data:'+escHtml(x.mime)+';base64,'+escHtml(x.b64)+'" alt="'+escHtml(x.name)+'">'
         +'<span>'+escHtml(x.name)+'</span></div>';
  }).join("");
  m.classList.add("show");
}
function closeSponsorModal(){var m=document.getElementById("sponsor-modal"); if(m)m.classList.remove("show");}
function refreshSkills(){ phwCall("refresh_skills",{}); }
function setSkillsDir(p){
  var el=document.getElementById("set-skills_dir");
  if(el) el.value=p;
}
function onBrowseSkillsDir(){
  phwCall("browse_skills_dir",{});
}
function setTheme(key){
  document.documentElement.setAttribute("data-theme", key);
  document.querySelectorAll(".theme-btn").forEach(function(b){b.classList.remove("active");});
  var a=document.querySelector('.theme-btn[value="'+key+'"]');
  if(a)a.classList.add("active");
  var h=document.getElementById("set-theme");
  if(h)h.value=key;
  var bg=document.getElementById("set-bg_image");
  applyBg(bg?bg.value:"");
}
// 页面就绪后主动刷新 skills 列表：必须等 PHW 桥接就绪，否则 webview 模式下
// window.PHW 尚未注入，会走失败的 fetch 回退导致刷新静默丢失。
(function whenReady(){
  if(window.PHW && window.PHW.route){ refreshSkills(); return; }
  var tries=0;
  var check=function(){
    if(window.PHW && window.PHW.route){ refreshSkills(); return; }
    if(++tries>=80){ console.warn("[refreshSkills] PHW 超时未就绪"); return; }
    setTimeout(check, 50);
  };
  if(window.addEventListener){ window.addEventListener("pywebviewready", check); }
  setTimeout(check, 50);
})();

function collectSettings(){
  var settings={};
  ["llm_base_url","llm_model","llm_api_key","max_depth","max_clones",
   "mcp_enabled","mcp_config","skills_enabled","skills_dir"].forEach(function(k){
    var el=document.getElementById("set-"+k);
    if(el)settings[k]=el.value;
  });
  return settings;
}
function refreshSkillOptions(list){
  var s=document.getElementById("skill-select");
  if(!s) return;
  var old=s.value;
  s.innerHTML='<option value="">不使用 Skill</option>'+
    (list||[]).map(function(x){return '<option value="'+x.name+'">'+x.name+'</option>';}).join("");
  s.value=old;
}
function setVerifyHint(text, cls){
  var h=document.getElementById("verify-hint");
  if(h){h.textContent=text||""; h.className="verify-hint "+(cls||"");}
}
function onTestApi(){
  setVerifyHint("正在校验…","");
  phwCall("test_api",{settings:collectSettings()});
}
function onSaveSettings(){
  var settings={};
  var appearance={};
  ["llm_base_url","llm_model","llm_api_key","max_depth","max_clones","mcp_enabled","mcp_config","skills_enabled","skills_dir"].forEach(function(k){
    var el=document.getElementById("set-"+k);
    if(el)settings[k]=el.value;
  });
  ["bg_image","custom_agent"].forEach(function(k){
    var el=document.getElementById("set-"+k);
    if(el)appearance[k]=el.value;
  });
  var th=document.getElementById("set-theme");
  if(th)appearance.theme=th.value;
  phwCall("save_settings",{settings:settings,appearance:appearance});
  closeSettings();
}
</script>
'''),
    ui.raw(
        f'<script>var VISION_INITIAL={json.dumps(_vision_enabled())};'
        f'var VISION_REASON={json.dumps(_vision_reason())};'
        f'document.documentElement.setAttribute("data-theme","{_init_theme}");'
        f'applyBg({json.dumps(_init_bg)});'
        f'if(window.updateImageBtn) updateImageBtn(VISION_INITIAL, VISION_REASON);</script>'
    ),

    # 更新提示条（默认隐藏，发现新版本时由 check_update 后台弹出）
    ui.raw(
        '<div id="update-banner" class="update-banner">'
        '<span class="ub-text"><span class="ub-dot"></span>'
        '<span id="update-text"></span></span>'
        '<button onclick="openRelease()">查看更新</button>'
        '<button class="ub-close" onclick="hideUpdateBanner()">忽略</button>'
        '</div>'
    ),

    ui.div(cls="topbar")[
        ui.div(cls="brand")[
            ui.raw('<svg class="brand-logo" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
                   '<circle cx="12" cy="5.5" r="2.6"/>'
                   '<circle cx="12" cy="11" r="2"/>'
                   '<ellipse cx="12" cy="18" rx="3.6" ry="4.4"/>'
                   '<path d="M7.2 9.2l3.2 1.6M16.8 9.2l-3.2 1.6M7 14.5l3.2-1M17 14.5l-3.2-1M7.8 19.5l3-1M16.2 19.5l-3-1" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/>'
                   '</svg>'),
            "AntNest",
        ],
        ui.span(cls="tag")["在暗面构建"],
        ui.div(cls="spacer"),
        ui.raw('<button class="btn ghost sponsor" onclick="phwCall(\'open_sponsor\',{})" title="赞助作者喵~~">'
               '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="margin-right:4px">'
               '<path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>'
               '</svg>赞助作者喵~~</button>'),
        ui.raw('<button class="btn ghost" onclick="toggleSettings()">⚙ 设置</button>'),
        ui.span(cls="muted")["蚁后"],
        ui.span(cls="pill idle", id="status-pill")["待命"],
    ],

    ui.div(cls="main")[
        # 左：聊天
        ui.div(cls="chat-col")[
            ui.div(cls="card chat")[
                ui.h2()["和蚁后对话"],
                ui.div(cls="chat-msgs", id="chat-msgs")[ui.raw(render_chat())],
                ui.div(cls="thinking-hint", id="thinking-hint")[
                    ui.raw('<span class="dot"></span><span class="txt"></span>')
                ],
                ui.div(cls="chat-input-row")[
                    ui.raw(f'<select class="skill-select" id="skill-select" onchange="onSkillChange(this.value)" title="选择要附加的 Skill">{_skill_options()}</select>'),
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
            ui.div(cls="card")[
                ui.h2()["当前任务"],
                ui.p(cls="muted", id="task-title")[STATE["task"]],
            ],
            ui.div(cls="card")[
                ui.h2()["子任务"],
                _subtasks(),
            ],
            ui.div(cls="card")[
                ui.h2()["工蚁"],
                _workers(),
            ],
            ui.div(cls="card")[
                ui.h2()["运行日志"],
                ui.div(cls="toolbar")[
                    ui.raw('<button class="btn ghost" onclick="phwCall(\'dispatch\')">↻ 重连核心</button>'),
                    ui.raw('<button class="btn ghost" onclick="phwCall(\'reset\')">■ 休眠</button>'),
                ],
                ui.div(cls="log", id="log")[ui.raw(render_log())],
            ],
        ],
    ],

    # 设置模态框
    _settings_modal(),
    # Skills 展示页
    _skills_modal(),
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
)

if __name__ == "__main__":
    app.run()
