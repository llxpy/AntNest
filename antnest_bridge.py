# -*- coding: utf-8 -*-
"""
antnest_bridge.py —— AntNest 核心 ↔ UI 适配层

【设计原则】
  R1 零侵入   不修改 AntNest.py 一行，全部通过 import + monkey patch 挂钩
  R2 可回溯   每个阶段发带序号的 trace，落盘 .antnest/ui_trace.log，
              出错时可定位到具体 stage 编号
  R3 不阻塞   Agent 循环跑后台线程，UI 线程只收事件
  R4 不死循环 单轮 = messages.append + agent_single_loop()
              （不用 human_loop —— 它 926 行会 read_input() 阻塞，
                until 分支还是死循环）

【阶段编号】错误回溯用
  S0  配置层        读写 config.json / ui_config.json
  S1  导入核心      import AntNest（含网络校验，可能 SystemExit）
  S2  初始化会话    复刻 main() 里的 messages / nest_md / hints
  S3  热应用配置    apply_settings / apply_custom_agent
  S4  挂钩          stdout tap + tool_executors 包装
  S5  单轮执行      run_turn → agent_single_loop
  S6  工蚁生命周期  spawn_clone 包装
  S7  收尾          采集 assistant 回复

【事件协议】on_event(kind, payload)
  status   {state: loading|ready|error|busy, detail}
  turn     {state: start|end}
  chat     {role: user|queen, text, reasoning?}
  stream   {phase: start|reasoning_start|reasoning|reasoning_end|content|end, text?, reasoning?}
  log      {tag: queen|sys|worker|warn, text}
  worker   {id, name, status: run|ok|fail, task, note}
  subtask  {id, title, worker, status: run|ok|fail, msg}
"""

import io
import json
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime

from api_compat import apply_recommendations, recommend_model_settings

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# 可写路径解析（坑2 修复）：安装版由启动器通过环境变量指到 %LOCALAPPDATA%/AntNest，
# 未设置时回退脚本同目录（dev 模式）。保持 AntNest 配置形状不变。
CORE_CFG = os.environ.get("ANT_CONFIG_PATH") or os.path.join(THIS_DIR, "config.json")        # AntNest 的（嵌套结构，别动它的形状）
UI_CFG = os.environ.get("ANT_UI_CONFIG_PATH") or os.path.join(THIS_DIR, "ui_config.json")     # UI 外观 + UI-only 项
TRACE_DIR = os.environ.get("ANT_TRACE_DIR") or os.path.join(THIS_DIR, ".antnest")
TRACE_LOG = os.path.join(TRACE_DIR, "ui_trace.log")

# 默认 Skills 目录：空值时解析为 app 同目录下的 Skills/（安装版随包），
# 这样安装包只需要创建 {app}\Skills，ui_config.json 留空即可。
_DEFAULT_SKILLS_DIR = os.path.join(THIS_DIR, "Skills")


def _resolve_skills_dir(raw):
    raw = (raw or "").strip()
    return raw if raw else _DEFAULT_SKILLS_DIR


def _is_default_skills_dir(path):
    try:
        return os.path.normcase(os.path.normpath(path or "")) == os.path.normcase(os.path.normpath(_DEFAULT_SKILLS_DIR))
    except Exception:
        return False


