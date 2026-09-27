# -*- coding: utf-8 -*-
"""AntNest · 配置校验（零依赖 schema 校验）

基于标准库 dataclass + 显式校验规则，验证 config.json 结构。
不引入 Pydantic，保持项目「零外部运行时依赖」约束。

校验策略：
  - 类型检查：字段为预期类型
  - 值范围：数值在合法区间
  - 枚举：枚举字段在合法集合内
  - 结构：必需字段存在

校验失败不崩溃，返回问题列表，由调用方决定如何处理。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import antnest_log

_log = antnest_log.get_logger("config")


@dataclass
class ApiConfig:
    """API 层配置"""

    base_url: str = "https://api.deepseek.com/v1"
    model_name: str = "deepseek-v4-flash"
    api_key: str = ""
    thinking_mode: str = "auto"
    skip_model_check: bool = False
    default_token_cap: int = 128000
    temperature: float = 0.6

    _VALID_THINKING_MODES = {"auto", "on", "off", "true", "false", "1", "0", "enabled", "disabled"}


@dataclass
class AgentConfig:
    """Agent 层配置"""

    max_depth: int = 2
    max_clones: dict = field(default_factory=lambda: {"0": 10, "1": 5, "2": 3})
    compact_threshold: float = 0.85
    tool_result_max_len: int = 8000
    worker_timeout: int = 300
    restore_on_restart: bool = True


@dataclass
class PermissionsConfig:
    """权限层配置（v1.4 新增）。

    默认值逐项复刻 v1.3.1 的实际行为：读写执行联网全放行，危险命令与 AntNest
    自身源码需用户确认。见 docs/v1.4-DESIGN.md §5.4。
    """

    # 当前授予的权限等级 L0–L5。设 0 即「只读审阅模式」。
    level: int = 3
    # 超过此等级直接硬拒绝（不再询问）。等于 level 时表示「未授予的一律询问」。
    ask_above_level: int = 3
    # 权限判定与放行/拒绝是否落审计日志
    audit: bool = True
    # 任务检查点（v1.4 新增，见 antnest_checkpoint）
    checkpoint_enabled: bool = True
    checkpoint_keep: int = 20

    _VALID_LEVELS = (0, 1, 2, 3, 4, 5)


@dataclass
class AppConfig:
    """根配置容器"""

    api: ApiConfig = field(default_factory=ApiConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)

    # 未识别字段名（用于警告，不阻断）
    unknown_api_keys: list[str] = field(default_factory=list)
    unknown_agent_keys: list[str] = field(default_factory=list)
    unknown_permissions_keys: list[str] = field(default_factory=list)

    # 校验问题汇总（供启动报告展示）
    problems: list[str] = field(default_factory=list)


# ====================== 单字段校验 ======================

def _check_int(value: Any, name: str, lo: int, hi: int, problems: list[str]) -> int:
    """校验整数范围，越界时记录问题并返回钳制值。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        problems.append(f"{name}：应为整数（{value!r}）")
        return lo
    if v < lo or v > hi:
        problems.append(f"{name}：超出范围 [{lo}, {hi}]，实际 {v}")
        return max(lo, min(hi, v))
    return v


def _check_float(value: Any, name: str, lo: float, hi: float, problems: list[str]) -> float:
    """校验浮点范围。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        problems.append(f"{name}：应为数字（{value!r}）")
        return lo
    if v < lo or v > hi:
        problems.append(f"{name}：超出范围 [{lo}, {hi}]，实际 {v}")
        return max(lo, min(hi, v))
    return v


def _check_bool(value: Any, name: str, problems: list[str]) -> bool:
    """校验布尔值（容忍字符串 true/false/1/0）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    problems.append(f"{name}：无法解析为布尔值（{value!r}）")
    return False


def _check_http_url(value: Any, name: str, problems: list[str]) -> str:
    """校验 HTTP(S) URL。"""
    s = str(value or "").strip()
    if s and not (s.startswith("http://") or s.startswith("https://")):
        problems.append(f"{name}：URL 缺少协议前缀（{s!r}），应为 http(s)://")
    return s


