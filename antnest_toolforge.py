# -*- coding: utf-8 -*-
"""AntNest · 工具工坊（Tool Forge）：可复用 Python 工具库。

核心思路（用户需求落地）：
- 蚁后/工蚁每次用 Python 手搓的实用工具不再丢弃，而是「标准化登记」进工具库：
  每个工具 = tool.json（名称/描述/参数 schema/分类）+ tool.py（源码）。
- 工具库持久化在项目记忆空间 `.antnest/tools/`，跨会话复用；
- 记忆占用最小化：登记时不把源码塞进对话，只写盘；需要用时 `get_tool_source`
  按名拉回源码注入本轮，用完即散，不长期占记忆；
- 配合提示词章节，蚁后遇到相似任务优先查库复用，而不是每次重造轮子；
- 工具也可以继续升级为 Skill（由 UI 的 skills 机制另行登记），这里保留目录级复用。

安全约束：
- 工具名只允许 [A-Za-z0-9_-]（防路径穿越）；
- 登记只写 .antnest/tools/ 下，绝不越界；
- 读取源码也锁定库目录内。
"""
from __future__ import annotations

import json
import os
import re
import shutil

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _A():
    import AntNest as _m
    return _m


def tools_dir() -> str:
    """工具库根目录：<项目记忆空间>/tools/。"""
    base = _A().PROJECT_ANT_DIR
    d = os.path.join(base, "tools")
    os.makedirs(d, exist_ok=True)
    return d


def _tool_path(name: str) -> str:
    return os.path.join(tools_dir(), name)


def _tool_dir_path(base_dir: str, name: str) -> str:
    """给定工具库根目录的路径版本。

    与 _tool_path 的区别：**不碰 _A()**，因此不会 import AntNest，也就不会
    走到 antnest_config 的 sys.exit(1)。UI 在核心未加载时也要能读技能表，
    所以采集侧必须走这条路径（见 docs/v1.4.1-SKILL-TABLE-DESIGN.md §0.2）。
    """
    return os.path.join(base_dir, name)


