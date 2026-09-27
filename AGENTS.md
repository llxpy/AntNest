# AGENTS.md — AntNest 开发约束

> 所有参与 AntNest 开发/运行的 Agent 必须遵守以下约束。
> v1.4 起，本文件里的「硬约束」都有测试守着（`tests/test_worker_e2e.py`、
> `tests/test_permissions.py`、`tests/test_ui_wiring.py`）。**改动前先看对应测试。**

---

## 0. 硬约束（违反会炸，且已有回归测试）

### H1. `antnest_clone_worker.py` 必须是叶子模块

`antnest_config.py` 的 import 顺序是致命的：

```
antnest_config.py:25   import antnest_clone_worker   ← 先
antnest_config.py:33   import antnest_log             ← 后
antnest_config.py:47   run_if_clone_mode() → sys.exit(0)
```

`antnest_clone_worker` 在第 25 行被 import 时，`antnest_config` 处于**半初始化**
状态（只绑定了 ≤25 行的名字）。因此：

> **`antnest_clone_worker.py` 只允许 import `antnest_log` 与标准库。
> 任何传递依赖到 `antnest_config` 的改动都会在 import 期循环导入，杀死所有工蚁。**

工蚁侧策略只读环境变量，**不要**在工蚁模块里 import 业务模块。

守护测试：`WorkerLeafModuleTest`（静态扫描传递 import 闭包）

### H2. 模块清单从 import 图派生，不得手写

工蚁在隔离目录以独立进程运行，只能依赖被**显式复制**过去的文件。手写清单会腐烂。

> **新增任何顶层 `antnest_*.py` 模块后，`antnest_inventory` 会自动纳入；
> 但 `pyproject.toml` / `installer/AntNest.iss` 需要同步**
> （由 `PackagingManifestTest` 在 CI 卡住，不一致即失败）。

工具函数：`python -m antnest_inventory`（打印闭包 + 两份清单的差异）

守护测试：`test_worker_e2e.py::WorkerDependencyTest` + `PackagingManifestTest`

### H3. 端到端路径必须有测试

v1.3.1 有三个严重缺陷长期没被发现，原因都是**关键路径无测试覆盖**：

- 没有任何测试真正启动过工蚁进程（`test_spawn_clone` mock 了 `Popen`）
- 没有任何测试驱动过 `agent_single_loop` 的工具派发路径
- 「所有工具调用全部失败」持续了整整一个版本

> **改动工具派发、权限闸门、计划派生、事件发射、检查点保存时，
> 必须有测试真正跑一遍 `agent_single_loop`（用 `mock.patch.object(AntNest,
> "llm_chat_stream", ...)` 即可，不需要网络）。**

参考实现：`tests/test_permission_gate.py`、`tests/test_event_wiring.py`

### H4. 权限闸门的两个不可让步点

见 §3。

---

## 1. 能力检测是启动流程的一部分（非可选）

- 进程内首次会话前调用 `AntNest._ensure_model_cap()`：按 `/models` 实测校准
  `TOKEN_CAP`，并把「当前模型能力」画像注入 `SYSTEM_PROMPT`。
- 检测必须**非致命**：网络失败 / 超时 / HTTP 401 一律降级为默认画像，
  禁止 `sys.exit(1)` 阻塞启动（历史版本在 401 时退出进程，已修复）。
- 缓存遵循 `model_capabilities`（TTL 600s，`(base, model)` 维度），避免重复探测。

> ⚠️ `antnest_config.py:118` 目前仍在 import 期 `sys.exit(1)`（无 API Key 时）。
> 这与本节「禁止 sys.exit(1) 阻塞启动」冲突，也是遗留 R8。
> `evals/harness._bootstrap_env` 靠提前设 `ANT_API_KEY` 绕开。

## 2. 开关放在配置区

- 唯一开关：`config.json → api.skip_model_check`（或环境变量
  `ANT_SKIP_MODEL_CHECK`）。UI 设置页可同步该字段（设置 → API → 跳过模型检测）。
- 置 `true`：跳过一切主动探测与校准，使用 `DEFAULT_TOKEN_CAP`。
- 权限：`config.json → permissions.level`（0–5）。默认 `3` 复刻 v1.3.1 行为；
  `0` = 只读审阅模式，`2` = 禁网络。
- 检查点：`permissions.checkpoint_enabled`（或 `ANT_CHECKPOINT=0` 关闭）。
- 事件：`ANT_EVENTS=0` 关闭。

## 3. 权限模型（v1.4）

模块：`antnest_permissions`。唯一闸门在 `antnest_loop.check_permission()`。

**两个顺序是语义，改动前先读模块 docstring：**

1. **`explicit_confirm` 必须在等级判定之前**。否则 `level=5` 会让
   `SELF_MOD(5) <= 5` 直接 ALLOW，「改自身源码永远需人确认」这个不变量被绕过。
2. **参数敏感判定必须覆盖静态等级**。`write_file` 静态等级是 L1，但
   `path` 指向核心源码时结论必须是 ASK 而非 ALLOW。

**未知工具 fail-closed DENY**（漏登记的新工具不会静默放行）。

**授权按作用域签发**（`write:<path>`、`cmd:<command>`），不按工具——
批准改一个文件不等于批准改整个仓库。