# ==================================================================== Trace
class _Trace:
    """带序号的阶段日志。出错时按 #序号 + S阶段 回溯。"""

    def __init__(self):
        self._n = 0
        self._lock = threading.Lock()
        self.recent = []

    def session(self, title):
        try:
            os.makedirs(TRACE_DIR, exist_ok=True)
            with open(TRACE_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 30} {title} "
                        f"{datetime.now():%Y-%m-%d %H:%M:%S} {'=' * 10}\n")
        except Exception:
            pass

    def __call__(self, stage, level, msg):
        with self._lock:
            self._n += 1
            n = self._n
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"#{n:04d} {ts} [{stage}] {level.upper():5s} {msg}"
        self.recent.append(line)
        if len(self.recent) > 500:
            del self.recent[:200]
        try:
            os.makedirs(TRACE_DIR, exist_ok=True)
            with open(TRACE_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        return line


trace = _Trace()
trace.session("新会话 PID=%d" % os.getpid())


# ==================================================================== S0 配置层
# AntNest 要的是嵌套结构 {"api":{...},"agent":{...}}；
# UI 用的是扁平表单。两边必须分开存，否则一点保存就把核心配置冲毁 → AntNest sys.exit(1)。

UI_DEFAULTS = {
    "theme": "dark",
    "bg_image": "",
    "custom_agent": "",
    # 下面几项 AntNest 目前不消费，纯 UI 侧留存
    "mcp_enabled": "true",
    "mcp_config": "mcp.json",
    "skills_enabled": "true",
    "skills_dir": "",
}

_APPEARANCE_KEYS = ("theme", "bg_image", "custom_agent")
_UI_ONLY_SETTING_KEYS = ("mcp_enabled", "mcp_config", "skills_enabled", "skills_dir")

# 模型名 vision 能力启发式判断（OpenAI /models 接口通常不返回 capabilities，
# 所以靠关键词兜底；若接口返回了 capabilities.vision 则优先信接口）。
_VISION_KEYWORDS = (
    "vision", "gpt-4o", "gpt-4-turbo", "gpt-4.5", "claude-3",
    "qwen-vl", "gemini-pro-vision", "gemini-1.5", "gemini-2",
    "llava", "internvl", "deepseek-vl", "glm-4v", "yi-vl",
    "minimax", "abab", "vl-", "-vl",
)


def _read_json(path, default):
    if not os.path.exists(path):
        return dict(default)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else dict(default)
    except Exception as e:
        trace("S0", "warn", f"读取 {os.path.basename(path)} 失败：{e}")
        return dict(default)


def parse_max_clones(s):
    """容忍 UI 里手写的 {0:10,1:5,2:3}（非合法 JSON）。"""
    if isinstance(s, dict):
        return {str(k): int(v) for k, v in s.items()}
    txt = str(s or "").strip()
    try:
        d = json.loads(txt)
        if isinstance(d, dict):
            return {str(k): int(v) for k, v in d.items()}
    except Exception:
        pass
    out = {m.group(1): int(m.group(2)) for m in re.finditer(r"(\d+)\s*:\s*(\d+)", txt)}
    return out or {"0": 10, "1": 5, "2": 3}


def fmt_max_clones(d):
    if not isinstance(d, dict) or not d:
        return "{0:10,1:5,2:3}"
    items = sorted(d.items(), key=lambda kv: int(kv[0]))
    return "{" + ",".join(f"{k}:{v}" for k, v in items) + "}"


def _expand_path(p):
    return os.path.expandvars(os.path.expanduser(p or ""))


def list_skills(skills_dir):
    """扫描 skills 目录（Hermes 兼容 SKILL.md + 旧版 .baim）。"""
    root = _expand_path(skills_dir)
    if not root:
        return []
    if not os.path.isdir(root):
        try:
            os.makedirs(root, exist_ok=True)
            trace("S0", "info", f"创建 skills 目录：{root}")
        except Exception as e:
            trace("S0", "warn", f"无法创建 skills 目录 {root}：{e}")
            return []
    try:
        from skills_loader import scan_skills

        out = scan_skills(root)
    except Exception as e:
        trace("S0", "warn", f"scan_skills 失败：{e}")
        out = []
    try:
        entries = sorted(os.listdir(root))
    except Exception as e:
        trace("S0", "warn", f"扫描 skills 目录失败：{e}")
        return out
    seen = {s["name"] for s in out}
    for name in entries:
        if name.startswith("."):
            continue
        full = os.path.join(root, name)
        if not os.path.isfile(full) or not name.lower().endswith(".baim"):
            continue
        bname = os.path.splitext(name)[0]
        if bname in seen:
            continue
        seen.add(bname)
        out.append(
            {
                "name": bname,
                "path": full,
                "type": "baim",
                "source": full,
                "description": "（.baim 技能包）",
            }
        )
    trace("S0", "info", f"扫描到 {len(out)} 个 skills")
    return out


def load_settings():
    """返回 (settings 扁平表单, appearance)。LLM/agent 来自 config.json，外观来自 ui_config.json。"""
    core = _read_json(CORE_CFG, {})
    ui = _read_json(UI_CFG, UI_DEFAULTS)
    api = core.get("api", {}) if isinstance(core.get("api"), dict) else {}
    agent = core.get("agent", {}) if isinstance(core.get("agent"), dict) else {}

    settings = {
        "llm_base_url": api.get("base_url", "https://api.deepseek.com/v1"),
        "llm_model": api.get("model_name", "deepseek-v4-flash"),
        "llm_api_key": api.get("api_key", ""),
        "thinking_mode": str(api.get("thinking_mode", "auto")),
        "skip_model_check": str(api.get("skip_model_check", False)).lower(),
        "max_depth": str(agent.get("max_depth", 2)),
        "max_clones": fmt_max_clones(agent.get("max_clones", {})),
    }
    for k in _UI_ONLY_SETTING_KEYS:
        if k == "skills_dir":
            settings[k] = _resolve_skills_dir(ui.get(k, UI_DEFAULTS[k]))
        else:
            settings[k] = str(ui.get(k, UI_DEFAULTS[k]))

    appearance = {k: ui.get(k, UI_DEFAULTS[k]) for k in _APPEARANCE_KEYS}
    trace("S0", "info", f"配置载入 model={settings['llm_model']} theme={appearance['theme']}")
    return settings, appearance


def save_settings(settings, appearance):
    """写回：LLM/agent → config.json（保持嵌套、保留未知字段）；外观 → ui_config.json。"""
    try:
        core = _read_json(CORE_CFG, {})
        api = core.get("api") if isinstance(core.get("api"), dict) else {}
        agent = core.get("agent") if isinstance(core.get("agent"), dict) else {}

        if "llm_base_url" in settings:
            api["base_url"] = str(settings["llm_base_url"]).strip()
        if "llm_model" in settings:
            api["model_name"] = str(settings["llm_model"]).strip()
        if "llm_api_key" in settings:
            api["api_key"] = str(settings["llm_api_key"]).strip()
        if "thinking_mode" in settings:
            api["thinking_mode"] = str(settings["thinking_mode"]).strip() or "auto"
        if "skip_model_check" in settings:
            api["skip_model_check"] = str(settings["skip_model_check"]).lower() in (
                "true", "1", "yes", "on"
            )
        if "max_depth" in settings:
            try:
                agent["max_depth"] = int(str(settings["max_depth"]).strip() or 2)
            except ValueError:
                trace("S0", "warn", f"max_depth 非法：{settings['max_depth']}，保留原值")
        if "max_clones" in settings:
            agent["max_clones"] = parse_max_clones(settings["max_clones"])
        agent.setdefault("compact_threshold", 0.85)
        agent.setdefault("tool_result_max_len", 8000)

        core["api"] = api
        core["agent"] = agent
        with open(CORE_CFG, "w", encoding="utf-8") as f:
            json.dump(core, f, ensure_ascii=False, indent=2)

        ui = _read_json(UI_CFG, UI_DEFAULTS)
        for k in _APPEARANCE_KEYS:
            if k in appearance:
                ui[k] = appearance[k]
        for k in _UI_ONLY_SETTING_KEYS:
            if k in settings:
                if k == "skills_dir" and _is_default_skills_dir(settings[k]):
                    ui[k] = ""
                else:
                    ui[k] = settings[k]
        with open(UI_CFG, "w", encoding="utf-8") as f:
            json.dump(ui, f, ensure_ascii=False, indent=2)

        trace("S0", "info", "配置已写入 config.json(嵌套) + ui_config.json")
        return True, ""
    except Exception as e:
        trace("S0", "error", f"保存失败：{e}")
        return False, str(e)


# ==================================================================== S0b API 校验
# 独立实现（不复用 AntNest.detect_model_len —— 那个失败会 sys.exit(1) 打死进程）。
# 只用标准库，行为对齐 AntNest 第 113-150 行，但返回值而非退出。

def fetch_models_list(base_url, api_key, timeout=12) -> dict:
    """拉取 /models 列表。返回 {ok, msg, models:[{id,vision}], raw}，不抛异常。"""
    import urllib.request
    import urllib.error

    base = (base_url or "").strip().rstrip("/")
    key = (api_key or "").strip()
    if not base:
        return {"ok": False, "msg": "Base URL 为空", "models": [], "raw": []}
    if not key:
        return {"ok": False, "msg": "API Key 为空", "models": [], "raw": []}

    url = f"{base}/models"
    req = urllib.request.Request(
        url, headers={"User-Agent": "AntNest", "Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, body = resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
    except Exception as e:
        trace("S0b", "warn", f"连接失败 {base}：{e}")
        return {"ok": False, "msg": f"无法连接到 {base}：{e}", "models": [], "raw": []}

    if status == 401:
        return {"ok": False, "msg": "API Key 无效或未授权（HTTP 401）", "models": [], "raw": []}
    if status != 200:
        return {
            "ok": False,
            "msg": f"获取模型列表失败（HTTP {status}）：{body[:120]}",
            "models": [],
            "raw": [],
        }
    try:
        out = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "msg": f"返回了非法 JSON：{body[:120]}", "models": [], "raw": []}

    raw = [d for d in out.get("data", []) if isinstance(d, dict) and d.get("id")]
    models = [
        {
            "id": d["id"],
            "vision": supports_vision(d["id"], raw),
            "rec": recommend_model_settings(base, d["id"]),
        }
        for d in raw
    ]
    msg = f"共检测到 {len(models)} 个模型 @ {base}"
    trace("S0b", "info", msg)
    return {"ok": True, "msg": msg, "models": models, "raw": raw}


def verify_api(base_url, model, api_key, timeout=12):
    """返回 (ok: bool, msg: str, models_data: list)。任何异常都不抛出。"""
    base = (base_url or "").strip().rstrip("/")
    model = (model or "").strip()
    result = fetch_models_list(base, api_key, timeout)
    if not result["ok"]:
        return False, result["msg"], []

    raw = result.get("raw") or []
    ids = [m["id"] for m in result.get("models") or []]
    if model and model not in ids:
        preview = ", ".join(ids[:12]) or "（空）"
        more = f" 等 {len(ids)} 个" if len(ids) > 12 else ""
        return False, f"Key 有效，但未找到模型 '{model}'。可用：{preview}{more}", raw
    trace("S0b", "info", f"校验通过 {model} @ {base}（{len(ids)} 个模型）")
    return True, f"校验通过：{model} @ {base}（{len(ids)} 个模型）", raw


def supports_vision(model_name, models_list=None):
    """判断模型是否支持图片识别。models_list 是 /models 返回的 data 列表。"""
    m = (model_name or "").lower()
    if models_list:
        for d in models_list:
            if isinstance(d, dict) and d.get("id") == model_name:
                caps = d.get("capabilities") or {}
                if isinstance(caps, dict) and caps.get("vision"):
                    return True
                break
    return any(k.lower() in m for k in _VISION_KEYWORDS)


def browse_folder(initial_dir=""):
    """弹出文件夹选择对话框；返回路径或空字符串。tkinter 延迟导入，缺失时优雅降级。"""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        trace("S0", "warn", f"无法导入 tkinter，文件夹选择不可用：{e}")
        return ""
    init = _expand_path(initial_dir) if initial_dir else None
    root = tk.Tk()
    root.withdraw()
    try:
        path = filedialog.askdirectory(initialdir=init) if init else filedialog.askdirectory()
    finally:
        root.destroy()
    return path or ""


def push_env(settings):
    """UI 里填的配置写进 os.environ。

    必须这么做的原因：AntNest 第 102-104 行是
        os.environ.get("ANT_API_KEY") or _config_api.get("api_key")
    环境变量优先级更高。如果宿主机已存在 ANT_API_KEY，
    用户在设置面板里改 Key 会完全不生效（本机就有这个变量，已踩过坑）。
    """
    mapping = {
        "ANT_BASE_URL": (settings.get("llm_base_url") or "").strip().rstrip("/"),
        "ANT_MODEL_NAME": (settings.get("llm_model") or "").strip(),
        "ANT_API_KEY": (settings.get("llm_api_key") or "").strip(),
        "ANT_THINKING_MODE": (settings.get("thinking_mode") or "").strip(),
        "ANT_SKIP_MODEL_CHECK": (settings.get("skip_model_check") or "").strip(),
    }
    changed = []
    for k, v in mapping.items():
        if v and os.environ.get(k) != v:
            os.environ[k] = v
            changed.append(k)
    if changed:
        trace("S0b", "info", f"环境变量已覆盖：{', '.join(changed)}（UI 设置优先）")
    return changed


# ==================================================================== S4 stdout 抓取
_LOG_TAGS = ("queen", "sys", "worker", "warn")  # 必须与 UI 的 .log-tag CSS 类一致

_RE_WORKER_DONE = re.compile(r"^\[工蚁\]\s+(.+?)\s+完成")
_WARN_HINTS = ("错误", "异常", "失败", "警告", "拦截", "超时", "中断", "💥", "！！！", "blocked", "timeout", "Traceback")


def classify_line(line):
    s = line.strip()
    if not s:
        return None
    if any(h in s for h in _WARN_HINTS):
        return "warn"
    if s.startswith("[工蚁]"):
        return "worker"
    if s.startswith("[*] ANTNEST"):
        return "queen"
    if s.startswith("===>") or s.startswith("<===") or s.startswith("[-] You"):
        return "sys"
    return "sys"


class StdoutTap(io.TextIOBase):
    """把 AntNest 的 print 流按行切出来喂给 UI，同时原样转发到真实终端。
    仅在有完整换行时推送——运行日志不做流式切片。"""

    def __init__(self, original, sink):
        self._orig = original
        self._sink = sink
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        try:
            self._orig.write(s)
            self._orig.flush()
        except Exception:
            pass
        with self._lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._emit(line)
        return len(s)

    def _emit(self, line):
        line = re.sub(r"\033\[[0-9;]*m", "", line).rstrip()
        if not line.strip():
            return
        try:
            self._sink(line)
        except Exception as e:
            trace("S4", "error", f"日志回调异常：{e}")

    def flush(self):
        with self._lock:
            if self._buf.strip():
                self._emit(self._buf)
            self._buf = ""
        try:
            self._orig.flush()
        except Exception:
            pass

    def isatty(self):
        return False

    def writable(self):
        return True


# ==================================================================== S6 工蚁结果解析
def summarize_clone_result(result):
    """spawn_clone 返回的是 JSON 字符串，解析出 (status, note)。"""
    try:
        d = json.loads(result)
    except Exception:
        txt = str(result or "").strip().replace("\n", " ")
        return "ok", (txt[:120] or "无返回内容")
    if not isinstance(d, dict):
        return "ok", str(d)[:120]
    st = d.get("status", "")
    if st == "ok":
        out = [x for x in (d.get("stdout") or "").strip().splitlines() if x.strip()]
        return "ok", (out[-1].strip()[:120] if out else "执行完成，无输出")
    if st in ("error", "timeout", "blocked"):
        note = (d.get("error") or d.get("stderr") or "").strip().replace("\n", " ")
        return "fail", (note[:120] or f"状态：{st}")
    return "ok", f"状态：{st}"


# ==================================================================== 核心适配器
class AntNestCore:
    def __init__(self):
        self.mod = None
        self.ready = False
        self.load_error = ""
        self.busy = False
        self._subs = []
        self._wid = 0
        self._base_system = ""
        self._models_list = []   # /models 返回的模型 id 列表，用于精确判断 vision
        self._vision_supported = None  # 该模型是否支持图片：None=未校验(仅关键词), True/False
        self._lock = threading.Lock()
        self._ui_settings = {}

    # ---------------- 事件
    def subscribe(self, fn):
        self._subs.append(fn)

    def emit(self, kind, **payload):
        for fn in list(self._subs):
            try:
                fn(kind, payload)
            except Exception as e:
                trace("EV", "error", f"订阅者异常 kind={kind}: {e}")

    def log(self, tag, text):
        if tag not in _LOG_TAGS:
            tag = "sys"
        self.emit("log", tag=tag, text=text)

    def _next_wid(self):
        with self._lock:
            self._wid += 1
            return self._wid

    # ---------------- S1 导入核心
    def ensure_loaded(self, settings=None):
        if self.ready:
            return True, ""
        settings = dict(settings or self._ui_settings or {})
        self.emit("status", state="loading", detail="正在载入 AntNest 核心…")
        trace("S1", "info", "开始导入 AntNest")

        # 防御：宿主进程若残留 clone 变量，import 会走工蚁分支并 sys.exit(0)
        os.environ.pop("AN_CLONE_MODE", None)
        os.environ.setdefault("AN_DEPTH", "0")
        if THIS_DIR not in sys.path:
            sys.path.insert(0, THIS_DIR)

        # 关键：必须在 import 之前推环境变量。AntNest 在模块顶层就读掉了配置，
        # 导入后再改 os.environ 完全没用。
        if settings:
            push_env(settings)

        cap = io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = cap
        try:
            import importlib
            mod = importlib.import_module("AntNest")
        except SystemExit as e:
            sys.stdout, sys.stderr = old_out, old_err
            msg = cap.getvalue().strip() or f"AntNest 启动检查失败（exit={e.code}）"
            self.load_error = msg
            trace("S1", "error", f"SystemExit：{msg.splitlines()[0] if msg else ''}")
            self.emit("status", state="error", detail=msg)
            return False, msg
        except Exception as e:
            sys.stdout, sys.stderr = old_out, old_err
            msg = f"{e}"
            self.load_error = msg
            trace("S1", "error", f"导入异常：{msg}\n{traceback.format_exc()}")
            self.emit("status", state="error", detail=msg)
            return False, msg
        else:
            sys.stdout, sys.stderr = old_out, old_err

        self.mod = mod
        trace("S1", "info", f"导入成功 model={getattr(mod,'ANT_MODEL_NAME','?')} "
                            f"cap={getattr(mod,'TOKEN_CAP','?')}")

        ok, err = self._init_session(settings or {})
        if not ok:
            return False, err

        self._patch_tools()
        self._ui_settings = dict(settings)
        self._init_mcp(self._ui_settings)
        self.ready = True
        self.emit("status", state="ready",
                  detail=f"{getattr(mod,'ANT_MODEL_NAME','?')} · 深度上限 {getattr(mod,'MAX_DEPTH','?')}")
        self.log("sys", f"蚁后就绪：{getattr(mod,'ANT_MODEL_NAME','?')}"
                        f"（token 上限 {getattr(mod,'TOKEN_CAP',0)//1000}k）")
        return True, ""

    # ---------------- S2 初始化会话（复刻 main() 第 1091-1106 行）
    def _init_session(self, settings):
        m = self.mod
        try:
            os.makedirs(m.ANT_HOME, exist_ok=True)
            os.makedirs(m.PROJECT_ANT_DIR, exist_ok=True)
            os.makedirs(m.CLONE_POOL_DIR, exist_ok=True)

            from pathlib import Path
            m.nest_md = Path(m.NEST_FILE).read_text(encoding="utf-8") if Path(m.NEST_FILE).exists() else ""
            m.hints = Path(m.HINT_FILE).read_text(encoding="utf-8") if Path(m.HINT_FILE).exists() else ""
            m.ALLOW_ALL_CLI = True  # UI 无法做终端确认

            self._base_system = m.SYSTEM_PROMPT.format(
                nest_md=m.nest_md or "无", hints=m.hints or "无", env_info=m.ENV_INFO
            )
            m.messages = [{"role": "system", "content": self._base_system}]
            trace("S2", "info", f"会话初始化完成，system prompt {len(self._base_system)} 字")
            return True, ""
        except Exception as e:
            msg = f"会话初始化失败：{e}"
            trace("S2", "error", f"{msg}\n{traceback.format_exc()}")
            self.emit("status", state="error", detail=msg)
            return False, msg

    # ---------------- S3 热应用
    def apply_settings(self, settings):
        """改完设置不重启也能生效（下一轮起）。"""
        m = self.mod
        self._ui_settings = dict(settings or self._ui_settings or {})
        if not m:
            return
        try:
            if settings.get("llm_base_url"):
                m.ANT_BASE_URL = str(settings["llm_base_url"]).strip().rstrip("/")
            if settings.get("llm_model"):
                m.ANT_MODEL_NAME = str(settings["llm_model"]).strip()
            if settings.get("llm_api_key"):
                key = str(settings["llm_api_key"]).strip()
                m.ANT_API_KEY = key
                m.COMMON_HEADER = {"User-Agent": "AntNest", "Authorization": f"Bearer {key}"}
            if settings.get("max_depth"):
                m.MAX_DEPTH = int(str(settings["max_depth"]).strip())
            if settings.get("thinking_mode"):
                m.THINKING_MODE = str(settings["thinking_mode"]).strip().lower() or "auto"
            if "skip_model_check" in settings:
                m.SKIP_MODEL_CHECK = str(settings["skip_model_check"]).lower() in (
                    "true", "1", "yes", "on"
                )
            trace("S3", "info", f"热应用：{m.ANT_MODEL_NAME} @ {m.ANT_BASE_URL} depth<={m.MAX_DEPTH}")
            self._init_mcp(self._ui_settings)
        except Exception as e:
            trace("S3", "warn", f"热应用部分失败：{e}")

    def apply_custom_agent(self, text):
        """把用户自定义指令追加到 system prompt 尾部（不破坏原 prompt）。"""
        m = self.mod
        if not m or not getattr(m, "messages", None):
            return
        try:
            content = self._base_system
            if (text or "").strip():
                content += "\n\n# 用户自定义指令（最高优先级）\n" + text.strip()
            if m.messages and m.messages[0].get("role") == "system":
                m.messages[0]["content"] = content
                trace("S3", "info", f"自定义 Agent 已注入（{len((text or '').strip())} 字）")
        except Exception as e:
            trace("S3", "warn", f"自定义 Agent 注入失败：{e}")

    # ---------------- S4/S6 挂钩工具
    def _patch_tools(self):
        m = self.mod
        if getattr(m, "_ui_patched", False):
            return
        orig_spawn = m.tool_executors.get("spawn_clone")

        def wrapped_spawn(command="", timeout=300, label="", **kw):
            wid = self._next_wid()
            first = (str(command).strip().splitlines() or [""])[0]
            title = (str(label).strip() or first)[:60] or f"子任务 {wid}"
            name = str(label).strip() or f"工蚁-{wid}"
            trace("S6", "info", f"派发 #{wid} {name} :: {title}")
            self.emit("worker", id=wid, name=name, status="run",
                      task=title, note="隔离目录已建，执行中")
            self.emit("subtask", id=wid, title=title, worker=name,
                      status="run", msg="已派发，等待工蚁返回")
            t0 = time.time()
            try:
                result = orig_spawn(command=command, timeout=timeout, label=label, **kw)
            except Exception as e:
                trace("S6", "error", f"#{wid} 抛异常：{e}")
                self.emit("worker", id=wid, name=name, status="fail", task=title, note=f"异常：{e}")
                self.emit("subtask", id=wid, title=title, worker=name, status="fail", msg=f"异常：{e}")
                raise
            status, note = summarize_clone_result(result)
            cost = round(time.time() - t0, 1)
            trace("S6", "info", f"#{wid} 归巢 status={status} {cost}s")
            self.emit("worker", id=wid, name=name, status=status, task=title,
                      note=f"{note}（{cost}s）")
            self.emit("subtask", id=wid, title=title, worker=name, status=status, msg=note)
            return result

        m.tool_executors["spawn_clone"] = wrapped_spawn

        def _ui_stream(phase, text=""):
            self.emit("stream", phase=phase, text=text or "")

        m.UI_STREAM_CB = _ui_stream
        m._ui_patched = True
        trace("S4", "info", "tool_executors.spawn_clone 已包装；UI 流式回调已注入")

    def set_ui_settings(self, settings):
        self._ui_settings = dict(settings or {})
        if self.mod and self.ready:
            self._init_mcp(self._ui_settings)

    def _init_mcp(self, settings):
        m = self.mod
        if not m:
            return
        enabled = str(settings.get("mcp_enabled", "false")).lower() in ("true", "1", "yes")
        m.MCP_ENABLED = False
        m.MCP_HUB = None
        if not enabled:
            trace("S4", "info", "MCP 未启用")
            return
        try:
            from mcp_client import get_hub

            cfg = str(settings.get("mcp_config", "mcp.json")).strip() or "mcp.json"
            cfg_path = cfg if os.path.isabs(cfg) else os.path.join(THIS_DIR, cfg)
            if not os.path.isfile(cfg_path):
                trace("S4", "info", f"MCP 配置文件不存在：{cfg_path}，已跳过")
                self.log("sys", "MCP 未配置：复制 mcp.json.example 为 mcp.json，或在设置中关闭 MCP")
                return
            hub = get_hub()
            hub.load_config(cfg_path)
            m.MCP_HUB = hub
            m.MCP_ENABLED = True
            trace("S4", "info", f"MCP 已加载 {len(hub.sessions)} 个服务器，{len(hub.tool_index)} 个工具")
            self.log("sys", f"MCP 就绪：{len(hub.sessions)} 服务器 / {len(hub.tool_index)} 工具")
        except Exception as e:
            trace("S4", "warn", f"MCP 加载失败：{e}")
            self.log("warn", f"MCP 加载失败：{e}")

    def list_models_async(self, settings, on_done=None):
        """后台拉取 /models 列表，供设置面板「检测模型列表」使用。"""

        def _run():
            self.emit("models", state="start", detail="正在检测模型列表…")
            result = fetch_models_list(
                settings.get("llm_base_url"),
                settings.get("llm_api_key"),
            )
            if result.get("ok"):
                self._models_list = result.get("raw") or []
                current = settings.get("llm_model", "")
                self._vision_supported = supports_vision(current, self._models_list)
                patched, rec = apply_recommendations(
                    settings, settings.get("llm_base_url"), current
                )
                if rec.get("hint"):
                    result["msg"] = f"{result.get('msg', '')} · {rec['hint']}"
                    result["rec"] = rec
                    result["settings_patch"] = {
                        k: patched[k]
                        for k in ("thinking_mode", "skip_model_check")
                        if k in patched
                    }
            self.emit(
                "models",
                state="ok" if result.get("ok") else "fail",
                detail=result.get("msg", ""),
                list=result.get("models") or [],
                rec=result.get("rec"),
                settings_patch=result.get("settings_patch"),
            )
            if on_done:
                try:
                    on_done(result)
                except Exception as e:
                    trace("S0b", "error", f"模型列表回调异常：{e}")

        threading.Thread(target=_run, daemon=True).start()

    def _inject_skill(self, text, skill_name):
        settings = self._ui_settings or {}
        enabled = str(settings.get("skills_enabled", "true")).lower() in ("true", "1", "yes")
        if not skill_name or not enabled:
            return text
        try:
            from skills_loader import load_skill, format_skill_block

            skill = load_skill(settings.get("skills_dir", ""), skill_name)
            if not skill:
                trace("S5", "warn", f"Skill 未找到：{skill_name}")
                return text
            block = format_skill_block(skill)
            trace("S5", "info", f"已注入 Skill：{skill.get('name')}（{len(block)} 字）")
            return f"{text}\n\n---\n\n{block}"
        except Exception as e:
            trace("S5", "warn", f"Skill 注入失败：{e}")
            return text

    # ---------------- S5 单轮执行
    # ---------------- S5 单轮执行
    def send(self, text, image_b64=None, image_mime="image/png", skill=""):
        text = (text or "").strip()
        if not text and not image_b64:
            return False, "空消息"
        if self.busy:
            self.emit("status", state="busy", detail="蚁后正在思考，请稍候")
            return False, "busy"
        threading.Thread(
            target=self._turn,
            args=(text, image_b64, image_mime, skill or ""),
            daemon=True,
        ).start()
        return True, ""

    def _turn(self, text, image_b64=None, image_mime="image/png", skill=""):
        self.busy = True
        self.emit("turn", state="start")
        if self.mod:
            self.mod.agent_reset_cancel()
        display_text = text
        if image_b64:
            display_text += "\n[附带图片]"
        self.emit("chat", role="user", text=display_text)
        trace("S5", "info", f"turn 开始：{text[:60]} image={bool(image_b64)}")
        try:
            ok, err = self.ensure_loaded()
            if not ok:
                head = (err or "未知错误").strip().splitlines()[0]
                self.emit("chat", role="queen", text=f"核心未就绪，无法执行。\n{err}")
                self.log("warn", f"[S1] {head}")
                return

            m = self.mod
            start = len(m.messages)

            # 图片：先检测当前模型是否支持 vision
            if image_b64:
                model = getattr(m, "ANT_MODEL_NAME", "")
                if not supports_vision(model, self._models_list):
                    self.emit("chat", role="queen",
                              text=f"当前模型 `{model}` 不支持图片识别，已忽略图片。"
                                   f"请换用 vision 模型（如 gpt-4o、claude-3、qwen-vl 等）后再试。")
                    self.log("warn", f"模型 {model} 不支持图片，图片已忽略")
                    image_b64 = None

            if image_b64:
                # 使用 OpenAI vision 格式；核心只是原样序列化 messages，不会破坏数组 content
                content = []
                user_text = self._inject_skill(text, skill) if text else ""
                if user_text:
                    content.append({"type": "text", "text": m.clean_input(user_text)})
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{image_mime};base64,{image_b64}"}
                })
                m.messages.append({"role": "user", "content": content})
            else:
                final_text = self._inject_skill(text, skill)
                m.messages.append({"role": "user", "content": m.clean_input(final_text)})
            self.log("queen", f"收到任务：{text[:80]}")
            self.emit("stream", phase="start", text="")

            old_out = sys.stdout
            sys.stdout = StdoutTap(old_out, lambda ln: self.log(classify_line(ln) or "sys", ln))
            try:
                m.agent_single_loop()
            finally:
                try:
                    sys.stdout.flush()
                except Exception:
                    pass
                sys.stdout = old_out

            cancelled = bool(getattr(m, "AGENT_CANCEL", False))

            # S7 收尾：取本轮最后一条 assistant 消息
            last_asst = None
            for x in reversed(m.messages[start:]):
                if x.get("role") == "assistant":
                    last_asst = x
                    break
            reply_text = (last_asst.get("content") or "").strip() if last_asst else ""
            reply_reason = (last_asst.get("reasoning_content") or "").strip() if last_asst else ""
            self.emit(
                "stream",
                phase="end",
                text=reply_text,
                reasoning=reply_reason,
            )
            if cancelled:
                self.emit("chat", role="queen", text="⏹ 操作已被强行停止。")
                trace("S7", "warn", "用户强行停止")
            elif reply_text:
                self.emit("chat", role="queen", text=reply_text, reasoning=reply_reason)
                trace("S7", "info", f"回复 {len(reply_text)} 字，本轮新增 {len(m.messages)-start} 条消息")
            else:
                self.emit("chat", role="queen", text="（本轮没有文本回复，详见运行日志）")
                trace("S7", "warn", "本轮无 assistant 文本")
        except Exception as e:
            trace("S5", "error", f"turn 异常：{e}\n{traceback.format_exc()}")
            self.log("warn", f"[S5] 执行异常：{e}")
            self.emit("chat", role="queen", text=f"执行出错：{e}\n（详见 .antnest/ui_trace.log）")
        finally:
            if self.mod:
                self.mod.agent_reset_cancel()
            self.busy = False
            self.emit("turn", state="end")
            trace("S5", "info", "turn 结束")

    def stop(self):
        """强行停止当前蚁后操作（中断 LLM 流与工蚁）。"""
        if not self.busy:
            return False, "idle"
        if self.mod:
            self.mod.agent_cancel()
        self.log("warn", "用户强行停止")
        return True, "stopping"

    # ---------------- 其它
    def verify_async(self, settings, on_done=None):
        """后台校验 API 配置，结果通过事件 + 回调返回。不阻塞 UI。"""
        def _run():
            self.emit("verify", state="start", detail="正在校验 API 配置…")
            ok, msg, ids = verify_api(
                settings.get("llm_base_url"),
                settings.get("llm_model"),
                settings.get("llm_api_key"),
            )
            self._models_list = ids or []
            self._vision_supported = supports_vision(settings.get("llm_model", ""), ids)
            rec = None
            settings_patch = None
            if ok:
                _, rec = apply_recommendations(
                    settings,
                    settings.get("llm_base_url"),
                    settings.get("llm_model"),
                )
                if rec.get("hint"):
                    msg = f"{msg} · {rec['hint']}"
                settings_patch = {
                    "thinking_mode": rec.get("thinking_mode"),
                    "skip_model_check": rec.get("skip_model_check"),
                }
            self.emit(
                "verify",
                state="ok" if ok else "fail",
                detail=msg,
                rec=rec,
                settings_patch=settings_patch,
            )
            if on_done:
                try:
                    on_done(ok, msg)
                except Exception as e:
                    trace("S0b", "error", f"校验回调异常：{e}")

        threading.Thread(target=_run, daemon=True).start()

    def effective_config(self):
        """当前真正生效的配置（环境变量可能盖过 config.json，这里是实话）。"""
        if self.mod:
            return {
                "base_url": getattr(self.mod, "ANT_BASE_URL", ""),
                "model": getattr(self.mod, "ANT_MODEL_NAME", ""),
                "key_tail": (getattr(self.mod, "ANT_API_KEY", "") or "")[-6:],
            }
        return {
            "base_url": os.environ.get("ANT_BASE_URL", ""),
            "model": os.environ.get("ANT_MODEL_NAME", ""),
            "key_tail": (os.environ.get("ANT_API_KEY", "") or "")[-6:],
        }

    def reset(self):
        """休眠：清空对话，回到只有 system prompt 的状态。"""
        if self.mod and getattr(self.mod, "messages", None):
            self.mod.messages = [{"role": "system", "content": self.mod.messages[0]["content"]}]
            trace("S2", "info", "会话已重置")
            self.log("warn", "蚁后休眠，上下文已清空")
            return True
        self.log("warn", "核心尚未载入，无需重置")
        return False

    def status_text(self):
        if self.busy:
            return "thinking"
        if self.ready:
            return "ready"
        if self.load_error:
            return "error"
        return "idle"
