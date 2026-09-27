# -*- coding: utf-8 -*-
"""AntNest · 结构化日志系统

三层输出：
  - 控制台：人类可读，带颜色和级别标记
  - 文件：JSON 结构化，支持轮转
  - 桥接事件：路由到 UI 的 log 事件

零外部依赖，仅使用标准库 logging。
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

# ====================== 日志级别映射 ======================
# 桥接层 tag 映射：queen/sys/worker/warn → 对应 logging 级别
TAG_LEVELS = {
    "queen": logging.INFO,
    "sys": logging.DEBUG,
    "worker": logging.INFO,
    "warn": logging.WARNING,
}

# 控制台颜色（ANSI）
_COLORS = {
    "DEBUG": "\033[36m",      # 青色
    "INFO": "\033[32m",       # 绿色
    "WARNING": "\033[33m",    # 黄色
    "ERROR": "\033[31m",      # 红色
    "CRITICAL": "\033[35m",   # 紫色
}
_RESET = "\033[0m"
_DIM = "\033[2m"

# ====================== 格式化器 ======================

class ConsoleFormatter(logging.Formatter):
    """控制台格式：[级别] 模块名: 消息"""

    def format(self, record: logging.LogRecord) -> str:
        levelname = record.levelname
        color = _COLORS.get(levelname, "")
        reset = _RESET if color else ""

        # 模块名：取最后一段，去掉 antnest_ 前缀
        mod = record.name
        if "." in mod:
            mod = mod.rsplit(".", 1)[-1]
        if mod.startswith("antnest_"):
            mod = mod[8:]

        msg = record.getMessage()

        # 带时间戳的完整格式（DEBUG 级别）
        if record.levelno <= logging.DEBUG:
            ts = time.strftime("%H:%M:%S", time.localtime(record.created))
            return f"{_DIM}{ts}{_RESET} {color}[{levelname:7s}]{reset} {mod}: {msg}"

        return f"{color}[{levelname:7s}]{reset} {mod}: {msg}"


class FileFormatter(logging.Formatter):
    """文件格式：JSON 结构化，每行一个 JSON 对象"""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "ms": int(record.created * 1000) % 1000,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # 附加 extra 字段
        if hasattr(record, "extra_data") and record.extra_data:
            entry["extra"] = record.extra_data
        if record.exc_info and record.exc_info[0]:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


# ====================== 桥接事件处理器 ======================

class BridgeLogHandler(logging.Handler):
    """将日志记录路由到桥接层的事件系统。

    使用方式：
        handler = BridgeLogHandler(emit_fn)
        logger.addHandler(handler)

    emit_fn 签名：emit_fn(tag: str, text: str)
    """

    def __init__(self, emit_fn: Optional[Callable[[str, str], None]] = None) -> None:
        super().__init__(logging.DEBUG)
        self._emit_fn: Optional[Callable[[str, str], None]] = emit_fn
        self._lock = threading.Lock()

    def set_emit(self, emit_fn: Optional[Callable[[str, str], None]]) -> None:
        with self._lock:
            self._emit_fn = emit_fn

    def emit(self, record: logging.LogRecord) -> None:
        if not self._emit_fn:
            return
        try:
            tag = self._level_to_tag(record.levelno)
            text = self.format(record)
            with self._lock:
                self._emit_fn(tag, text)
        except Exception:
            pass

    @staticmethod
    def _level_to_tag(level: int) -> str:
        if level >= logging.WARNING:
            return "warn"
        if level >= logging.INFO:
            return "sys"
        return "sys"


# ====================== 审计日志 ======================

class AuditLogger:
    """独立审计日志，记录所有工具调用和关键操作。

    输出到 .antnest/audit.log，JSON 格式，支持轮转。
    """

    def __init__(self, log_dir: str) -> None:
        self._log_dir = log_dir
        self._logger = logging.getLogger("antnest.audit")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "audit.log")

        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(FileFormatter())
        self._logger.addHandler(handler)

    def log_tool_call(self, name: str, args: dict, result_preview: str = "") -> None:
        """记录工具调用"""
        self._logger.info(
            f"tool_call: {name}",
            extra={"extra_data": {
                "tool": name,
                "args": _safe_args(args),
                "result_preview": result_preview[:200] if result_preview else "",
            }},
        )

    def log_tool_result(self, name: str, status: str, duration_ms: float = 0) -> None:
        """记录工具结果"""
        self._logger.info(
            f"tool_result: {name} → {status}",
            extra={"extra_data": {
                "tool": name,
                "status": status,
                "duration_ms": round(duration_ms, 1),
            }},
        )

    def log_worker_spawn(self, worker_id: str, task: str, depth: int) -> None:
        """记录工蚁创建"""
        self._logger.info(
            f"worker_spawn: {worker_id}",
            extra={"extra_data": {
                "worker_id": worker_id,
                "task": task[:200],
                "depth": depth,
            }},
        )

    def log_worker_done(self, worker_id: str, exit_code: int, duration_ms: float) -> None:
        """记录工蚁完成"""
        self._logger.info(
            f"worker_done: {worker_id} exit={exit_code}",
            extra={"extra_data": {
                "worker_id": worker_id,
                "exit_code": exit_code,
                "duration_ms": round(duration_ms, 1),
            }},
        )

    def log_config_change(self, key: str, old_value: Any, new_value: Any) -> None:
        """记录配置变更"""
        self._logger.info(
            f"config_change: {key}",
            extra={"extra_data": {
                "key": key,
                "old": _safe_value(old_value),
                "new": _safe_value(new_value),
            }},
        )

    def log_security_event(self, event_type: str, detail: str) -> None:
        """记录安全事件"""
        self._logger.warning(
            f"security: {event_type}",
            extra={"extra_data": {
                "event_type": event_type,
                "detail": detail[:500],
            }},
        )


def _safe_args(args: dict) -> dict:
    """安全序列化工具参数（截断过长值）"""
    result: dict = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 500:
            result[k] = v[:500] + f"...({len(v)} chars)"
        else:
            result[k] = v
    return result


def _safe_value(val: Any) -> str:
    """安全转换配置值"""
    s = str(val)
    return s[:100] if len(s) > 100 else s


# ====================== 全局初始化 ======================

_initialized = False
_bridge_handler = BridgeLogHandler()
_audit: AuditLogger | None = None


def setup(log_dir: str | None = None, level: int = logging.INFO) -> None:
    """初始化日志系统。

    Args:
        log_dir: 日志目录（默认 .antnest/）
        level: 控制台日志级别
    """
    global _initialized, _audit
    if _initialized:
        return
    _initialized = True

    if log_dir is None:
        log_dir = os.path.join(os.getcwd(), ".antnest")

    # 根 logger
    root = logging.getLogger("antnest")
    root.setLevel(logging.DEBUG)

    # 清除已有 handler（避免重复）
    root.handlers.clear()

    # 控制台 handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(ConsoleFormatter())
    root.addHandler(console)

    # 文件 handler（结构化 JSON）
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "antnest.log")
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(FileFormatter())
    root.addHandler(file_handler)

    # 桥接 handler
    root.addHandler(_bridge_handler)

    # 审计 logger
    _audit = AuditLogger(log_dir)


def get_logger(name: str) -> logging.Logger:
    """获取命名 logger。

    所有 logger 都挂在 antnest 命名空间下：
        get_logger("config") → antnest.config
        get_logger("loop")   → antnest.loop
    """
    if not name.startswith("antnest"):
        name = f"antnest.{name}"
    return logging.getLogger(name)


def set_bridge_emit(emit_fn: Optional[Callable[[str, str], None]]) -> None:
    """设置桥接层事件回调。

    在 antnest_bridge.py 初始化时调用：
        antnest_log.set_bridge_emit(bridge.emit)
    """
    _bridge_handler.set_emit(emit_fn)


def get_audit() -> AuditLogger:
    """获取审计日志实例"""
    if _audit is None:
        setup()
    assert _audit is not None
    return _audit


# ====================== 兼容层：替换 print() ======================

def log_print(*args: Any, level: int = logging.INFO, tag: str = "sys", **kwargs: Any) -> None:
    """替代 print() 的日志函数。

    用法：
        from antnest_log import log_print
        log_print(f"工蚁 {worker_id} 完成", level=logging.INFO, tag="worker")
    """
    msg = " ".join(str(a) for a in args)
    # 去掉 end="" 和 flush=True 等 print 特有参数
    logger = get_logger("core")
    logger.log(level, msg)
