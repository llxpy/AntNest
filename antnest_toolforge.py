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


def register_tool(name: str, description: str = "", parameters: str = "{}",
                  code: str = "", category: str = "general", source_note: str = "") -> str:
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
    meta = {
        "name": name,
        "version": 2 if existed else 1,
        "description": (description or "").strip(),
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
        "path": os.path.join("tools", name).replace("\\", "/"),
        "replaced": bool(existed),
        "message": msg,
    }, ensure_ascii=False)


def list_tools() -> str:
    """列出工具库全部工具（名称/描述/分类/源码行数），供蚁后挑选复用。"""
    d = tools_dir()
    out = []
    try:
        names = sorted(os.listdir(d))
    except Exception:
        names = []
    for n in names:
        p = os.path.join(d, n)
        if not os.path.isdir(p):
            continue
        meta = {}
        try:
            with open(os.path.join(p, "tool.json"), "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass
        code_path = os.path.join(p, "tool.py")
        lines = 0
        try:
            lines = len(open(code_path, encoding="utf-8").read().splitlines())
        except Exception:
            pass
        out.append({
            "name": meta.get("name", n),
            "description": meta.get("description", ""),
            "category": meta.get("category", "general"),
            "lines": lines,
            "version": meta.get("version", 1),
            "updated_at": meta.get("updated_at", ""),
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