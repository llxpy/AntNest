# -*- coding: utf-8 -*-
"""AntNest · 记忆层：记忆树 RAG 检索注入、压缩线索保留。"""
from __future__ import annotations

import os
from pathlib import Path

import memory_retrieval
import memory_tree


def _A():
    import AntNest as _m
    return _m


def _with_retrieved_memory(msgs):
    """每轮调用前注入动态策略、恢复摘要和相关记忆（记忆树 RAG，失败降级 BM25）。"""
    try:
        extra = _A()._runtime_prompt_context()
        query = ""
        for m in reversed(msgs):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                query = m["content"][:200]
                break
        if query:
            hit = ""
            try:
                tree = memory_tree.load(_A().MEMORY_TREE_FILE)
                tree.sync_from_sources(((_A().NEST_FILE, "NEST"), (_A().HINT_FILE, "hints")))
                hit = tree.retrieve(query, k=_A()._MEMORY_TOP_K)
                tree.save()
            except Exception:
                hit = ""
            if not hit:
                hit = memory_retrieval.retrieve(
                    query, ((_A().NEST_FILE, "NEST"), (_A().HINT_FILE, "hints")), k=_A()._MEMORY_TOP_K
                )
            if hit:
                extra = (extra + "\n\n" if extra else "") + "《记忆检索》供参考：\n" + hit
        return msgs + [{"role": "system", "content": extra}] if extra else msgs
    except Exception:
        return msgs
def _trim_tool_content(msg):
    if msg.get("role") == "tool" and msg.get("content") and len(msg["content"]) > 400:
        c = msg["content"]
        msg = {**msg, "content": c[:200] + "\n…（中间内容已省略）…\n" + c[-200:]}
    return msg


def leave_memory_hints(hints):
    global messages, COMPACT_PANIC

    try:
        nest_text = Path(_A().NEST_FILE).read_text(encoding="utf-8") if Path(_A().NEST_FILE).exists() else ""
    except Exception:
        nest_text = getattr(_A(), "nest_md", "") or ""

    compact_i = -1
    for i in range(len(_A().messages) - 1, -1, -1):
        if _A().messages[i]["role"] == "user" and _A().messages[i]["content"] == _A().COMPACT_PROMPT:
            compact_i = i
            break

    if compact_i < 0:
        return "错误：未找到记忆压缩标记，无法执行 leave_memory_hints。"

    last_user_i = compact_i - 1
    for i in range(last_user_i, -1, -1):
        if _A().messages[i]["role"] == "user":
            last_user_i = i
            break

    kept = []
    for m in _A().messages[last_user_i:compact_i]:
        kept.append(_trim_tool_content(m))

    _A().messages = [
        {
            "role": "system",
            "content": _A().SYSTEM_PROMPT.format(
                nest_md=nest_text or "无", hints=_A().hints or "无", env_info=_A().ENV_INFO,
                model_capability=_A().model_capability_summary(),
            ),
        },
        {
            "role": "user",
            "content": (
                "《系统提示》记忆已压缩。你之前调用 leave_memory_hints 留下了关键线索"
                "（见系统提示中的记忆线索区块），请确认任务状态并继续。\n"
            ),
        },
    ] + kept

    _A().COMPACT_PANIC = False

    os.makedirs(_A().PROJECT_ANT_DIR, exist_ok=True)
    with open(_A().HINT_FILE, "w", encoding="utf-8") as f:
        f.write(_A().hints)
    # 同步进记忆树（episodic 层），供后续 RAG 检索复用
    try:
        _tree = memory_tree.load(_A().MEMORY_TREE_FILE)
        _tree.record_episodic("hints", "记忆压缩线索", _A().hints[:3000])
        _tree.save()
    except Exception:
        pass
    return "已留下记忆线索，对话已压缩。"
