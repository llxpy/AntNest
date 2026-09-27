# -*- coding: utf-8 -*-
"""AntNest · 分层异常体系 + 错误码

统一异常分类，每个异常携带：
  - code: 机器可读错误码（如 'CFG-001'），便于日志/监控/API 识别
  - user_msg: 面向用户的友好提示（中文）
  - 保留异常链

设计原则：
  - 所有业务异常继承 AntNestError
  - 不在库边界抛出裸 Exception/RuntimeError（除非系统级）
  - 保持向后兼容：现有代码抛出的 ValueError/RuntimeError 不被破坏
"""
from __future__ import annotations


class AntNestError(Exception):
    """AntNest 统一异常基类。

    所有业务异常应继承此类。携带错误码与用户友好消息。
    """

    default_code = "ERR-000"
    default_user_msg = "发生未知错误"

    def __init__(self, message: str = "", *, code: str | None = None, user_msg: str | None = None):
        self.code = code or self.default_code
        self.user_msg = user_msg or self.default_user_msg
        super().__init__(message or self.user_msg)

    def to_dict(self) -> dict[str, str]:
        """转为 JSON 友好字典（供工具/UI/API 返回）。"""
        return {
            "error": str(self),
            "code": self.code,
            "user_msg": self.user_msg,
        }


# ====================== 分层异常 ======================

class ConfigError(AntNestError):
    """配置相关错误（config.json 解析、字段校验、密钥缺失）。"""

    default_code = "CFG-001"
    default_user_msg = "配置错误"


class ApiError(AntNestError):
    """API 调用错误（LLM 请求失败、认证失败、模型不可用）。"""

    default_code = "API-001"
    default_user_msg = "API 调用失败"


class NetworkError(AntNestError):
    """网络层错误（连接失败、超时、DNS）。"""

    default_code = "NET-001"
    default_user_msg = "网络连接失败"


class ToolError(AntNestError):
    """工具执行错误（文件操作、CLI、代码执行）。"""

    default_code = "TOOL-001"
    default_user_msg = "工具执行失败"


class WorkerError(AntNestError):
    """工蚁相关错误（spawn 失败、任务超时、执行崩溃）。"""

    default_code = "WKR-001"
    default_user_msg = "工蚁执行失败"


class SafetyError(AntNestError, ValueError):
    """安全拦截（危险命令、路径穿越、越权访问）。

    继承 ValueError 以保持向后兼容（旧代码/测试捕获 ValueError）。
    """

    default_code = "SEC-001"
    default_user_msg = "操作被安全策略拦截"


class SessionError(AntNestError):
    """会话相关错误（锁冲突、会话文件损坏）。"""

    default_code = "SES-001"
    default_user_msg = "会话操作失败"


class MemoryError(AntNestError):
    """记忆系统错误。"""

    default_code = "MEM-001"
    default_user_msg = "记忆操作失败"


# ====================== 助手函数 ======================

def as_antnest_error(exc: Exception, default: type[AntNestError] = AntNestError) -> AntNestError:
    """将任意异常包装为 AntNestError（若已是则原样返回）。

    用于在调用边界统一异常类型，同时保留原始错误链。
    """
    if isinstance(exc, AntNestError):
        return exc
    wrapped: AntNestError = default(str(exc))
    wrapped.__cause__ = exc
    return wrapped