def _check_level(value: Any, name: str, problems: list[str]) -> int:
    """校验权限等级 L0–L5（也接受 "L3" / "network" 这类写法）。

    无法识别时回退到该字段的**文档默认值**（NETWORK=3，与 v1.3.1 行为一致），
    而不是最保守的 READ——后者会让「配置写错」静默变成「降权运行」。
    """
    from antnest_permissions import PermLevel

    fallback = int(PermLevel.NETWORK)
    if value is None:
        return fallback
    if isinstance(value, bool):
        problems.append(f"{name}：应为 0–5 的等级（收到布尔值 {value!r}），回退 {fallback}")
        return fallback
    parsed = PermLevel.parse(value, None)
    if parsed is None:
        problems.append(
            f"{name}：无法识别的权限等级（{value!r}），应为 0–5 或 L0–L5，回退 {fallback}"
        )
        return fallback
    return int(parsed)


# ====================== 主校验入口 ======================

def validate_config(config: dict | None) -> AppConfig:
    """校验整个配置字典，返回规范化后的 AppConfig。

    只读：不修改原字典，只记录问题并返回安全默认值。
    """
    config = config or {}
    problems: list[str] = []
    api_unknown: list[str] = []
    agent_unknown: list[str] = []
    perm_unknown: list[str] = []
    api_cfg = ApiConfig()
    agent_cfg = AgentConfig()
    perm_cfg = PermissionsConfig()

    # ---- api 层 ----
    raw_api = config.get("api", {})
    if not isinstance(raw_api, dict):
        problems.append("api：应为对象")
        raw_api = {}

    _api_fields = {f for f in ApiConfig.__dataclass_fields__ if not f.startswith("_")}
    for k in raw_api:
        if k not in _api_fields:
            api_unknown.append(k)

    if "base_url" in raw_api:
        api_cfg.base_url = _check_http_url(raw_api.get("base_url"), "api.base_url", problems)
    if "model_name" in raw_api:
        api_cfg.model_name = str(raw_api.get("model_name") or "")
    if "api_key" in raw_api:
        api_cfg.api_key = str(raw_api.get("api_key") or "")
    if "skip_model_check" in raw_api:
        api_cfg.skip_model_check = _check_bool(raw_api.get("skip_model_check"), "api.skip_model_check", problems)
    if "default_token_cap" in raw_api:
        _tok_raw = raw_api.get("default_token_cap")
        if _tok_raw is None:
            pass
        else:
            try:
                _tok = int(_tok_raw)
                api_cfg.default_token_cap = _check_int(_tok, "api.default_token_cap", 1000, 2_000_000, problems)
            except (TypeError, ValueError):
                problems.append("api.default_token_cap：应为整数，回退默认 128000")
    thinking = str(raw_api.get("thinking_mode") or "auto").strip().lower()
    if thinking not in ApiConfig._VALID_THINKING_MODES:
        problems.append(f"api.thinking_mode：无效值（{thinking!r}），应为 {sorted(ApiConfig._VALID_THINKING_MODES)}")
        thinking = "auto"
    api_cfg.thinking_mode = thinking
    if "temperature" in raw_api:
        api_cfg.temperature = _check_float(
            raw_api.get("temperature"), "api.temperature", 0.0, 2.0, problems
        )

    # ---- agent 层 ----
    raw_agent = config.get("agent", {})
    if not isinstance(raw_agent, dict):
        problems.append("agent：应为对象")
        raw_agent = {}

    _agent_fields = {f for f in AgentConfig.__dataclass_fields__ if not f.startswith("_")}
    for k in raw_agent:
        if k not in _agent_fields:
            agent_unknown.append(k)

    if "max_depth" in raw_agent:
        agent_cfg.max_depth = _check_int(raw_agent.get("max_depth"), "agent.max_depth", 0, 10, problems)
    if "compact_threshold" in raw_agent:
        agent_cfg.compact_threshold = _check_float(
            raw_agent.get("compact_threshold"), "agent.compact_threshold", 0.1, 1.0, problems
        )
    if "tool_result_max_len" in raw_agent:
        agent_cfg.tool_result_max_len = _check_int(
            raw_agent.get("tool_result_max_len"), "agent.tool_result_max_len", 100, 1_000_000, problems
        )
    if "worker_timeout" in raw_agent:
        agent_cfg.worker_timeout = _check_int(
            raw_agent.get("worker_timeout"), "agent.worker_timeout", 1, 3600, problems
        )
    if "restore_on_restart" in raw_agent:
        agent_cfg.restore_on_restart = _check_bool(
            raw_agent.get("restore_on_restart"), "agent.restore_on_restart", problems
        )
    if "max_clones" in raw_agent:
        mc = raw_agent.get("max_clones")
        if isinstance(mc, dict):
            try:
                agent_cfg.max_clones = {str(k): max(0, int(v)) for k, v in mc.items()}
            except (TypeError, ValueError):
                problems.append("agent.max_clones：值应为整数")
        else:
            problems.append("agent.max_clones：应为对象")

    # ---- permissions 层（v1.4 新增） ----
    raw_perm = config.get("permissions", {})
    if not isinstance(raw_perm, dict):
        problems.append("permissions：应为对象")
        raw_perm = {}

    _perm_fields = {f for f in PermissionsConfig.__dataclass_fields__ if not f.startswith("_")}
    for k in raw_perm:
        if k not in _perm_fields:
            perm_unknown.append(k)

    if "level" in raw_perm:
        perm_cfg.level = _check_level(raw_perm.get("level"), "permissions.level", problems)
    if "ask_above_level" in raw_perm:
        perm_cfg.ask_above_level = _check_level(
            raw_perm.get("ask_above_level"), "permissions.ask_above_level", problems
        )
    if "audit" in raw_perm:
        perm_cfg.audit = _check_bool(raw_perm.get("audit"), "permissions.audit", problems)
    if "checkpoint_enabled" in raw_perm:
        perm_cfg.checkpoint_enabled = _check_bool(
            raw_perm.get("checkpoint_enabled"), "permissions.checkpoint_enabled", problems
        )
    if "checkpoint_keep" in raw_perm:
        perm_cfg.checkpoint_keep = _check_int(
            raw_perm.get("checkpoint_keep"), "permissions.checkpoint_keep", 1, 200, problems
        )
    if perm_cfg.ask_above_level > perm_cfg.level:
        # 不静默改写用户意图：硬拒绝阈值高于授予等级意味着「越级即询问」，
        # 这是合法配置（放宽），但容易与「收紧」搞混，明确提示一次。
        problems.append(
            f"permissions.ask_above_level（L{perm_cfg.ask_above_level}）高于 "
            f"level（L{perm_cfg.level}）：越级操作将走「询问」而非「硬拒绝」"
        )

    result = AppConfig(
        api=api_cfg,
        agent=agent_cfg,
        permissions=perm_cfg,
        unknown_api_keys=api_unknown,
        unknown_agent_keys=agent_unknown,
        unknown_permissions_keys=perm_unknown,
    )

    # ---- 日志输出 ----
    if problems:
        for p in problems:
            _log.warning(f"配置校验：{p}")
        _log.info(f"配置校验完成，发现 {len(problems)} 个问题，已回退安全默认值")
    else:
        _log.debug("配置校验通过")

    result.problems = problems
    return result


def log_validation_report(result: AppConfig) -> None:
    """在启动时输出配置校验报告。"""
    unknown = (
        result.unknown_api_keys
        + result.unknown_agent_keys
        + result.unknown_permissions_keys
    )
    if unknown:
        _log.info(f"配置中包含未识别字段：{', '.join(unknown)}（将被忽略）")
