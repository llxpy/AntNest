# -*- coding: utf-8 -*-
"""记忆树（memory_tree）：B+Tree 思想 + RAG 检索的记忆管理层（零依赖）。

为什么说「B+Tree 思想」而不是完整实现：
- 分层：anchor（锚点/人设）< knowledge（NEST.md / hints.md 文档叶）
        < experience（经验/偏好）< episodic（会话事件）
- 有序索引：叶子按 key 排序；叶子满则分裂并重平衡；叶子间通过 next 指针连成链，
  范围扫描沿链前进，定位叶子用二分 → 有界深度、不随数据规模线性增长索引层级。
- RAG：查询 tokenize 后对候选做「BM25 词法分 × 层级权重 + 重要度 + 热度衰减」的
  混合打分，取 top-k 注入上下文；命中项会被 note_usage 提升热度，长期热点可 promote。

零依赖、原子写盘（临时文件 + os.replace）；任何异常都优雅降级为空结果，
不影响 Agent 主循环。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from bisect import insort
from pathlib import Path

import memory_retrieval

LEVELS = ("anchor", "knowledge", "experience", "episodic")
_LEVEL_WEIGHT = {"anchor": 2.0, "knowledge": 1.0, "experience": 0.9, "episodic": 0.5}
_LEVEL_LABEL = {"anchor": "锚点", "knowledge": "记忆线索", "experience": "经验", "episodic": "事件"}
LEAF_CAPACITY = 8
EPISODIC_CAP = 40
MAX_ITEMS = 600


def _now() -> int:
    return int(time.time())


def _item(key, content, level, source="", tags=None):
    return {
        "key": str(key),
        "content": str(content)[:20000],
        "level": str(level),
        "source": str(source) or "",
        "tags": list(tags or []),
        "importance": 0.5,
        "created": _now(),
        "updated": _now(),
        "access_count": 0,
        "last_access": None,
    }


def _leaf(leaf_id, start_key=""):
    return {"id": leaf_id, "start": start_key, "next": None, "items": []}


def _default_data() -> dict:
    return {
        "version": 1,
        "leaves": [_leaf("L1")],
        "meta": {"created": _now(), "updated": _now()},
    }


class MemoryTree:
    """分层有序记忆树。索引文件是单个 JSON，结构见 _default_data。"""

    def __init__(self, path):
        self.path = str(path)
        self.data = _default_data()

    # ---------- 定位 / 遍历 ----------
    def _locate_leaf(self, key):
        """二分：找最后一个 start <= key 的叶子（B+Tree 思想的叶子定位）。"""
        leaves = self.data["leaves"]
        lo, hi = 0, len(leaves)
        while lo < hi:
            mid = (lo + hi) // 2
            if leaves[mid]["start"] <= key:
                lo = mid + 1
            else:
                hi = mid
        return leaves[lo - 1] if lo > 0 else leaves[0]

    def _all_items(self):
        for leaf in self.data["leaves"]:
            yield from leaf["items"]

    def _reindex(self):
        """分裂后保持叶子有序、start 正确、next 链成环。"""
        leaves = sorted(
            self.data["leaves"],
            key=lambda l: l["items"][0]["key"] if l["items"] else "",
        )
        for i, leaf in enumerate(leaves):
            leaf["start"] = leaf["items"][0]["key"] if leaf["items"] else ""
            leaf["next"] = leaves[i + 1]["id"] if i + 1 < len(leaves) else None
        self.data["leaves"] = leaves

    # ---------- 增删查 ----------
    def get(self, key) -> dict | None:
        leaf = self._locate_leaf(key)
        for it in leaf["items"]:
            if it["key"] == key:
                return it
        return None

    def insert(self, key, content, level="knowledge", source="", tags=None,
               importance=None) -> bool:
        """按 key 有序插入；同名 key 则更新内容与元数据。返回 True=新增。"""
        existing = self.get(key)
        if existing:
            existing["content"] = str(content)[:20000]
            if level in LEVELS:
                existing["level"] = level
            if source:
                existing["source"] = str(source)
            if tags:
                existing["tags"] = list({*existing["tags"], *tags})
            existing["updated"] = _now()
            return False
        total = sum(len(leaf["items"]) for leaf in self.data["leaves"])
        if total >= MAX_ITEMS:
            return False
        leaf = self._locate_leaf(key)
        it = _item(key, content, level, source, tags)
        if importance is not None:
            it["importance"] = max(0.0, min(1.0, float(importance)))
        insort(leaf["items"], it, key=lambda x: x["key"])
        self._maybe_split(leaf)
        self.data["meta"]["updated"] = _now()
        return True

    def _maybe_split(self, leaf):
        if len(leaf["items"]) <= LEAF_CAPACITY:
            return
        mid = LEAF_CAPACITY // 2
        new = _leaf(f"L{_now()}-{len(self.data['leaves'])}")
        new["items"] = leaf["items"][mid:]
        leaf["items"] = leaf["items"][:mid]
        self.data["leaves"].append(new)
        self._reindex()

    def delete(self, key) -> bool:
        leaf = self._locate_leaf(key)
        before = len(leaf["items"])
        leaf["items"] = [it for it in leaf["items"] if it["key"] != key]
        removed = before != len(leaf["items"])
        if removed:
            if not leaf["items"]:
                self.data["leaves"] = [x for x in self.data["leaves"] if x["id"] != leaf["id"]]
                self._reindex()
            self.data["meta"]["updated"] = _now()
        return removed

    def search_prefix(self, prefix) -> list:
        """范围扫描：从最后一个 start <= prefix 的叶子起，沿 next 链收集（B+Tree 叶链扫描）。"""
        prefix = str(prefix)
        leaves = self.data["leaves"]
        if not leaves:
            return []
        leaf = self._locate_leaf(prefix)
        out = []
        seen = set()
        while leaf is not None and leaf["id"] not in seen:
            seen.add(leaf["id"])
            out.extend(it for it in leaf["items"] if it["key"].startswith(prefix))
            nxt = leaf.get("next")
            leaf = next((l for l in leaves if l["id"] == nxt), None)
        return out

    # ---------- 分层操作 ----------
    def promote(self, key, to_level) -> bool:
        if to_level not in LEVELS:
            return False
        it = self.get(key)
        if not it:
            return False
        it["level"] = to_level
        it["updated"] = _now()
        return True

    def note_usage(self, keys) -> None:
        for k in keys or []:
            it = self.get(k)
            if it:
                it["access_count"] = int(it.get("access_count", 0)) + 1
                it["last_access"] = _now()
                it["importance"] = min(1.0, float(it.get("importance", 0.5)) + 0.04)

    def record_episodic(self, kind, title, text, level="episodic") -> bool:
        key = (
            f"epi-{_now()}-{kind}-"
            f"{hashlib.md5(str(title).encode('utf-8')).hexdigest()[:6]}"
        )
        inserted = self.insert(
            key, f"[{kind}] {str(title)[:200]}\n{str(text)[:3000]}",
            level=level, source="session", tags=[kind],
        )
        epi = [it for it in self._all_items() if it["level"] == "episodic"]
        if len(epi) > EPISODIC_CAP:
            epi.sort(key=lambda x: x.get("created", 0))
            for old in epi[: len(epi) - EPISODIC_CAP]:
                self.delete(old["key"])
        return inserted

    def sync_from_sources(self, sources) -> int:
        """把 markdown 记忆源按段落 upsert 进 knowledge 层。sources: [(path, tag)]"""
        added = 0
        for path, tag in sources:
            try:
                txt = Path(path).read_text(encoding="utf-8")
            except Exception:
                continue
            for para in re.split(r"\n\s*\n+", txt):
                para = para.strip()
                if len(para) < 10:
                    continue
                digest = hashlib.md5(para.encode("utf-8")).hexdigest()[:10]
                if self.insert(f"{tag}:{digest}", para, level="knowledge",
                               source=tag, tags=[tag]):
                    added += 1
        return added

    # ---------- RAG 检索 ----------
    def retrieve(self, query, k=4) -> str:
        """混合打分检索：BM25 词法分 × 层级权重 + 重要度 + 热度衰减。返回格式化文本。"""
        q = memory_retrieval.tokenize(query or "")
        if not q:
            return ""
        items = list(self._all_items())
        if not items:
            return ""
        qf = {}
        for t in q:
            qf[t] = qf.get(t, 0) + 1
        tf_map, df, dl_sum = {}, {}, 0
        for it in items:
            toks = memory_retrieval.tokenize(it["content"])
            tmap = {}
            for t in toks:
                tmap[t] = tmap.get(t, 0) + 1
            tf_map[it["key"]] = tmap
            dl_sum += max(sum(tmap.values()), 1)
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        n = max(len(items), 1)
        avgdl = max(dl_sum / n, 1.0)
        now = _now()
        scored = []
        for it in items:
            tmap = tf_map[it["key"]]
            toks_n = max(sum(tmap.values()), 1)
            s = 0.0
            for t, qcnt in qf.items():
                cnt = tmap.get(t, 0)
                if not cnt:
                    continue
                idf = math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
                s += idf * (cnt * 1.2) / (cnt + 1.2 * (0.25 + 0.75 * toks_n / avgdl)) * qcnt
            last = it.get("last_access")
            recency = math.exp(-max(0.0, (now - last) / 86400.0) / 14.0) if last else 0.3
            total = (
                s * _LEVEL_WEIGHT.get(it["level"], 1.0)
                + float(it.get("importance", 0.5)) * 1.2
                + recency * 0.5
            )
            if total > 0.0:
                scored.append((total, it))
        scored.sort(key=lambda x: -x[0])
        hits = scored[:k]
        self.note_usage([it["key"] for _, it in hits])
        parts = []
        for _score, it in hits:
            lv = it.get("level", "knowledge")
            src = it.get("source") or ""
            label = _LEVEL_LABEL.get(lv, lv)
            if lv == "knowledge" and src == "NEST":
                label = "知识"
            parts.append(f"[{label}] {it['content'][:2000]}")
        return "\n\n".join(parts)

    # ---------- 统计 / 持久化 ----------
    def stats(self) -> dict:
        by_level = {lv: 0 for lv in LEVELS}
        for it in self._all_items():
            lv = it.get("level", "knowledge")
            by_level[lv] = by_level.get(lv, 0) + 1
        return {
            "total": sum(by_level.values()),
            "levels": by_level,
            "leaves": len(self.data["leaves"]),
        }

    def save(self) -> None:
        try:
            target = Path(self.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".mem-", suffix=".json",
                                       dir=str(target.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False)
                os.replace(tmp, target)
            finally:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass
        except Exception:
            pass

    @classmethod
    def load(cls, path) -> "MemoryTree":
        tree = cls(path)
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("leaves"), list) \
                    and data["leaves"]:
                tree.data = data
                if not tree.data.get("meta"):
                    tree.data["meta"] = _default_data()["meta"]
        except Exception:
            pass
        return tree


def load(path) -> MemoryTree:
    return MemoryTree.load(path)