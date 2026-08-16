# -*- coding: utf-8 -*-
"""零依赖 BM25 记忆检索（AntNest 记忆层）。

对 NEST.md / hints.md 按段落建索引，检索时取 top-k 注入上下文。
中文按「双字滑动窗口」切分（无需 jieba），英文按单词切分。
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path

_CACHE: dict = {"mtime": None, "docs": [], "df": {}, "N": 1, "avgdl": 0}


def tokenize(text: str) -> list:
    """英文按单词、中文按双字滑动窗口切分。"""
    tokens = []
    for m in re.finditer(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text or ""):
        tok = m.group(0)
        tokens.append(tok.lower() if tok[0].isascii() else tok)
    cjk = "".join(m.group(0) for m in re.finditer(r"[\u4e00-\u9fff]", text or ""))
    if len(cjk) >= 2:
        tokens.extend(cjk[i:i + 2] for i in range(len(cjk) - 1))
    return tokens


def _build_index(files):
    docs = []
    for path, tag in files:
        try:
            txt = Path(path).read_text(encoding="utf-8")
        except Exception:
            continue
        for para in re.split(r"\n\s*\n+", txt):
            para = para.strip()
            if len(para) < 10:
                continue
            toks = tokenize(para)
            docs.append({"tag": tag, "para": para, "tokens": toks, "dl": len(toks)})
    n = max(len(docs), 1)
    df = {}
    for d in docs:
        for t in set(d["tokens"]):
            df[t] = df.get(t, 0) + 1
    return {
        "docs": docs, "df": df, "N": n,
        "avgdl": sum(d["dl"] for d in docs) / n,
    }


def get_index(files) -> dict:
    """带 mtime 缓存的索引获取。files: [(path, tag), ...]"""
    key = []
    for p, _tag in files:
        try:
            key.append((p, Path(p).stat().st_mtime_ns))
        except Exception:
            key.append((p, -1))
    if _CACHE["mtime"] == key:
        return _CACHE
    idx = _build_index(files)
    _CACHE.clear()
    _CACHE.update({"mtime": key, **idx})
    return _CACHE


def retrieve(query: str, files, k: int = 5) -> str:
    """BM25 检索，返回 top-k 段落文本（带来源标签）。无命中返回空串。"""
    try:
        idx = get_index(files)
        q_toks = tokenize(query or "")
        if not q_toks or not idx["docs"]:
            return ""
        k1, b = 1.5, 0.75
        qf = {}
        for t in q_toks:
            qf[t] = qf.get(t, 0) + 1
        scored = []
        for d in idx["docs"]:
            dl = d["dl"]
            if dl == 0:
                continue
            tf_counts = {}
            for t in d["tokens"]:
                tf_counts[t] = tf_counts.get(t, 0) + 1
            s = 0.0
            for t, qcnt in qf.items():
                tf = tf_counts.get(t, 0)
                if tf == 0:
                    continue
                df = idx["df"].get(t, 0)
                idf = math.log(1 + (idx["N"] - df + 0.5) / (df + 0.5))
                denom = tf + k1 * (1 - b + b * dl / idx["avgdl"])
                s += idf * (tf * (k1 + 1)) / denom * qcnt
            if s > 0:
                scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        parts = []
        for _s, d in scored[:k]:
            label = "NEST 知识" if d["tag"] == "NEST" else "记忆线索"
            parts.append(f"[{label}] {d['para']}")
        return "\n\n".join(parts)
    except Exception:
        return ""
