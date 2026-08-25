# -*- coding: utf-8 -*-
"""回归测试：记忆树（B+Tree 思想 + RAG 检索）。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import memory_tree as mt


class MemoryTreeTest(unittest.TestCase):
    def test_insert_get_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory_tree.json")
            tree = mt.load(path)
            self.assertTrue(tree.insert("k1", "AntNest 的蚁后不直接执行命令", level="knowledge", source="NEST"))
            self.assertTrue(tree.insert("k2", "记忆由记忆树管理", level="experience"))
            self.assertIsNotNone(tree.get("k1"))
            self.assertEqual(tree.get("k1")["level"], "knowledge")
            self.assertTrue(tree.delete("k1"))
            self.assertIsNone(tree.get("k1"))

    def test_leaf_split_and_search(self):
        """超过叶容量后分裂，且所有 key 仍可查询。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory_tree.json")
            tree = mt.load(path)
            for i in range(24):
                self.assertTrue(tree.insert(f"item-{i:03d}", f"内容段落 {i}", level="knowledge", tags=["t"]))
            st = tree.stats()
            self.assertGreater(st["leaves"], 1, "超过叶容量应触发分裂")
            self.assertEqual(st["total"], 24)
            for i in range(24):
                self.assertIsNotNone(tree.get(f"item-{i:03d}"), f"分裂后仍可查到 item-{i:03d}")
            self.assertEqual(len(tree.search_prefix("item-01")), 10)

    def test_retrieve_rag_topk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory_tree.json")
            tree = mt.load(path)
            tree.insert("n1", "工蚁在隔离目录执行命令，执行完毕销毁", level="knowledge", source="NEST")
            tree.insert("n2", "用户偏好：先验证再报告，结论先行", level="experience", source="hints")
            tree.insert("n3", "今天天气很好适合散步", level="knowledge", source="NEST")
            hit = tree.retrieve("工蚁执行命令应该怎么隔离", k=2)
            self.assertIn("工蚁在隔离目录执行命令", hit)
            self.assertIn("[知识]", hit)
            # 无意义查询不命中核心主题
            hit2 = tree.retrieve("天气")
            self.assertIn("天气", hit2)

    def test_episodic_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory_tree.json")
            tree = mt.load(path)
            for i in range(mt.EPISODIC_CAP + 10):
                tree.record_episodic("task", f"任务 {i}", f"内容 {i}")
            st = tree.stats()
            self.assertLessEqual(st["levels"]["episodic"], mt.EPISODIC_CAP)

    def test_sync_from_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "NEST.md"
            md.write_text(
                "# 知识库\n\n蚁后只负责拆解与决策，工蚁负责执行。\n\n另一个主题：验证优先。",
                encoding="utf-8",
            )
            path = os.path.join(tmp, "memory_tree.json")
            tree = mt.load(path)
            added = tree.sync_from_sources([(str(md), "NEST")])
            self.assertGreaterEqual(added, 2)
            hit = tree.retrieve("蚁后负责什么", k=1)
            self.assertIn("蚁后只负责拆解", hit)

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "memory_tree.json")
            tree = mt.load(path)
            tree.insert("persist-1", "持久化记忆内容", level="knowledge")
            tree.save()
            reloaded = mt.load(path)
            self.assertEqual(reloaded.get("persist-1")["content"], "持久化记忆内容")
            self.assertEqual(reloaded.stats()["total"], 1)


if __name__ == "__main__":
    unittest.main()