def iter_tool_meta(base_dir: str) -> list:
    """读取工具库全部元数据，**逐项 fail-soft**，且不读 tool.py 源码。

    单一只读的采集入口。list_tools() 与 UI 技能表都建立在它之上：

    - 不读源码：list_tools 为了给模型报「源码行数」会 open(tool.py).read()
      每个工具一次，那是给模型复用的开销，画一张名字表不该付。
    - 损坏的 tool.json 跳过而非抛出：工具目录可能被手工改坏，一条坏记录
      不该让整个技能表打不开。
    - 返回 display_name：可能为空（存量工具），由调用方按回退链处理。
    """
    out = []
    try:
        names = sorted(os.listdir(base_dir))
    except Exception:
        return out
    for n in names:
        p = os.path.join(base_dir, n)
        if not os.path.isdir(p):
            continue
        meta = {}
        try:
            with open(os.path.join(p, "tool.json"), "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                meta = loaded
        except Exception:
            # 保留目录名兜底：至少让用户看见有这么个东西坏了
            meta = {"name": n, "broken": True}
        out.append({
            "name": meta.get("name") or n,
            "display_name": (meta.get("display_name") or "").strip(),
            "description": meta.get("description", ""),
            "category": meta.get("category", "general"),
            "parameters": meta.get("parameters", {}),
            "source_note": meta.get("source_note", ""),
            "updated_at": meta.get("updated_at", ""),
            "version": meta.get("version", 1),
            "broken": bool(meta.get("broken")),
        })
    return out


def register_tool(name: str, description: str = "", parameters: str = "{}",
                  code: str = "", category: str = "general", source_note: str = "",
                  display_name: str = "") -> str:
    """标准化登记一个 Python 工具到工具库（可覆盖更新）。

    parameters 为 JSON 对象字符串（与工具 schema 同构）；code 为工具源码。
    source_note 可记录「由哪次任务/哪个脚本演化而来」，便于溯源。
    返回 JSON：{status, name, path, replaced, message}。
    """
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        return json.dumps({
            "status": "error",
            "error": "工具名只能由字母/数字/中划线/下划线组成且不超过 64 字符",
        }, ensure_ascii=False)
    try:
        params = json.loads(parameters or "{}")
        if not isinstance(params, dict):
            params = {}
    except Exception:
        return json.dumps({
            "status": "error",
            "error": "parameters 必须是合法 JSON 对象",
        }, ensure_ascii=False)
    if not code or not isinstance(code, str):
        return json.dumps({
            "status": "error",
            "error": "code 不能为空（工具源码）",
        }, ensure_ascii=False)

    d = _tool_path(name)
    os.makedirs(d, exist_ok=True)
    existed = os.path.exists(os.path.join(d, "tool.py"))

    # 覆盖更新时**合并**旧 meta，不能整体重建：蚁后改进自己的工具时会再调
    # 一次 register_tool，而它通常不会重复传 display_name。整体覆写会让
    # 「改进了自己的工具」这条故事（也是技能表要让用户看到的）恰好丢掉名字，
    # 界面上退化成一句描述碎片。
    old_meta = {}
    if existed:
        try:
            with open(os.path.join(d, "tool.json"), "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                old_meta = loaded
        except Exception:
            old_meta = {}

    display = (display_name or "").strip() or (old_meta.get("display_name") or "").strip()

    meta = {
        "name": name,
        "version": 2 if existed else 1,
        "description": (description or "").strip(),
        "display_name": display,
        "category": (category or "general").strip() or "general",
        "parameters": params,
        "source_note": (source_note or "").strip(),
        "updated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(d, "tool.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    with open(os.path.join(d, "tool.py"), "w", encoding="utf-8") as f:
        f.write(code if code.endswith("\n") else code + "\n")
    msg = "工具已登记" + ("（覆盖更新）" if existed else "")
    return json.dumps({
        "status": "ok",
        "name": name,
        "display_name": display,
        "path": os.path.join("tools", name).replace("\\", "/"),
        "replaced": bool(existed),
        "message": msg,
    }, ensure_ascii=False)


def list_tools() -> str:
    """列出工具库全部工具（名称/描述/分类/源码行数），供蚁后挑选复用。

    与技能表的区别：这里**要**读 tool.py 数行数（模型复用时需要知道体量），
    而技能表只画名字表，不该付这个开销 —— 所以那条路径走 iter_tool_meta。
    """
    d = tools_dir()
    out = []
    for meta in iter_tool_meta(d):
        code_path = os.path.join(d, meta["name"], "tool.py")
        lines = 0
        try:
            lines = len(open(code_path, encoding="utf-8").read().splitlines())
        except Exception:
            pass
        out.append({
            "name": meta["name"],
            "display_name": meta["display_name"],
            "description": meta["description"],
            "category": meta["category"],
            "lines": lines,
            "version": meta["version"],
            "updated_at": meta["updated_at"],
        })
    return json.dumps({
        "status": "ok",
        "total": len(out),
        "tools": out,
        "path": "tools",
    }, ensure_ascii=False)


def get_tool_source(name: str) -> str:
    """按名取工具源码（供蚁后复制/复用/改进后重新登记）。"""
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        return json.dumps({
            "status": "error",
            "error": "无效的工具名",
        }, ensure_ascii=False)
    p = os.path.join(_tool_path(name), "tool.py")
    if not os.path.isfile(p):
        return json.dumps({
            "status": "error",
            "error": f"工具库中没有 {name!r}（可用 list_tools 查看）",
        }, ensure_ascii=False)
    try:
        code = open(p, encoding="utf-8").read()
        with open(os.path.join(_tool_path(name), "tool.json"), "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": f"读取工具失败：{e}",
        }, ensure_ascii=False)
    return json.dumps({
        "status": "ok",
        "name": name,
        "description": meta.get("description", ""),
        "parameters": meta.get("parameters", {}),
        "category": meta.get("category", "general"),
        "code": code,
    }, ensure_ascii=False)


def build_skill_payload(specs: list, tool_metas: list) -> dict:
    """把「内置 spec」与「自撰工具 meta」汇成技能表 payload。**纯函数**。

    可读名回退链只有两级：
        内置：ToolSpec.display（非空，S1 守着）
        自撰：tool.json 的 display_name → name 原样

    为什么只有两级：``.antnest/tools/`` 当前是空的。为一个不存在的样本群体
    设计三级回退是空想，凑齐三个真实样本再加。回退到 ``name`` 而不是从
    description 截标题，是因为 description 是给模型匹配用的功能性描述，
    截出来会是「将手搓的 Python 工具标准」这种碎片。

    不碰 AntNest / antnest_config，所以本函数可单独测试。
    """
    builtin = []
    worker_only = []
    for s in specs:
        row = {
            "name": s.name,
            "display": (getattr(s, "display", "") or s.name),
            "level": int(s.level),
            "summary": s.summary or "",
        }
        if s.queen_visible:
            builtin.append(row)
        else:
            # 工蚁专用（run_cli / run_python）与压缩态专用（leave_memory_hints）
            # 蚁后常态下拿不到，单独归组而不是混进「蚁后能做的」里
            worker_only.append(row)

    learned = []
    for m in tool_metas:
        learned.append({
            "name": m.get("name", ""),
            "display": (m.get("display_name") or m.get("name") or "（未命名工具）"),
            "description": m.get("description", ""),
            "category": m.get("category", "general"),
            "updated_at": m.get("updated_at", ""),
            "broken": bool(m.get("broken")),
        })

    return {
        "status": "ok",
        "builtin": builtin,
        "worker_only": worker_only,
        "learned": learned,
        "counts": {
            "builtin": len(builtin),
            "worker_only": len(worker_only),
            "learned": len(learned),
        },
    }


def remove_tool(name: str) -> str:
    """删除工具库中的某个工具（谨慎操作，由蚁后显式调用）。"""
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        return json.dumps({"status": "error", "error": "无效的工具名"}, ensure_ascii=False)
    p = _tool_path(name)
    if not os.path.isdir(p):
        return json.dumps({"status": "error", "error": f"工具 {name!r} 不存在"}, ensure_ascii=False)
    shutil.rmtree(p, ignore_errors=True)
    return json.dumps({"status": "ok", "name": name, "message": "工具已删除"}, ensure_ascii=False)