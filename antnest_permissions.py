# -*- coding: utf-8 -*-
"""AntNest · Worker 权限模型（L0–L5）

把「工蚁能看什么、能写什么、能执行什么、能不能联网、能不能改 AntNest 自己」
从散落在 6 个调用点的 if 判定，收敛成一个可声明、可审计、可询问的决策函数。

等级
----
    L0 READ        只读（view_file / list_dir / grep_files）
    L1 WRITE       工作区写入（write_file / search_replace）
    L2 EXECUTE     执行代码与命令（spawn_clone / run_cli / run_python）
    L3 NETWORK     出站网络（web_fetch / mcp_call）
    L4 SYSTEM      系统级变更（危险命令模式命中、管理员操作）
    L5 SELF_MOD    修改 AntNest 自身正在运行的源码

三态而非二态
------------
    ALLOW  直接放行
    ASK    交给用户确认（Allow Once / Allow Task / Deny）
    DENY   硬拒绝

路线图 §10 明确要求「Worker requests: [Allow Once] [Allow Task] [Deny]」，
二态表达不了「先问我」。

判定顺序（先特判，后分级）
--------------------------
    unknown tool                → DENY     fail-closed，忘登记的工具不会静默放行
    explicit_confirm            → ASK      自源码 / 危险命令 / L4，永不 ALLOW
    required <= level           → ALLOW
    required >  ask_above_level → DENY
    otherwise                   → ASK

`explicit_confirm` 排在分级之前是刻意的：否则 `level = 5`（最自然的“全开”配置）
会因为 `SELF_MOD(5) <= 5` 而直接 ALLOW，把「改自身源码永远需要人明确同意」这个
不变量绕过去。

安全边界（重要，别读错了）
--------------------------
本模块是**权限声明与拦截**，**不是**强 Sandbox。命令串上的危险模式正则只是一个
*提示*，不是控制：一段看起来无害的 Python/PowerShell 依然可以做任何事
（`python x.py` 里藏 `shutil.rmtree` 不会被本模块拦到）。

真正的强隔离需要 Job Object / WSL2 / AppContainer，属 v1.5 范围。
现有防线保持不变且继续有效：独立进程 + 临时目录 + 超时 + 进程树清理 +
工蚁侧 `dangerous_patterns`（第二道红线，见硬约束 C1）。

导入约束
--------
本模块**只依赖标准库**。它会被 `antnest_registry` 导入，而后者由
`antnest_queen` 拉起；`antnest_config` 在半初始化状态下就会走到那条路。
绝不能在这里 import `antnest_config`。
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path


class _Unset:
    """哨兵：区分「未传 default」与「显式传 None」。"""

    __slots__ = ()


_UNSET = _Unset()


class PermLevel(IntEnum):
    """权限等级。数值可比较，L0 最低 L5 最高。"""

    READ = 0
    WRITE = 1
    EXECUTE = 2
    NETWORK = 3
    SYSTEM = 4
    SELF_MOD = 5

    @property
    def label(self) -> str:
        return _LEVEL_LABEL[self]

    @classmethod
    def parse(
        cls,
        value: object,
        default: "PermLevel | None" = _UNSET,  # type: ignore[assignment]
    ) -> "PermLevel | None":
        """宽松解析等级值。

        接受 0–5 的整数、"L3"/"l3"、以及语义名（network / workspace_write …）。
        无法识别时返回 `default`；未显式传入 default 时回退**最保守的 READ**
        （fail-closed）。调用方若想区分「解析失败」与「恰好是 READ」，
        传一个显式的 default（例如 config 校验传 NETWORK）。
        """
        fallback = cls.READ if isinstance(default, _Unset) else default
        if isinstance(value, PermLevel):
            return value
        if isinstance(value, bool):
            return fallback
        if isinstance(value, int):
            try:
                return cls(value) if 0 <= value <= cls.SELF_MOD else fallback
            except ValueError:  # IntEnum：越界整数
                return fallback
        text = str(value or "").strip().upper()
        if text.startswith("L") and text[1:].isdigit():
            text = text[1:]
        if text.isdigit():
            n = int(text)
            try:
                return cls(n) if 0 <= n <= cls.SELF_MOD else fallback
            except ValueError:
                return fallback
        return _LEVEL_BY_NAME.get(text, fallback)


_LEVEL_LABEL: dict[PermLevel, str] = {
    PermLevel.READ: "L0 只读",
    PermLevel.WRITE: "L1 工作区写入",
    PermLevel.EXECUTE: "L2 执行代码/命令",
    PermLevel.NETWORK: "L3 出站网络",
    PermLevel.SYSTEM: "L4 系统变更",
    PermLevel.SELF_MOD: "L5 修改 AntNest 自身",
}

_LEVEL_BY_NAME: dict[str, PermLevel] = {
    "READ": PermLevel.READ, "READ_ONLY": PermLevel.READ,
    "WRITE": PermLevel.WRITE, "WORKSPACE_WRITE": PermLevel.WRITE,
    "EXECUTE": PermLevel.EXECUTE, "EXEC": PermLevel.EXECUTE,
    "NETWORK": PermLevel.NETWORK, "NET": PermLevel.NETWORK,
    "SYSTEM": PermLevel.SYSTEM,
    "SELF_MOD": PermLevel.SELF_MOD, "SELF": PermLevel.SELF_MOD,
    "SELF_MODIFICATION": PermLevel.SELF_MOD,
}


class Action(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

    @property
    def allowed(self) -> bool:
        return self is Action.ALLOW

    @property
    def blocked(self) -> bool:
        return self is Action.DENY


@dataclass(frozen=True)
class Decision:
    """一次权限判定的结果。"""

    action: Action
    level: PermLevel
    reason: str = ""
    code: str = ""
    scope: str = ""
    tool: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.action is Action.ALLOW

    @property
    def blocked(self) -> bool:
        return self.action is Action.DENY

    @property
    def needs_approval(self) -> bool:
        return self.action is Action.ASK

    @property
    def status(self) -> str:
        """工具结果里的 status 字段取值。

        `denied` 是新增词汇：antnest_loop 之前只把
        error/blocked/approval_required 当作非 ok，新增的 denied 若不登记会被
        记成 ok（见 docs/v1.4-DESIGN.md §7.6）。
        """
        if self.action is Action.ALLOW:
            return "ok"
        if self.action is Action.ASK:
            return "approval_required"
        return "denied"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "scope": self.scope,
            "tool": self.tool,
            "level": int(self.level),
            "level_label": self.level.label,
            "action": self.action.value,
            "reason": self.reason,
            "code": self.code,
            **({"detail": self.detail} if self.detail else {}),
        }

    def to_tool_result(self, message: str = "") -> str:
        """序列化成工具结果 JSON，供 loop 直接回灌给模型。"""
        import json

        payload = self.to_dict()
        payload["error"] = message or self.reason or self.level.label
        payload["message"] = (
            message
            or f"该操作需要「{self.level.label}」权限（当前授予 {self.level_label_of_grant()}），"
               f"已拦截。请说明目的并请求用户授权，或改用更低权限的方式完成。"
        )
        return json.dumps(payload, ensure_ascii=False)

    def level_label_of_grant(self) -> str:
        return str(self.detail.get("granted_label", ""))


# ====================== 危险命令模式（唯一主表） ======================
#
# 原先散在三处且语义不同：
#   - antnest_queen._DANGER_CLI_PATTERNS   （拦截）
#   - admin_utils.DANGEROUS_COMMANDS        （仅用于展示分类/风险等级）
#   - antnest_clone_worker.dangerous_patterns（工蚁侧拦截，第二道红线）
#
# 本表是「蚁后侧」的唯一主表，吸收 admin_utils 的分类语义。
# antnest_clone_worker 那张表**保留不动**——它是工蚁进程内的第二道防线，
# 且受硬约束 C1 限制（不能 import 业务模块）。这层冗余是正确的防御纵深。
#
# 范围刻意保守：只拦系统级不可逆操作。项目内相对路径
# （rm -rf node_modules）必须放行，否则 Agent 没法正常干活。
DANGER_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+/(?!/)", "递归删除根/绝对路径"),
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+[a-zA-Z]:[\\/]", "递归删除整个盘符"),
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+~", "递归删除用户主目录"),
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+\.\.(?:[\\/]|\s|$)", "递归删除上级目录"),
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+\.(?:\s|$)", "递归删除当前目录"),
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+\*", "通配递归删除"),
    (r"\brm\s+-(?:rf|fr|r\s*-f|f\s*-r|r|R)\s+[$]HOME", "递归删除 HOME"),
    (r"\bdel\s+/[sqf]+\s+[a-zA-Z]:[\\/]", "强制删除系统盘"),
    (r"\brd\s+/[sq]+\s+[a-zA-Z]:[\\/]", "删除系统盘目录"),
    (r"\bformat\s+[a-zA-Z]:", "格式化磁盘"),
    (r"\bshutdown\b", "关机/重启"),
    (r"\bhalt\b", "关机"),
    (r"\bmkfs\b", "格式化文件系统"),
    (r"\bdd\s+if=/dev/zero\b", "危险磁盘写入"),
    (r"\bRemove-Item\b[^\n]*(?:-Recurse|-Force|-r\b)", "PowerShell 递归/强制删除"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\};:", "fork bomb"),
)

# 命令里出现「写核心源码」信号时，才去比对核心文件名。
# 与 antnest_config._command_self_source_target 的启发式保持一致（单一真源在
# 那边；这里只复用「命中什么算写信号」的判定，不重复维护文件名表）。
_WRITE_SIGNALS = (
    re.compile(r"\.write_(?:text|bytes)\s*\("),
    re.compile(r"\bset-content\b|\bout-file\b|\badd-content\b"),
    re.compile(r"\b(?:sed|perl)\s+-i\b|\btee\b"),
    re.compile(r"\b(?:rm|del|remove-item|unlink|mv|move)\b"),
    re.compile(r"\b(?:os|pathlib)\.(?:remove|replace|rename|unlink)\s*\("),
    re.compile(r"\bshutil\.(?:copy|copy2|move|rmtree)\s*\("),
    re.compile(r"open\s*\([^\n)]*,\s*[\"'](?:w|a|x|r\+|w\+|a\+)"),
    re.compile(r"(?:^|[^>])>{1,2}(?!=)"),
)


def match_danger(command: str) -> tuple[bool, str]:
    """命令是否命中危险模式。返回 (是否危险, 原因)。"""
    text = str(command or "")
    if not text:
        return False, ""
    for pattern, why in DANGER_PATTERNS:
        if re.search(pattern, text):
            return True, why
    return False, ""


def has_write_signal(command: str) -> bool:
    """命令是否含有「写入/删除」信号（用于判断是否需要比对核心文件名）。"""
    text = str(command or "")
    lower = text.lower()
    return any(p.search(lower) or p.search(text) for p in _WRITE_SIGNALS)


@dataclass
class PermissionsConfig:
    """`config.json → permissions` 段的运行时形态。

    默认值刻意复刻 v1.3.1 的实际行为（见 docs/v1.4-DESIGN.md §5.4 逐项核对）：
    读写执行联网全放行，危险命令与自身源码需确认。
    """

    level: PermLevel = PermLevel.NETWORK          # 3
    ask_above_level: PermLevel = PermLevel.NETWORK  # 3
    audit: bool = True
    checkpoint_enabled: bool = True
    checkpoint_keep: int = 20
    unknown_keys: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return (
            f"L{self.level} {self.level.label}"
            + (f"（>L{self.ask_above_level} 直接拒绝）" if self.ask_above_level < self.level else "")
            + f"，自源码与 L4 始终需确认；审计 {'开' if self.audit else '关'}"
        )


@dataclass(frozen=True)
class PermissionPolicy:
    """一次判定所需的全部输入（便于测试时构造，不依赖全局配置）。"""

    level: PermLevel = PermLevel.NETWORK
    ask_above_level: PermLevel = PermLevel.NETWORK
    project_dir: str = ""
    self_source_names: frozenset[str] = frozenset()
    self_source_root: str = ""
    # 外部注入的判定钩子（避免本模块 import antnest_config，见模块 docstring）
    self_mod_approved_cb: object = None   # Callable[[], bool] | None
    danger_match: object = None           # Callable[[str], tuple[bool, str]] | None

    def self_mod_approved(self) -> bool:
        cb = self.self_mod_approved_cb
        if callable(cb):
            try:
                return bool(cb())
            except Exception:
                return False
        return False


def decide(
    required: PermLevel,
    *,
    level: PermLevel,
    ask_above_level: PermLevel,
    explicit_confirm: bool = False,
    tool: str = "",
    scope: str = "",
    reason: str = "",
    code: str = "",
    detail: dict | None = None,
    unknown: bool = False,
) -> Decision:
    """核心判定。顺序即语义，改动前先读模块 docstring。

    `explicit_confirm` 必须排在分级之前——否则 level=SELF_MOD 会让
    「改自身源码需确认」彻底失效。
    """
    payload = dict(detail or {})
    payload.setdefault("granted_label", f"L{level} {level.label}")

    if unknown:
        return Decision(
            Action.DENY, required,
            reason=reason or f"工具未在注册表登记，按 fail-closed 拒绝（需要 {required.label}）",
            code=code or "PERM-UNKNOWN-TOOL", tool=tool, scope=scope, detail=payload,
        )
    if explicit_confirm:
        return Decision(
            Action.ASK, required,
            reason=reason or f"{required.label}：{_confirm_hint(required)}",
            code=code or f"PERM-{required.name}", tool=tool, scope=scope, detail=payload,
        )
    if required <= level:
        return Decision(
            Action.ALLOW, required,
            reason=reason or f"已授予 L{level}，满足所需 {required.label}",
            code=code or "PERM-OK", tool=tool, scope=scope, detail=payload,
        )
    if required > ask_above_level:
        return Decision(
            Action.DENY, required,
            reason=reason or (
                f"需要 {required.label}，仅授予 L{level}"
                f"（硬拒绝阈值 L{ask_above_level}）"
            ),
            code=code or "PERM-OVER-LEVEL", tool=tool, scope=scope, detail=payload,
        )
    return Decision(
        Action.ASK, required,
        reason=reason or f"需要 {required.label}，当前仅授予 L{level}，请用户确认",
        code=code or "PERM-NEEDS-APPROVAL", tool=tool, scope=scope, detail=payload,
    )


def _confirm_hint(required: PermLevel) -> str:
    if required is PermLevel.SELF_MOD:
        return "正在修改 AntNest 自身运行的源码，必须由用户明确同意"
    if required is PermLevel.SYSTEM:
        return "命中系统级危险操作，必须由用户明确确认"
    return "该操作需要用户明确确认"


class PermissionEngine:
    """有状态的权限引擎：持有配置 + 本回合/本任务的一次性授权。

    授权 key 按**具体作用域**签发（`write:antnest_config.py`、`cmd:rm -rf /`），
    不按工具签发——批准改一个文件不等于批准改整个仓库。
    """

    def __init__(self, config: PermissionsConfig | None = None, policy: PermissionPolicy | None = None) -> None:
        self.config = config or PermissionsConfig()
        self.policy = policy or PermissionPolicy()
        self._once: set[str] = set()
        self._task: set[str] = set()
        self._lock = threading.RLock()

    # ---------------- 授权生命周期 ----------------

    def grant_once(self, key: str) -> None:
        with self._lock:
            self._once.add(str(key))

    def grant_task(self, key: str) -> None:
        with self._lock:
            self._task.add(str(key))

    def reset_turn(self) -> None:
        """回合边界：一次性授权失效（任务级授权保留）。"""
        with self._lock:
            self._once.clear()

    def reset_task(self) -> None:
        """任务边界：全部授权失效。"""
        with self._lock:
            self._once.clear()
            self._task.clear()

    def _granted(self, key: str) -> bool:
        with self._lock:
            return key in self._once or key in self._task

    def grants(self) -> dict[str, list[str]]:
        with self._lock:
            return {"once": sorted(self._once), "task": sorted(self._task)}

    # ---------------- 判定入口 ----------------

    def check_level(
        self,
        required: object,
        *,
        tool: str = "",
        scope: str = "",
        key: str = "",
        reason: str = "",
        code: str = "",
    ) -> Decision:
        """纯等级判定（不含参数敏感性）。`required` 接受 int / "L3" / PermLevel。"""
        need = PermLevel.parse(required, PermLevel.READ)
        if key and self._granted(key):
            # 已被显式授权：显式授权优先于等级。
            return Decision(
                Action.ALLOW, need,
                reason=f"用户已授权该作用域（{key}）",
                code="PERM-GRANTED", tool=tool, scope=scope,
                detail={"granted_label": f"L{self.policy.level} {self.policy.level.label}",
                        "grant_key": key},
            )
        return decide(
            need,
            level=self.policy.level,
            ask_above_level=self.policy.ask_above_level,
            tool=tool, scope=scope, reason=reason, code=code,
        )

    def check_write_path(self, path: str, *, tool: str = "") -> Decision:
        """写路径判定。**参数敏感**：路径落在核心源码上时必须 ASK，
        无论 level 授予到多少（硬约束 C2）。

        例外：用户已明确批准过本次自源码修改（`self_mod_approved_cb`）时降级为普通
        写入判定——这与 `antnest_config._self_modification_gate` 在已批准时返回空串
        的既有行为一致。批准只覆盖自源码这一类，**不覆盖**危险命令。
        """
        key = f"write:{_norm(path)}"
        if self._granted(key):
            return Decision(
                Action.ALLOW, PermLevel.SELF_MOD,
                reason="用户已授权修改该核心文件", code="PERM-GRANTED",
                tool=tool, scope="self_source", detail={"grant_key": key},
            )
        if self._is_self_source(path):
            if self.policy.self_mod_approved():
                return self.check_level(
                    PermLevel.WRITE, tool=tool, scope="workspace",
                    code="PERM-WRITE",
                    reason="用户已明确批准本次自身源码修改",
                )
            return decide(
                PermLevel.SELF_MOD,
                level=self.policy.level,
                ask_above_level=self.policy.ask_above_level,
                explicit_confirm=True,
                tool=tool, scope="self_source",
                reason=(
                    f"{_display(path)} 是 AntNest 正在运行的核心源码，"
                    "必须由用户明确同意后才能修改"
                ),
                code="PERM-SELF-SOURCE",
            )
        return self.check_level(
            PermLevel.WRITE, tool=tool, scope="workspace",
            code="PERM-WRITE",
        )

    def check_command(self, command: str, *, tool: str = "") -> Decision:
        """命令/代码判定。**参数敏感**：命中危险模式 → ASK（永不 ALLOW）。"""
        danger, why = self._danger(command)
        if danger:
            return decide(
                PermLevel.SYSTEM,
                level=self.policy.level,
                ask_above_level=self.policy.ask_above_level,
                explicit_confirm=True,
                tool=tool, scope="danger_command",
                reason=f"命中高危命令模式（{why}），必须由用户确认",
                code="PERM-DANGER-COMMAND",
                detail={"pattern_reason": why},
            )
        target = self._self_source_target(command)
        if target is not None:
            if self.policy.self_mod_approved():
                return self.check_level(
                    PermLevel.EXECUTE, tool=tool, scope="command", code="PERM-EXECUTE",
                    reason="用户已明确批准本次自身源码修改",
                )
            return decide(
                PermLevel.SELF_MOD,
                level=self.policy.level,
                ask_above_level=self.policy.ask_above_level,
                explicit_confirm=True,
                tool=tool, scope="self_source",
                reason=f"命令试图修改 AntNest 核心源码（{target}），必须由用户明确同意",
                code="PERM-SELF-SOURCE",
                detail={"target": target},
            )
        return self.check_level(
            PermLevel.EXECUTE, tool=tool, scope="command", code="PERM-EXECUTE",
        )

    def check_tool(
        self,
        tool: str,
        args: dict | None = None,
        *,
        required: PermLevel | None = None,
        registered: bool = True,
    ) -> Decision:
        """工具级判定入口。

        **参数敏感判定优先于静态等级**（硬约束 C2）：一个 `write_file` 的静态
        等级是 L1，但如果 path 指向核心源码，结论必须是 ASK 而不是 ALLOW。
        """
        args = dict(args or {})
        if not registered:
            return decide(
                required if required is not None else PermLevel.EXECUTE,
                level=self.policy.level, ask_above_level=self.policy.ask_above_level,
                unknown=True, tool=tool, scope="unknown",
            )
        # 1) 先跑参数敏感检查
        for key in ("path", "file", "target"):
            if key in args and required in (None, PermLevel.WRITE) and _looks_like_write(tool):
                return self.check_write_path(str(args[key]), tool=tool)
        for key in ("command", "code"):
            if key in args and _looks_like_exec(tool):
                return self.check_command(str(args[key]), tool=tool)
        # 2) 再退回静态等级
        if required is None:
            required = PermLevel.READ
        return self.check_level(required, tool=tool, scope="tool", key=f"tool:{tool}")

    # ---------------- 内部 ----------------

    def _danger(self, command: str) -> tuple[bool, str]:
        hook = self.policy.danger_match
        if callable(hook):
            try:
                res = hook(command)
                if isinstance(res, tuple) and len(res) == 2:
                    return bool(res[0]), str(res[1])
            except Exception:
                pass
        return match_danger(command)

    def _is_self_source(self, path: str) -> bool:
        names = self.policy.self_source_names
        if not names:
            return False
        root = self.policy.self_source_root
        try:
            target = Path(path).resolve()
        except (OSError, ValueError):
            return False
        if root:
            try:
                rel = os.path.normcase(os.path.normpath(os.path.relpath(target, Path(root).resolve())))
            except ValueError:
                return False
            return rel in {os.path.normcase(os.path.normpath(n)) for n in names}
        return os.path.normcase(target.name) in {os.path.normcase(n) for n in names}

    def _self_source_target(self, command: str) -> str | None:
        """命令是否试图改核心源码。有写信号才比对文件名，避免只读命令误报。"""
        names = self.policy.self_source_names
        if not names or not has_write_signal(command):
            return None
        lower = str(command or "").lower()
        for name in names:
            if name.lower() in lower:
                return name
        return None


# ====================== 展示辅助（UI / 审计复用） ======================

def describe_grants(level: PermLevel) -> list[dict]:
    """按当前 level 列出每个等级的能力与是否放行，供 UI 渲染徽章。"""
    out: list[dict] = []
    for lv in PermLevel:
        if lv is PermLevel.SELF_MOD:
            granted, note = False, "始终需明确确认"
        elif lv is PermLevel.SYSTEM:
            granted, note = False, "命中危险命令时需确认"
        else:
            granted = lv <= level
            note = "已授予" if granted else "未授予"
        out.append({
            "level": int(lv),
            "name": lv.name,
            "label": lv.label,
            "granted": granted,
            "note": note,
        })
    return out


def _norm(path: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(str(Path(path).expanduser().resolve())))
    except (OSError, ValueError):
        return os.path.normcase(os.path.normpath(str(path)))


def _display(path: str) -> str:
    text = str(path)
    return text if len(text) <= 120 else text[:117] + "..."


def _looks_like_write(tool: str) -> bool:
    return any(k in tool for k in ("write", "replace", "edit", "patch", "delete", "remove", "move", "rename"))


def _looks_like_exec(tool: str) -> bool:
    return any(k in tool for k in ("cli", "exec", "python", "spawn", "run", "shell", "bash", "script"))


_default_engine: PermissionEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> PermissionEngine:
    """进程级默认引擎。

    配置在 antnest_config import 期通过 `configure()` 注入；未注入时用向后兼容
    的默认档（level=3）。
    """
    global _default_engine
    with _engine_lock:
        if _default_engine is None:
            _default_engine = PermissionEngine()
        return _default_engine


def configure(config: PermissionsConfig, policy: PermissionPolicy) -> PermissionEngine:
    """由 antnest_config 在 import 期调用，注入真实配置。"""
    global _default_engine
    engine = PermissionEngine(config, policy)
    with _engine_lock:
        _default_engine = engine
    return engine
