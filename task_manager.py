# -*- coding: utf-8 -*-
"""
任务状态机：pending → running → done → verified

用于跟踪工蚁任务的生命周期，支持：
- 任务状态流转
- 幂等保护（相同命令不重复派发）
- 验证工蚁机制
"""

import json
import hashlib
import time
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    VERIFIED = "verified"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class Task:
    """单个任务的状态跟踪"""
    
    def __init__(self, task_id: str, command: str, label: str = ""):
        self.task_id = task_id
        self.command = command
        self.label = label
        self.status = TaskStatus.PENDING
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.verified_at: Optional[float] = None
        self.result: Optional[str] = None
        self.verify_result: Optional[str] = None
        self.clone_dir: Optional[str] = None
        self.retry_count: int = 0
        self.command_hash: str = hashlib.md5(command.encode()).hexdigest()[:12]
    
    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "command": self.command[:200] + ("..." if len(self.command) > 200 else ""),
            "label": self.label,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "verified_at": self.verified_at,
            "retry_count": self.retry_count,
            "command_hash": self.command_hash,
            "has_result": self.result is not None,
            "has_verify_result": self.verify_result is not None,
        }
    
    def mark_running(self):
        self.status = TaskStatus.RUNNING
        self.started_at = time.time()
    
    def mark_done(self, result: str):
        self.status = TaskStatus.DONE
        self.finished_at = time.time()
        self.result = result
    
    def mark_verified(self, verify_result: str):
        self.status = TaskStatus.VERIFIED
        self.verified_at = time.time()
        self.verify_result = verify_result
    
    def mark_failed(self, error: str):
        self.status = TaskStatus.FAILED
        self.finished_at = time.time()
        self.result = json.dumps({"status": "error", "error": error}, ensure_ascii=False)
    
    def mark_timeout(self):
        self.status = TaskStatus.TIMEOUT
        self.finished_at = time.time()
    
    def mark_cancelled(self):
        self.status = TaskStatus.CANCELLED
        self.finished_at = time.time()


class TaskManager:
    """任务管理器：跟踪所有工蚁任务"""
    
    def __init__(self, max_history: int = 100):
        self.tasks: dict[str, Task] = {}
        self.max_history = max_history
        self._recent_hashes: dict[str, str] = {}  # command_hash -> task_id
    
    def create_task(self, task_id: str, command: str, label: str = "") -> Task:
        """创建新任务"""
        task = Task(task_id, command, label)
        self.tasks[task_id] = task
        self._recent_hashes[task.command_hash] = task_id
        self._cleanup_old_tasks()
        return task
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务"""
        return self.tasks.get(task_id)
    
    def find_by_hash(self, command_hash: str) -> Optional[Task]:
        """根据命令哈希查找最近的任务（幂等检查）"""
        task_id = self._recent_hashes.get(command_hash)
        if task_id:
            return self.tasks.get(task_id)
        return None
    
    def get_pending_verification(self) -> list[Task]:
        """获取所有待验证的任务"""
        return [t for t in self.tasks.values() if t.status == TaskStatus.DONE]
    
    def get_running_tasks(self) -> list[Task]:
        """获取所有运行中的任务"""
        return [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]
    
    def get_stats(self) -> dict:
        """获取任务统计"""
        stats = {s.value: 0 for s in TaskStatus}
        for task in self.tasks.values():
            stats[task.status.value] += 1
        return stats
    
    def _cleanup_old_tasks(self):
        """清理旧任务，保持历史记录在限制内"""
        if len(self.tasks) <= self.max_history:
            return
        # 按创建时间排序，删除最旧的
        sorted_tasks = sorted(self.tasks.values(), key=lambda t: t.created_at)
        to_remove = sorted_tasks[:len(sorted_tasks) - self.max_history]
        for task in to_remove:
            del self.tasks[task.task_id]
            # 清理哈希映射
            if self._recent_hashes.get(task.command_hash) == task.task_id:
                del self._recent_hashes[task.command_hash]


# 全局任务管理器实例
_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """获取全局任务管理器"""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