> ⚠️ 权限拦截**不是** Sandbox。危险模式是正则提示不是控制：
> `python x.py` 里藏 `shutil.rmtree` 拦不到。文档与 UI 措辞不得夸大。

守护测试：`tests/test_permissions.py`、`tests/test_permission_gate.py`

## 4. 工具注册表（v1.4）

`antnest_registry.TOOL_SPECS` 是**唯一**硬编码清单。执行器表、暴露给 LLM 的
schema 列表、畸形调用正则、prompt 目录全部从它派生。

改动规则：

- 新增工具 → 只改 `TOOL_SPECS` + `antnest_schemas`（加 schema）
- **不要**在任何地方手写工具名枚举
- 工具数组的**顺序**影响模型行为：v1.3.1 那 11 个的相对顺序不得变动，
  新增工具只能追加
- 畸形调用正则覆盖 `TOOL_SPECS` **全集**（不是 exposed 子集）——
  `leave_memory_hints` 只在压缩态暴露，收窄就能被伪造绕过
- prompt 工具清单由 `QueenPromptFileTest` 在 CI 卡住一致性

守护测试：`tests/test_registry.py`

## 5. 事件日志（v1.4）

`antnest_events`。三条不可让步的约束：

1. **fail-open**，但首次写失败必须打 WARNING（否则静默丢弃与正常无记录无法区分）
2. **绝不落盘推理内容 / 凭据**（`reasoning_content`、`api_key` 等 redact；
   `total_tokens` 等指标白名单放行）
3. 落 **`PROJECT_ANT_DIR`**，不是 `ANT_HOME`（后者在安装版只读，记录会被静默丢弃）

`events/*.jsonl` = 可重放；`audit.log` = 仅安全事件。**不要双写。**

守护测试：`tests/test_events.py`

## 6. 计划 DAG（v1.4）

`antnest_plan`。双数据源，缺一不可：

- `update_plan` 工具（LLM 显式规划）
- `spawn_clone` 的自动派生（flash 类模型不按格式调工具时兜底）

规则：

- **权限闸门必须排在计划派生之前**。反过来的话，被拒绝的 spawn 也会留下节点，
  但那件事根本没发生，计划会说谎
- 环必须**整体拒绝**并保留原计划（半个新计划比旧计划更糟）
- `PlanNode` 是多字段读取，UI 线程并发读会渲染出「✓ 但结果为空」——
  所有变更在 `antnest_events.STATE_LOCK` 内

守护测试：`tests/test_plan.py`、`tests/test_plan_wiring.py`

## 7. 检查点（v1.4）

`antnest_checkpoint`。**核心是 `rehydrate()`，不是切片。**

朴素 `messages[-N:]` 会产出被 API 拒绝的列表：切点落在工具批次中间会让列表
以 `role:"tool"` 开头；既有回填（只扫最后一个 assistant、只补缺失响应、
然后 break）救不回来；system prompt 会整个丢失（安全与架构规则全没）；
`reasoning_content` 会被明文持久化。

规则：

- 快照必须在**工具批次闭合的静止点**取（`antnest_loop` 的 while 顶部），
  **不能**从工具内部调——那时 messages 正在被追加，快照是撕裂的
- 落盘前剥离思维链字段
- `list()` 按数值排序，不靠文件名字符串序（seq 破千后会错）

守护测试：`tests/test_checkpoint.py`

## 8. 评测（v1.4）

`evals/`。**必须先说清楚：v1.4 评测测的是运行时管线，不是模型能力。**
`Report.model_dependent` 是显式字段（默认 `false`）。

- 断言键拼错会**报错**而不是静默通过（假绿比没评测更危险）
- `tools_not_called` 断言的是**执行器未被调用**，不是「没有 tool 消息」——
  权限闸门短路时仍会写 tool 消息
- 评测 chdir 到临时目录，所以用例里涉及仓库路径的必须用 `{ROOT}` 占位
- 跨用例状态必须隔离（`IsolationTest` 跑两遍比结果）

新增安全相关行为时，同步加一个 eval 用例，并考虑做一次**变异测试**
（故意破坏 → 确认对应用例失败 → 还原）。

## 9. 双命名空间（D7，已知技术债，遗留 R9）

本项目是「共享可变全局 + 延迟 `_A()` 访问」的架构。`from x import *` 是
**值拷贝**，所以 `messages` / `AGENT_CANCEL` / `UI_STREAM_CB` 等同时存在于
`AntNest` 与子模块两个命名空间，改一处必须同步另一处。

```python
import AntNest as _m
_m.UI_STREAM_CB = cb          # 壳
antnest_config.UI_STREAM_CB = cb   # 模块级（_stream_emit 读的是这个）
```

**工具函数不应走 `_A()` 路径**——`antnest_log.get_audit` 曾在 v1.3.1 被写成
`_A().get_audit()`，而壳从未 re-export 它，导致**每个工具调用都抛
AttributeError**。新代码直接 `import` 模块用模块级函数。

## 10. 代码风格

- 注释解释**为什么**，不解释「做了什么」；每个非显然的设计决定都要写清
  被否掉的方案和原因（这是 v1.4 设计评审最大的收获）
- 中文用户可见文案用中文；标识符/文档字符串可中英混用
- 零外部运行时依赖（仅 `pywebview`）；新模块只用标准库
- 不要在模块顶层做 I/O / 起线程 / 查单实例
