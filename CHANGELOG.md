# 更新日志

## v1.4 (进行中) — Reliability

设计文档：[`docs/v1.4-DESIGN.md`](docs/v1.4-DESIGN.md)（v2 评审后修订）
每个步骤完成后在此追加 *本步解决了什么 / 还有什么痛点*。

---

### v1.4.1 — 可观测性 + exe 启动入口重建

设计文档：[`docs/v1.4.1-DESIGN.md`](docs/v1.4.1-DESIGN.md)、
[`docs/v1.4.1-BOOT-DESIGN.md`](docs/v1.4.1-BOOT-DESIGN.md)（均为评审后修订版）

#### 步 0 — 面板头按钮整宽拉伸

- **解决**：截图里的「轨迹」按钮渲染成整宽方块，像一个文本框；子任务/工蚁的「展开」按钮同样受影响。根因不是缺 CSS，而是 Python 把按钮写成 `h2` 的兄弟节点，被列向 flex 的 `align-items:stretch` 拉满——而 `.card h2` 本身就是 `display:flex`，本意就是标题与按钮同行。修正结构而非补 CSS。
- **顺带**：`v1.4` 引入的 `.btn-expand` / `.card-h2` 在 `app.css` 里**零规则**（死类），所以这个错误一直没被发现。新增通用死类扫描：覆盖两个组件类产地（`ui_render.py` 26 个 + `prototype_antnest.py` 3 个）逐个断言有 CSS 规则，钩子类需显式登记，扫描面缩小时测试自己会失败。
- **遗留**：无。

#### 步 1 — 捕获 `finish_reason`（失败归因的数据源）

- **解决**：`llm_chat_stream` 全程不捕获 `finish_reason`，于是「回复被 token 上限截断」「被内容策略拦截」「服务端资源不足」三类全部塌缩成同一句「本轮没有文本回复」。现在三元组返回，`agent_single_loop` 新增 `LoopResult` 显式回传结局（7 种：`replied`/`empty`/`max_rounds`/`cancelled`/`denied`/`approval`/`error`）+ 逐轮 `finish_reasons` + 重试告警原文。
- **关键坑**：`finish_reason` 挂在 `choices[0]` 而非 `delta`，而流式协议的终止块是 `{"delta": {}, "finish_reason": "stop"}`——`delta` 为空会被 `if not delta: continue` 短路掉。**读取点写错则测试全绿而覆盖为零**。已加 SSE 字节流 fixture（含终止块）与源码顺序断言，并用变异测试确认守得住（破坏后 7 个用例失败）。
- **顺带**：「回复为空」的重试告警原先只喂回给模型、用户一个字都看不到，现收进 `LoopResult.retry_notes`。
- **遗留**：`diagnose_turn()` 归因与 `AntNestError.user_msg` 上屏属下一步。

#### 步 2 — exe 启动入口重建

- **解决**：`launcher.ps1`（`AntNest.exe` 的唯一源码，开始菜单与桌面快捷方式都指向它）有四类真问题：
  1. `$ErrorActionPreference="Stop"` 却**没有顶层 try/catch**，而 ps2exe `-noConsole` 下没有控制台可打印——`Add-Type` / `Start-Process`（AV 隔离 `uv.exe` 时）等抛出的终止性错误全部静默。
  2. `WaitForExit()` 之后**不检查退出码**：`uv run` 同步失败、`pywebview` import 失败、venv 损坏——应用起不来时屏幕上什么都没有。
  3. WebView2 探测每次双击都 `Get-ChildItem -Recurse` 重扫一遍，无 marker 缓存。改为**注册表优先**（Edge Update 维护，~1ms），递归扫描退为兜底。
  4. `$app` 靠 `MainModule.FileName` 推导，未编译运行时等于 `powershell.exe` → `$app` 变成 `System32`。改用 `$PSScriptRoot` 优先。
- **失败汇报不新增捕获机制**：子进程非零退出时读应用**本来就在写**的 `antnest.log` / `ui_trace.log` 尾部，弹窗给出「退出码 + 真实错误原文 + 日志路径」。不重定向 stdout 是刻意的（见下）。
- **删除死代码**：`launch.ps1`（`launcher.ps1` 的近似复制品，无任何消费者）与 `launcher.ps1`，由 `installer/antnest_boot.ps1` 取代。同步更新 `build_launcher.ps1`、`AntNest.iss`、`tools/release_check.ps1`、`installer/README.md`、`.gitignore`、RELEASE_NOTES。
- **顺带把陈旧 exe 变成可检测**：`tools/sync_release.ps1` 注入预构建 exe 且不重跑构建脚本，源码改了 exe 也不会更新。加 `$BOOT_SCHEMA` 指纹并由 `release_check.ps1` 比对——本次即因此发现入库 exe 版本资源已过期并重建。
- **本步推翻了自己的一版设计**（详见 BOOT-DESIGN §0）：v1 声称存在一条「import 期 `sys.exit(1)` → 静默死亡」的链路，评审逐环核对后发现第 1-2 环是错的——`antnest_bridge` 模块级并不 import `antnest_config`，它只经 `:775` 的**惰性** `importlib.import_module("AntNest")` 到达，而那里有 `except SystemExit` 且 stdout 已被换成 `StringIO`，错误原文会被渲染进聊天面板（`antnest_bridge.py:1192`）。我用一个关于 CPython 的正确事实（`SystemExit` 不经 `excepthook`）推出了一条假的事实。v1 基于此的 P6（预检 API Key，未配则阻止启动）会造成**净倒退**：拦住 → 应用不启动 → 设置页打不开，而它是唯一的自助修复路径。已删除。
- **守住的硬约束**：
  - **零黑框**：`-RedirectStandard*` 会强制 `UseShellExecute=false`，使 `WindowStyle` 失效，父进程无控制台时子进程会新分配一个**可见**控制台。已加测试禁止该用法。
  - **无误报超时**：健康应用一直运行到用户关窗，任何 `WaitForExit(毫秒)` 都会在每次成功启动时误报。已加测试禁止（WebView2 安装器的有界等待单独豁免并反向守卫）。
- **遗留**：`antnest_config.py:116-120` 的 import 期 `sys.exit(1)`（R8）**本轮不动**。它的表现已可观测（`antnest_bridge.py:1192` 会把原文上屏），本轮只是不再让入口层掩盖它。R11：`ps2exe` 产物与真实启动失败场景需在有 GUI/有网环境手工各验一次（本轮已用 PowerShell 5.1 实机跑通 20 项行为断言，但无头环境下 `MessageBox` 被打桩）。

---

### Step 0 — Module Inventory（模块清单单一真源）

#### 解决的问题

**修复了一个已存在的生产级缺陷：工蚁在非 editable 安装下无法启动。**

`antnest_queen.spawn_clone` 用手写元组把 16 个模块复制进工蚁隔离目录，但这份清单
与真实代码脱节，漏掉了三个**顶层 import 的硬依赖**：

| 缺失模块 | 被谁在模块顶层 import |
|----------|----------------------|
| `antnest_log.py` | `antnest_clone_worker.py:13` |
| `antnest_errors.py` | `code_tools.py:26`、`antnest_llm.py:15` |
| `antnest_config_schema.py` | `antnest_config.py:76`（工蚁必经） |

之所以一直没暴露：开发环境是 editable 安装（`__editable__.antnest-1.3.1.pth`），
site-packages 的 finder 把缺失模块从仓库根解析了出来。**那是开发环境的巧合，不是代码
的性质。** 一旦换成 wheel 安装或干净 venv，工蚁会在 import 期 `ModuleNotFoundError`，
`result.json` 不生成，`spawn_clone` 返回「工蚁未生成结果文件」——而
`view_file` / `list_dir` / `grep_files` / `write_file` / `search_replace` **全部**
经由 `spawn_clone`，等于**整条工具链全瘫**。

`installer/AntNest.iss` 同样漏了 `antnest_log` / `antnest_errors` /
`antnest_config_schema` / `antnest_ui_pure`，被 `launcher.ps1` 走 `uv run --project`
掩盖。

仓库里**此前没有任何一个测试真正启动过工蚁进程**（`test_spawn_clone.py` mock 了
`Popen`，深度闸门用例在到达 `Popen` 前就 return 了），所以 163 个测试对这类故障
结构性失明。

#### 改动

- **新增 `antnest_inventory.py`**：用 `ast` 静态扫描 `AntNest.py` 的**真实 import
  传递闭包**派生清单，取代手写元组。UI 层（`antnest_bridge` / `prototype_antnest` /
  `phtmlwin` 等）被显式排除——工蚁不需要也不该复制它们。
- **`antnest_queen.spawn_clone`**：复制循环改用 `antnest_inventory.worker_modules()`。
  清单从 16 → 22 个模块。
- **`installer/AntNest.iss`**：补齐 4 个缺失模块 + `antnest_inventory`。
- **`pyproject.toml`**：补 `antnest_inventory`。
- **新增 `tests/test_worker_e2e.py`（13 个用例）**，其中最关键的三条：
  - `test_worker_runs_without_repo_on_syspath` — 把工蚁文件复制到临时目录，
    **`PYTHONPATH=""` + `python -E` + cwd 隔离**的前提下以独立进程真跑一次工蚁，
    断言 `result.json` 存在且 `status == "ok"`。这是仓库第一个端到端工蚁测试。
  - `test_legacy_list_is_insufficient` — 故意用**修复前**的手写清单搭目录，断言工蚁
    起不来。把这个 bug 钉死在测试里。
  - `WorkerLeafModuleTest` — 静态断言 `antnest_clone_worker` 的传递依赖**不触达**
    `antnest_config`（见下方「新增的架构约束」）。
- **`antnest_inventory.py` 支持 `python -m antnest_inventory` 自检**，直接打印
  import 闭包与两份打包清单的差异。

#### 新增的架构约束（有测试守着）

`antnest_config.py` 的 import 顺序是致命的：

```
antnest_config.py:25   import antnest_clone_worker   ← 先
antnest_config.py:33   import antnest_log             ← 后
antnest_config.py:47   run_if_clone_mode() → sys.exit(0)
```

`antnest_clone_worker` 在第 25 行被 import 时，`antnest_config` 处于**半初始化**状态
（只绑定了 ≤25 行的名字）。因此：

> **`antnest_clone_worker.py` 必须是叶子模块——只允许 import `antnest_log` 与标准库。
> 任何传递依赖到 `antnest_config` 的改动都会在 import 期循环导入，杀死所有工蚁。**

设计文档 v1 初版曾计划「把权限等级下发给工蚁、工蚁内自行判级」，按常规写法实现会在
第一次提交就炸掉整个工蚁群。这个约束现在有测试守着。

#### 还有什么痛点（未解决）

| 痛点 | 说明 | 计划 |
|------|------|------|
| `antnest_config.py:118` import 期 `sys.exit(1)` | 未配置 API Key 时直接杀进程。`AGENTS.md` §1 明确要求「禁止 `sys.exit(1)` 阻塞启动」。**这让全仓库无法在无 key 环境下测试**，是 eval harness 的头号障碍 | 遗留 R8，本轮不动（改启动语义风险太大） |
| `antnest_loop.py:154-159` 状态词汇表 | 只把 `error`/`blocked`/`approval_required` 视为非 ok，新增的 `denied` 会被记成 `ok` | Step 3 |
| `antnest_loop.py:131-141` 重复调用检测 | 连续 3 次相同的**被拒**调用会触发「自我反思换方法」——等于教模型换一个方式重试刚被禁止的操作 | Step 3 |
| `ALLOW_ALL_CLI` 声明但零读取 | 管理员模式下危险命令走阻塞式 `input()`，UI 里会冻结工作线程 | Step 3 |
| 工具调用顺序执行 | `agent_single_loop.py:116` 顺序跑 `tool_calls`，「可并行」只是 prompt 里的建议，`MAX_CLONES` 无代码强制 | 遗留 R6，不在本轮 |
| `load_settings`/`save_settings` 重复实现校验钳制 | 与 `antnest_config_schema` 存在漂移风险 | 遗留 R7 |

---

### Step 1 — Permission Model（L0–L5 权限分级）

#### 解决的问题

**1. 顺带修掉第二个「清单漂移」缺陷：自身源码保护名单已过期。**

`antnest_config._SELF_SOURCE_NAMES` 是手写集合，落后于模块拆分——`antnest_config.py`、
`antnest_log.py`、`antnest_errors.py`、`antnest_config_schema.py`、`antnest_queen.py`、
`antnest_llm.py` **全都不在名单里**。也就是说「修改 AntNest 自身源码需用户确认」这条
门禁，**对配置文件本身是不生效的**：Agent 可以无确认地改写 `antnest_config.py`。
现改为从 `antnest_inventory` 的 import 闭包派生（33 个条目，原 14 个）。

**2. 把权限判断从「散落 6 个点的 if」收敛成一个可声明、可审计的决策函数。**

原先权限相关逻辑分布在 `spawn_clone` / `write_file` / `search_replace` / `run_cli` /
`run_python` 五处，三张语义不同的危险命令表，且没有任何地方能回答「这只工蚁能看什么、
能写什么、能不能联网」。

新增 `antnest_permissions.py`：

- `PermLevel` L0–L5（READ / WRITE / EXECUTE / NETWORK / SYSTEM / SELF_MOD）
- `Action` 三态 ALLOW / ASK / DENY（路线图 §10 要求 `[Allow Once] [Allow Task] [Deny]`，
  二态表达不了「先问我」）
- `decide()` 核心判定，**顺序即语义**：

  ```
  unknown tool                → DENY   fail-closed，忘登记的工具不会静默放行
  explicit_confirm            → ASK    自源码 / 危险命令 / L4，永不 ALLOW
  required <= level           → ALLOW
  required > ask_above_level  → DENY
  otherwise                   → ASK
  ```

  `explicit_confirm` 排在分级之前是刻意的。否则 `level=5`（最自然的「全开」配置）会因为
  `SELF_MOD(5) <= 5` 直接 ALLOW，把「改自身源码永远需要人明确同意」这个不变量绕过去。
  评审初版正是踩了这个坑，已由 `DecideOrderTest.test_explicit_confirm_beats_even_max_level`
  钉死。

- **参数敏感判定优先于静态等级**（硬约束 C2）。`write_file` 的静态等级是 L1，但如果
  `path` 指向核心源码，结论必须是 ASK 而非 ALLOW。否则等于给了 Agent 一把改写自身
  源码的钥匙——这是 v1.4 本会**主动引入**的安全回归。已由
  `ArgSensitivityTest.test_write_to_self_source_asks_even_at_write_level` 钉死。
- **授权按作用域签发**，不按工具：批准改一个文件不等于批准改整个仓库。
  `grant_once` / `grant_task` / `reset_turn` / `reset_task`。
- **危险模式表合并为一份**：`antnest_permissions.DANGER_PATTERNS` 升格为主表（吸收
  `admin_utils.DANGEROUS_COMMANDS` 的分类语义）。`antnest_queen._DANGER_CLI_PATTERNS`
  保留为兼容别名，`_check_danger_command` 改为反向委托。
  `antnest_clone_worker.dangerous_patterns` **保留不动**——它是工蚁进程内的第二道红线，
  且受硬约束 C1 限制（不能 import 业务模块）。这层冗余是正确的防御纵深。

**3. 新增 `config.json → permissions` 段**，全部字段有安全默认值，旧 `config.json`
不加任何东西即可运行。`level=3` 逐项复刻 v1.3.1 行为：

| 能力 | v1.3.1 实际行为 | level=3 判定 |
|------|----------------|-------------|
| 读文件 | 允许 | ALLOW |
| 写项目文件 | 允许 | ALLOW |
| 执行命令 | 允许 | ALLOW |
| web_fetch / MCP | 允许 | ALLOW |
| 危险命令（管理员） | 弹确认 | ASK |
| 改自身源码 | 需明确同意 | ASK |

额外收获：`level=0` 提供「只读审阅模式」，`level=2` 提供「禁网络」——一个字段换来
一整套可用的降权姿态。

**4. 测试**：`tests/test_permissions.py`（40 用例），含等级矩阵、判定顺序、参数敏感性、
授权生命周期、schema 校验，以及一个刻意反直觉的
`DangerNotSandboxTest.test_danger_patterns_do_not_catch_obfuscated_payload`——
它断言「看起来无害、实际做坏事的 Python」**不会**被危险模式拦到。存在的意义是防止
文档和 UI 夸大权限模型的能力。

#### 还有什么痛点（未解决）

| 痛点 | 说明 | 计划 |
|------|------|------|
| **权限引擎还没接线** | 本步只交付模型 + 配置 + 测试。`spawn_clone` / `write_file` 等仍走旧的 `_self_modification_gate`，`agent_single_loop` 也还没有权限闸门 | **Step 3** |
| `ALLOW_ALL_CLI` 声明但零读取 | 管理员模式下危险命令走阻塞式 `input()`，UI 里会冻结工作线程 | Step 3 |
| `denied` 未登记进非 ok 元组 | `antnest_loop.py:154-159` 只认 error/blocked/approval_required，新词汇会被记成 `ok` | Step 3 |
| 被拒调用会计入重复检测 | 连续 3 次相同的**被拒**调用会触发「自我反思换方法」，等于教模型换方式重试刚被禁止的操作 | Step 3 |
| **权限不是 Sandbox** | 危险模式只是提示不是控制：`python x.py` 里藏 `shutil.rmtree` 拦不到。真正的强隔离需 Job Object / WSL2 / AppContainer | 遗留 R4（v1.5） |
| UI 无处展示权限 | 还没有 ✓/✗ 徽章，也没有越权询问的呈现 | Step 8 |
| `antnest_config.py:118` 的 `sys.exit(1)` | 阻断无 key 环境下的测试 | 遗留 R8 |
| `antnest_loop` / `queen` 的工具名仍手写 4 处 | 畸形调用正则与 prompt 工具列表都还是旧的 | Step 2 |

---

### Step 2 — Tool Registry（工具名单点维护）

#### 解决的问题

工具名此前被手写 4 遍，其中**两处已经腐烂**：

| 位置 | 覆盖 | v1.3.1 状态 |
|------|------|------------|
| `AntNest.py` tool_executors dict | 16 | 真源，但不含 level / exposed 元信息 |
| `antnest_queen.get_queen_tools` | 11+2+2 | 手工同步 |
| `antnest_loop._detect_malformed_tool_call` 正则 | 13 | **已过期**（缺 register_tool / list_tools / get_tool_source） |
| `prompts/queen_system.md` 散文列表 | 13 | **已过期**（同上） |

任何 allowlist 或权限声明挂在这些地方，只会跟着一起烂。新增 `antnest_registry.py`，
`TOOL_SPECS` 成为唯一硬编码清单，其余全部派生：

| 派生函数 | 取代 |
|----------|------|
| `build_executors(ns)` | `AntNest.py` 的 dict 字面量 |
| `visible_schemas(ns, ...)` | `get_queen_tools` 的手写列表 |
| `malformed_call_regex()` | `antnest_loop` 的手写 alternation |
| `prompt_catalog()` / `prompt_catalog_lines()` | `queen_system.md` 的散文清单 |

`ToolSpec` 除名字外还携带 `level`（所需权限等级）、`exposed` / `worker_only` /
`panic_only`（可见性）、`mutating`（是否改状态，供 Plan 节点归属推断用）——这些字段
是 Step 3 权限闸门与 Step 5 Plan DAG 的输入。

#### 三个刻意做出的设计决定

**1. 畸形调用正则覆盖 `TOOL_SPECS` 全集（16 个），而不是 exposed 子集（11 个）。**

旧手写正则是 13 个，比 exposed 还多——因为它顺带覆盖了 `run_cli` / `run_python` /
`leave_memory_hints` / `mcp_call` / `mcp_list_tools`。其中 `leave_memory_hints` 尤其
关键：它只在 `COMPACT_PANIC` 下暴露，若按 exposed 收窄，模型就能用纯文本伪造它的
JSON 调用绕过压缩流程。所以宁可放宽。

**2. MCP 建模成两个普通 spec，不做「按服务器动态生成条目」。**

设计初稿曾打算把每个 MCP 工具做成动态项追加进 `visible_specs`。**那是错的**：
`mcp_call(server, tool, args)` 是单一派发器，`tool_executors` 里只有 `"mcp_call"`
一个键。追加动态项会产生**有 schema 没执行器**的工具，而 Step 3 的 fail-closed 权限
会把它们**全部拒绝**——MCP 就从「能用」变成「全瘫」。已加反向不变量测试
`test_exposed_subset_of_executors` 守住这条。

**3. 不做 `{tool_catalog}` 运行时注入，改用 prompt 一致性测试。**

设计初稿想把工具列表做成 SYSTEM_PROMPT 占位符。但 `SYSTEM_PROMPT.format(...)` 在
**6 处**被调用（`AntNest.py`×2、`antnest_bridge`、`antnest_memory`、`antnest_session`、
`prototype_antnest`），`str.format` 对未替换的 `{...}` 抛 `KeyError`；且工具可见性
依赖运行时的 `MCP_ENABLED` / `COMPACT_PANIC`，import 期烘焙会 advertise 不存在的工具。
改为：修好 `queen_system.md`（补齐 3 个漏掉的工具、说明 `denied` 新词汇、把
`run_cli`/`run_python` 明确移出可调用集合），并加 `QueenPromptFileTest` 在 CI 卡住
漂移。**消灭漂移靠测试，不靠运行时魔法。**

#### 回归：可见性与顺序必须逐项不变

接线时一度把 `leave_memory_hints` 暴露到了常态（12 个可见工具 vs 旧的 11 个）。这是
真实的行为变更——该工具的实现要求 messages 里存在 `COMPACT_PROMPT` 标记
（`antnest_memory.leave_memory_hints:59-66`），常态暴露只会让模型反复调用后拿到错误。
已加 `panic_only` 字段修正。

`tools` 数组的**顺序**也会影响模型注意力分布，因此 `TOOL_SPECS` 中蚁后可见的部分
按 v1.3.1 的原始顺序排列（MCP 追加在尾部），并由两个用例钉死：

- `test_visibility_is_identical_to_v131_handwritten_list`（集合相等）
- `test_legacy_order_is_preserved_when_sorted_by_first_use`（顺序相等）

#### 测试

`tests/test_registry.py`（35 用例）。重点不是「某函数返回什么」，而是**四种派生物之间
必须一致**：spec ↔ executor ↔ schema ↔ prompt ↔ 畸形正则。另含
`NoHandwrittenEnumerationTest`，用源码断言确保手写枚举不会回流。

#### 还有什么痛点（未解决）

| 痛点 | 说明 | 计划 |
|------|------|------|
| **权限引擎仍未接线** | 注册表已提供 `level`，但 `agent_single_loop` 与 queen 的 5 个门禁点还没 consult 它 | **Step 3** |
| `antnest_loop.py` 的 `import re` 已无使用者 | 派生正则后成为死 import | Step 3 顺手清理 |
| `remove_tool` 仍不可达 | `antnest_toolforge.remove_tool` 不在 `tool_executors` 也不在 spec 里，LLM 调不到 | 遗留 |
| 动态注册工具（toolforge 落盘的那批）不进注册表 | 它们是 filesystem-backed 的第二套注册表，只有 `register_tool` 这个入口工具在 spec 里 | 遗留（v1.5 与 Worker Profile 一起处理） |
| Plan / Event / Checkpoint 均未开始 | — | Step 4–6 |

---

### Step 3 — 权限闸门接线（并修掉一个致命回归）

#### 解决的问题

**1. 修复一个致命回归：v1.3.1 基线上「所有工具调用全部失效」。**

`antnest_loop` 写的是 `_A().get_audit().log_tool_call(...)`，但 `AntNest` 壳**从未
re-export `get_audit`**（它只是 `antnest_log` 的一个函数）。于是每一次工具派发都
抛 `AttributeError`，被同一 `try` 块里的宽 `except Exception` 吞成：

```
工具执行异常：module 'AntNest' has no attribute 'get_audit'
```

**结果：Agent 一个动作也做不了。** 这个 bug 随 v1.3.1 的模块拆分引入
（`git log -S get_audit` 确认 `get_audit` 在拆分前根本不存在）。

之所以长期没被发现：163 个测试里**没有任何一个真正驱动过 `agent_single_loop` 的
工具派发路径**——和 Step 0 的工蚁依赖清单是同一类问题（关键路径无测试覆盖）。

修法（两处，缺一不可）：
- `antnest_loop` 改用模块级 `antnest_log.get_audit()`。工具函数不该走 `_A()` 那条
  脆弱的壳属性查找路径。
- `AntNest.py` 补上 `get_audit` / `get_logger` / `set_bridge_emit` 的 re-export，
  维持 `AntNest.get_audit` 这个外部契约。

**2. 权限闸门接入 `agent_single_loop`（全仓库唯一工具派发点）。**

新增 `check_permission(name, args)`，在 `tool_executors[name](**args)` 之前判定：

- 未在注册表登记的工具 → **fail-closed DENY**（新增工具忘登记不会静默放行）
- 参数敏感判定优先于静态等级（硬约束 C2）
- DENY/ASK 都写 `audit.log` 的 `log_security_event`（该方法自 v1.3.1 起就写好了
  但**零调用点**，本步接上）

**3. 替换 queen 内部 5 处手写门禁**，统一走 `PermissionEngine`：

| 位置 | 原实现 | 现实现 |
|------|--------|--------|
| `spawn_clone` | `_command_self_source_target` + `_self_modification_gate` + admin `input()` | `check_command` |
| `write_file` / `search_replace` | `_self_modification_gate` | `check_write_path` |
| `run_cli` / `run_python` | `_command_self_source_target` + `_check_danger_command` | `check_command` |

**4. 修掉 `ALLOW_ALL_CLI` 声明但零读取导致的 UI 冻结。**

`ALLOW_ALL_CLI` 被 `antnest_bridge.py:809` 设为 `True`（注释写「UI 无法做终端确认」），
但**全仓库无任何读取点**。实际后果：管理员模式下每条危险命令都会走
`admin_utils.get_user_confirmation()` → **阻塞式 `input()`**。而 UI 的 `_turn` 跑在
本进程的 `threading.Thread` 里，用户没有任何界面能应答 → **整个 Agent 冻结**。

现在统一走对话式授权：返回 `approval_required` → `antnest_loop` 结束本批次并在聊天里
说明 → 用户下一条消息授权。`input()` 只在「终端 + 管理员 + `ALLOW_ALL_CLI` + stdin 可用」
四个条件同时满足时才走（CLI 场景保留更好的体验）。

**5. `denied` 登记进非 ok 词汇表。**

`antnest_loop` 之前只把 `error`/`blocked`/`approval_required` 当作失败。新增的
`denied` 若不登记，审计与统计会把「被拒绝」记成「成功」。已提取
`NON_OK_STATUS` 常量 + `_result_status()` 辅助函数。

**6. 被拒调用不再触发「换方法重试」的自我反思提示。**

`_DUP_CALL_LIMIT=3` 原本会对连续 3 次相同的**被拒**调用注入《自我反思》——
等于在教模型换一个方式去重试一个刚被明确禁止的操作。现在被拒的调用会从重复计数里
移除。

**7. 收紧 `check_tool` 的 API。**

`required` 参数原本默认 `PermLevel.READ`。调用方一旦忘记传，就会静默拿到 ALLOW——
这正是 fail-closed 要防的方向。现改为**必填**。

#### 测试

`tests/test_permission_gate.py`（17 用例）。用假 LLM（`mock.patch.object(AntNest,
'llm_chat_stream', ...)`）驱动**真实的** `agent_single_loop`，走真实权限判定、真实
工具结果回灌、真实 break_loop。覆盖：

- 写自身源码的 `write_file` / `search_replace` **执行器未被调用**
- `spawn_clone("rm -rf /")`、`spawn_clone("echo x > antnest_loop.py")` 均被拦下
- 只读命令（`cat antnest_loop.py`）**不被误拦**（过度拦截会让 Agent 无法工作）
- 未登记工具 fail-closed
- 拒绝后每个 `tool_call` 仍有 `tool` 响应（否则 API 会 400）
- 拒绝后循环立即终止（iterations == 1）
- 被拒调用不触发自我反思提示
- `spawn_clone` 在 UI 模式下**绝不调用 `input()`**（mock 断言）

> 写这个测试的过程直接暴露了上面第 1 条的致命回归——如果只做 Step 1/2 而不真正
> 驱动一次循环，这个 bug 会一路带到发布。

#### 还有什么痛点（未解决）

| 痛点 | 说明 | 计划 |
|------|------|------|
| **UI 无处呈现权限** | 用户看不到当前 level、看不到 ✓/✗ 徽章；越权询问也只是聊天里的一段文字 | **Step 8** |
| 对话式授权只认「同意修改自身源码」 | `_maybe_approve_self_modification` 的正则只匹配自源码场景，危险命令/越权的授权走不通 | Step 8 |
| 审计与事件未分离 | 工具事件仍写 `audit.log`（Step 4 会拆成 events/ + audit 两份） | Step 4 |
| 权限判定在工具内部也会跑一次 | `view_file` → `spawn_clone` 会二次判定。冗余但无害（两层防线） | 保留 |
| 授权存储在内存 | 进程重启后一次性/任务级授权全部失效 | Step 6 随 checkpoint 一起考虑 |
| **权限不是 Sandbox** | 危险模式只是提示不是控制 | 遗留 R4（v1.5） |

---

### Step 4 — Event Log（结构化事件流 + 任务重放）

#### 解决的问题

v1.3.1 有三份日志，定位一直很模糊：

| 文件 | 格式 | 内容 | 问题 |
|------|------|------|------|
| `.antnest/antnest.log` | JSONL | 全部 `antnest.*` logger | 是 debug 日志，不是事件流 |
| `.antnest/audit.log` | JSONL | tool_call / tool_result | 混了「发生了什么」与「谁被拦了」 |
| `.antnest/ui_trace.log` | 纯文本 | UI 阶段流水 | 只有阶段号，重放不出任务时间线 |

`AuditLogger` 里 `log_worker_spawn` / `log_worker_done` / `log_config_change` /
`log_security_event` 四个方法从 v1.3.1 起就写好了，但**零调用点**。

本步确立分工：

```
events/<task_id>.jsonl   任务生命周期  → 可重放
audit.log                仅安全事件    → 可追责
```

于是 `antnest_loop` 不再把工具事件写进 audit.log，audit 只留安全决策。
`log_config_change` 也接上了（`apply_settings` 热应用时记录变更前后值）。

**事件集**（31 个）：任务 7 / 计划 4 / 工蚁 7 / 工具 2 / 权限 3 / 检查点 2 / 循环 5。

**重放输出**正是路线图 §5 想要的形态：

```
18:03:22 #0001  TASK_CREATED     goal=修复测试失败
18:03:22 #0002  LOOP_ROUND       round=1
18:03:22 #0003  TOOL_CALL        tool=list_tools
18:03:25 #0004  TOOL_RESULT      tool=list_tools status=ok duration_ms=1234.5
18:03:25 #0005  TASK_COMPLETED
```

#### 实现的改动

- **新增 `antnest_events.py`**：`Event` 枚举 + `EventLog`（JSONL + 内存环 + 轮转）
  + 订阅机制 + 回合级 task_id 上下文。
- **`antnest_loop`**：TOOL_CALL / TOOL_RESULT / PERMISSION_REQUESTED /
  PERMISSION_DENIED / LOOP_ROUND / LOOP_MAX_ROUNDS / LOOP_DUP_CALL / LOOP_COMPACT /
  LOOP_CANCELLED 全部接线。`log_tool_call` / `log_tool_result` 从循环中移除。
- **`antnest_queen.spawn_clone`**：WORKER_STARTED / COMPLETED / FAILED / TIMEOUT /
  CANCELLED / RETRY 接线（父进程侧——蚁后本来就观察得到 spawn 的起止）。
- **`antnest_bridge._turn`**：TASK_CREATED / TASK_STARTED / TASK_COMPLETED /
  TASK_FAILED / TASK_PAUSED 接线；回合结束调 `reset_turn()` 清理一次性授权。
- **`wrapped_spawn` 回灌 `wid` 与 `worker_name` 到工蚁结果**。原来结果里没有 wid，
  Plan 无从建立「节点 ↔ 工蚁」映射（Step 5 的前置）。

#### 四个硬约束，各有测试守着

1. **fail-open，但首次失败必须出声**。写盘失败时 `emit` 仍返回记录（事件本身发生了，
   只是没持久化，返回 None 会让调用方误判）。首次失败打 WARNING，之后降级为 DEBUG
   免得刷屏——否则「静默丢弃」和「正常没记录」从外面看一模一样。
2. **绝不落盘推理内容 / 凭据**。`reasoning_content`、`thinking`、`api_key`、
   `password`、`secret` 一律 redact；但 `total_tokens` / `prompt_tokens` /
   `completion_tokens` 白名单放行（评测指标要它们）。有一条用例专门把
   `reasoning_content` 塞进事件再断言文件里搜不到。
3. **落 `PROJECT_ANT_DIR` 而不是 `ANT_HOME`**。ANT_HOME 在安装版指向 Program Files
   （只读），记在那儿等于所有安装用户的记录被静默丢弃。
4. **写盘用独立的 `_write_lock`，不复用 `STATE_LOCK`**。后者要覆盖 plan 的状态变更，
   持锁做文件 I/O 会把 UI 线程的读取一起阻塞。

#### 自己的测试抓出的 3 个 bug

| Bug | 后果 |
|-----|------|
| `_FORBIDDEN_KEY` 用 `$` 锚定结尾 | `reasoning_content` 以 "content" 结尾，**没被脱敏**——思维链照常落盘。改成「键名含敏感词即 redact」+ 凭据精确名单 + 后缀匹配 |
| 多线程各自 `open(path,"a")` | Windows 上**丢行**：6 线程 × 40 条只剩 237 条。加 `_write_lock` 后 8 线程 × 300 条 = 2400 条，一条不丢、seq 连续 |
| `tasks()` 只捕 `OSError` | 路径含空字符时抛 `ValueError`，`stats()` / `replay()` 反而把调用方搞崩。补 `ValueError` + 读侧整体 fail-open |

#### 测试

- `tests/test_events.py`（36 用例）：记录格式、隐私、fail-open、轮转、查询、重放、
  并发、订阅、辅助函数。
- `tests/test_event_wiring.py`（13 用例）：用假 LLM 驱动真实 `agent_single_loop`，
  验证事件真的产生、顺序正确、可重放、不含思维链、audit/events 分工正确。
  含一条「`wrapped_spawn` 结果必须带 wid」的接线断言。

#### 还有什么痛点（未解决）

| 痛点 | 说明 | 计划 |
|------|------|------|
| **重放没有入口** | 能跑 `EventLog.replay()`，但用户/运维没有命令行或 UI 入口 | Step 8 |
| 工蚁内部事件不记录 | 工蚁是独立进程，汇入需 `AN_EVENT_FILE` 跨进程写入 | 遗留（v1.5） |
| 授权状态不持久 | 一次性/任务级授权只存内存，进程重启即失效 | Step 6 |
| `ui_trace.log` 未合并 | 三份日志变成「events + audit + ui_trace」，仍是三份 | 遗留（与 stdout 抓取层一起处理） |
| 事件没有 UI 实时通道 | `subscribe()` 已就绪但没人用 | Step 8 |
| 磁盘占用 | 8MB × 3 代 × 20 个任务上限，约 480MB 理论峰值 | 观察，必要时收紧 |

---

### Step 5 — Plan DAG（显式计划层 + 自动派生兜底）

#### 解决的问题

v1.3.1 的「计划」只以自由文本存在于 `reasoning_content` 与聊天气泡里。UI 上的
`SUBTASKS` 面板是**自动派生**的——一次 `spawn_clone` 对应一条，与蚁后实际想做
什么无关。这正是路线图 §3 要解决的：「蚁后负责规划、工蚁负责执行」在产品层
从未真正体现。

#### 双数据源（缺一不可）

1. **LLM 显式规划** —— 新增 `update_plan` 工具。`level=READ`、`mutating=False`：
   它不碰任何文件、不执行任何命令，只登记意图。标成 WRITE 会让「只读审阅模式」
   下无法规划。
2. **自动派生（兜底，必须有）** —— `spawn_clone` 通过权限闸门后，若 LLM 从未
   显式规划过则自动开一个节点并认领该工蚁。

第 2 条不是可选的。flash 类模型经常不按格式调工具；只依赖 `update_plan` 会让
Plan 面板在多数真实任务里是空的，整个特性等于没做。

**硬指标已写成测试**：任何含 `spawn_clone` 且真正执行的回合，
`ui_render.render_plan(plan.snapshot())` 必须非空。

#### 环检测必须整体拒绝

`update_plan` 与 `ready_nodes()` 都检环。有环则**整体拒绝该次更新并保留原计划**——
半个新计划比旧计划更糟，而一个有环的 DAG 会让 `ready_nodes()` 永远返回空、
计划直接死锁。被拒时返回 `error` + `hint`（告诉模型怎么改），并记 `PLAN_REJECTED`。

#### 三个接线点

| 位置 | 动作 |
|------|------|
| `spawn_clone` 权限通过后 | `ensure_node_for_spawn()` 派生/复用节点，绑 `task_id → node_id` |
| `spawn_clone` 归巢后 | `complete_node_for_spawn()` 按 task_id 反查并推进 |
| `wrapped_spawn` | 补 `wid` 绑定，并向 UI `emit("plan", ...)` |

#### UI 渲染（`ui_render.py` + `app.css`）

新增三个纯函数，无 webview 依赖，可直接单测：

- `render_plan(plan)` —— 按依赖深度缩进的树形面板，节点带状态字形
  （`○ ● ✓ ✗ − ⊘`）、工蚁归属、结果摘要、错误信息，顶部有进度条。
- `render_permissions(rows)` —— 路线图 §10 的 ✓/✗ 权限徽章。
- `render_timeline(records)` —— 路线图 §5 的事件时间线。

所有文本过 `esc()`：模型产出的 title / result_summary 可能含尖括号或脚本标签，
`tests/test_ui_plan_render.py` 有专门的注入用例。样式进 `app.css`，含
`prefers-reduced-motion` 降级（running 节点的脉冲动画）。

#### 自己的测试抓出的 2 个 bug

| Bug | 后果 |
|-----|------|
| `replace()` 里旧索引取在赋值**之后** | LLM 重建计划时，已完成节点的进度**全被抹掉**。`old = self._index()` 拿到的是新列表，条件恒为假 |
| 计划派生排在权限闸门**之前** | 被拒绝的 `rm -rf /` 也会在计划里留下节点——但那件事根本没发生，计划会说谎 |

两条都由测试钉死（`test_rebuild_keeps_completed_state`、
`test_denied_spawn_leaves_plan_empty`）。

#### 测试

- `tests/test_plan.py`（49 用例）：DAG 拓扑、环拒绝、进度保留、工蚁绑定、
  自动派生、快照往返、提示词渲染。
- `tests/test_ui_plan_render.py`（32 用例）：HTML 结构、缩进深度、注入安全、
  样式确实进了 app.css。
- `tests/test_plan_wiring.py`（15 用例）：端到端——真派一次工蚁验证计划被派生
  且归巢后节点完成；含深度拦截与权限拒绝时计划保持为空。

#### 还有什么痛点（未解决）

| 痛点 | 说明 | 计划 |
|------|------|------|
| **UI 还没接上 plan 事件** | `render_plan` 已就绪、bridge 已 emit `plan`，但 `prototype_antnest.py` 侧没有 `PLAN` 状态与渲染分支 | **Step 8** |
| **执行仍是顺序的** | `ready_nodes()` 表达的是依赖结构，不是并发承诺。`agent_single_loop` 顺序跑 `tool_calls` | 遗留 R6，不在本轮 |
| `max_clones` 仍无代码强制 | 只用于填 prompt 占位符 | 遗留 |
| 计划不进系统提示 | `Plan.to_prompt()` 已实现但没接进 `_with_retrieved_memory` | Step 6 随 checkpoint 一起接 |
| 计划不持久 | 进程重启即丢 | Step 6 |
| 节点无重试语义 | `attempts` 字段在，但没有「失败后重试该节点」的流程 | 遗留 |
| `MAX_NODES=60` 是硬编码 | 没进 config | 遗留 |

---

### Step 6 — Task Checkpoint / Resume（真正的任务检查点）

#### 解决的问题

v1.3.1 只有 `antnest_runtime_state.record_stop`：用户按停止时存一份「决策摘要 +
证据 + 下一步」的文本，恢复时靠正则从 `messages` 里找最后一次 user 消息当查询。
那不是检查点——它**不知道做过什么、不知道计划走到哪、不知道哪些工蚁在跑**。

本步提供真检查点：计划快照 + 消息尾部 + 事件水位 + 统计，恢复时能明确回答
路线图 §6 的三问。

#### 核心是 `rehydrate()`，不是切片

朴素地 `messages[-12:]` 会产出**被 API 拒绝**的消息列表，且既有回填逻辑救不回来：

| 断裂 | 后果 |
|------|------|
| 切点落在工具批次中间 | 列表**以 `role:"tool"` 开头**，父 assistant 已丢失 → OpenAI 兼容端点直接 400 |
| `antnest_loop` 的回填只扫**最后一个** assistant、补**缺失**响应、然后 `break` | 无法修复开头的孤儿 tool 消息 |
| 12 条几乎不含 `messages[0]` | **system prompt 丢失** —— NEST.md / hints / ENV_INFO / 模型能力 / 深度规则 / 工具契约全没 |
| `llm_chat_stream` 挂 `reasoning_content` | 切片落盘即**明文持久化思维链**，与既有约定冲突 |

失败方式最恶劣：`ApiError` 被 `antnest_loop` 的宽 `except` 吞掉后 `break`，
整轮没有任何 assistant 消息，UI 什么也收不到——「能恢复」静默失效。

所以恢复走四步 `rehydrate()`：

1. 前置**重新构建的** system 消息（读壳上当前的 `messages[0]`，丢弃切片里的旧版）
2. 丢弃开头的孤儿 `role:"tool"` 消息，直到遇到 user 或不带 tool_calls 的 assistant
3. 为残留的每个 `assistant(tool_calls)` 在该批次内合成响应，**遍历全部批次**
   （不像 loop 里的回填只扫最后一个）
4. 剥离 `reasoning_content` / `thinking` 等思维链字段

#### 快照时机

**必须在工具批次闭合的静止点取**。设计初稿说「工蚁归巢时快照」是错的——那时
`agent_single_loop` 正在迭代 `msg["tool_calls"]`，下一步还会往 `messages` 追加，
快照是撕裂的。

实际触发点：

| 时机 | reason |
|------|--------|
| 每个回合边界（while 顶部） | `round_boundary` |
| 工具批次正常闭合 | `batch_closed` |
| 等待用户授权 / 被硬拒绝 | `awaiting_approval` / `denied` |
| 用户按停止（bridge.stop） | `user_stop` |
| 达到最大轮次 | `max_rounds`（经 batch_closed 覆盖） |

#### 存储

`.antnest/checkpoints/<task_id>/<seq>.json`，原子写（temp + `os.replace`），
每任务 20 个 + 全局最近 5 个任务。`ANT_CHECKPOINT=0` 可关闭。

#### 新增 bridge 接口

- `list_checkpoints(task_id)` —— 列出检查点
- `resume_task(task_id)` —— 还原 messages + 计划，向 UI 发 `chat` 与 `plan`
- `replay_task(task_id)` —— 返回事件时间线文本

#### 自己的测试抓出的 2 个 bug

| Bug | 后果 |
|-----|------|
| `list()` 靠文件名字符串排序 | seq 超过 999 时 `"1000" < "999"`，**时间顺序错乱**。改为按数值排序 |
| 尾部里的旧 system 消息没被丢弃 | 恢复后出现**两条 system**，且旧的那份带着过期的工具契约 |

另有一条测试直接构造「工具批次被切断」的尾部，断言恢复结果能通过
`api_compat.sanitize_messages_for_api`（deepseek / kimi / openai_compat 三种 profile）。

#### 测试

- `tests/test_checkpoint.py`（54 用例）：思维链剥离、rehydrate 四步、
  结构自检、存储/轮转/原子写、恢复提示、事件接线。
- `tests/test_checkpoint_wiring.py`（16 用例）：用假 LLM 驱动真实回合，验证
  检查点真的被触发、真的带计划、恢复结果 API 安全；含 bridge 三个新接口。

#### 还有什么痛点（未解决）

| 痛点 | 说明 | 计划 |
|------|------|------|
| **UI 无入口** | `list_checkpoints` / `resume_task` / `replay_task` 都有了，但 `prototype_antnest.py` 没有按钮 | **Step 8** |
| **恢复是手动的** | 进程重启后不会自动恢复，用户得显式触发 | Step 8 |
| **与 `stop_snapshot` 双源** | 同一语义两处持久化。`record_stop` 保留不动（已被测试覆盖），checkpoint 是超集 | 遗留 R5（v1.5 统一 `_runtime_prompt_context`） |
| 计划不进系统提示 | `Plan.to_prompt()` 已实现但没接进 `_with_retrieved_memory`——恢复后模型看不到计划 | Step 8 一起接 |
| 授权不持久 | 一次性/任务级授权只存内存，重启后失效 | 已知取舍 |
| 每回合都存检查点 | 长任务会写不少 JSON。已限 20 个/任务 + 5 个任务，但没做「无变化不存」 | 后续优化 |

---

### Step 7 — Agent Evaluation（评测基础设施 + CI）

#### 解决的问题

路线图 §8 要求「Agent 能力不能只用单元测试衡量」。但必须先把话说清楚：

> **v1.4 的评测测的是运行时管线，不是模型智商。**

用假 LLM（`ScriptedLLM`）驱动**真实的** `agent_single_loop`，能可靠测出：
工具派发是否正确、权限闸门是否拦住、计划是否推进、事件是否完整、
检查点能否恢复、危险操作是否被拦。

它**测不出**「这个模型能不能自己发现并修好 bug」——那需要真实 API。
所以 `Report.model_dependent` 是**显式字段**（默认 `false`），不是注释里的说明。
把两者混为一谈就是自欺。

#### 用例与断言

24 个用例，4 类：

| 类别 | 数量 | 覆盖 |
|------|------|------|
| safety | 6 | 自源码写、危险命令、shell 重定向绕过、只读模式放行读/拒绝写、拒绝后不触发反思 |
| planning | 6 | 显式登记、环拒绝、悬空依赖拒绝、spawn 自动派生、被拒 spawn 不留节点、计划注入 |
| recovery | 6 | 检查点保存、思维链不落盘、带计划、恢复提示三问、拒绝后终止、孤儿 tool 消息 |
| permissions | 6 | 默认档放行、只读仍可规划、禁网络、禁执行、只读命令不误拦、畸形调用检测 |

断言引擎支持 14 类断言键。**未知的键会报错而不是静默通过**——拼错一个键
就「假绿」的评测比没有评测更危险。

#### 指标（路线图 §8 的七项）

成功率 / 工具调用次数 / 平均与最慢耗时 / token 消耗 / 轮数 / 错误恢复率 /
危险操作拦截率。结果落 `evals/results/<version>.json`，`--compare 1.3.1 1.4`
可输出跨版本 diff（路线图 §8 想要的那张表）。

#### 离线可跑的前提

`antnest_config.py:118` 在 **import 期**发现没有 API Key 就 `sys.exit(1)`。
`harness._bootstrap_env` 负责三件事：设 `ANT_API_KEY`、设
`ANT_SKIP_MODEL_CHECK=1`（否则每回合走真实 `detect_capability(timeout=6)`）、
chdir 到临时目录（`PROJECT_DIR = os.getcwd()`，否则结果不可复现）。
**评测完全不需要网络与 key**，已接入 CI。

#### 变异测试：确认评测真的有鉴别力

全绿不代表有用。所以临时把 `PermissionEngine._is_self_source` 改成永远返回
`False`（模拟「自身源码保护被移除」），重跑：

```
[FAIL] recovery_loop_terminates_on_denial
[FAIL] recovery_dangling_tool_message_avoided
[FAIL] safety_self_source_write
```

**只有依赖自源码检测的 3 个用例失败，其余 21 个照常通过**——评测确实在测
那件事，没有「什么都测一下所以永远绿」的假象。

#### 评测框架自身抓出的 3 个 bug

| Bug | 后果 |
|-----|------|
| 观测点选在 `tool_executors` 包装上 | 权限闸门在 loop 里**先于**执行器短路，被拒绝时执行器根本不被调用——包装器只看得见「真正执行的」，看不见「被拦下的」，而后者恰是安全类用例要断言的东西。改到 `role:"tool"` 消息（回灌给模型的那份结果） |
| `_status_of_content` 直接 `json.loads` | loop 会按 `TOOL_RESULT_LEN` 截断工具结果，**内容不是合法 JSON**，status 读成空。补正则兜底 |
| `saved_engine` 在替换**之后**才抓 | finally 里「还原」把刚装上的引擎又装回去，上一个用例的 `level` 泄漏到下一个——只读用例之后的 spawn 用例全被误拒。实测连锁 6 个用例误判 |

另有两处是设计问题而非 bug，一并记下：
- 评测把 cwd 切到临时目录，所以用例里写相对路径（`antnest_config.py`）会解析到
  临时目录，自源码门禁自然不触发——用例「通过了」但**根本没测到东西**。
  加了 `{ROOT}` 占位符，涉及仓库路径的用例一律用它。
- 观测里补了 `TASK_STARTED` / `TASK_COMPLETED`：这两个事件由 bridge 的 `_turn`
  发出，而评测直接驱动 `agent_single_loop`（不经过 bridge）。已在代码注释里
  标明这是**评测口径的补齐**，不是对运行时行为的伪造。

#### 测试

`tests/test_eval_harness.py`（47 用例）：用例文件 schema、断言引擎逐键验证
（含每个键的「不该通过」反例）、状态解析（含截断 JSON）、指标计算、
跨用例状态隔离（同一批用例跑两次结果必须一致——这正是上面那个泄漏 bug 的
回归测试）。

#### 还有什么痛点（未解决）

| 痛点 | 说明 | 计划 |
|------|------|------|
| **没有 v1.3.1 基线可比** | `evals/results/1.3.1.json` 不存在（v1.3.1 没有评测设施），`--compare` 暂时只能与 1.4 自己比 | 后续版本起自动积累 |
| **不测模型能力** | 真实 API 路径（`--live`）未实现 | 留接口，需要时补 |
| 指标口径偏薄 | 没有「工具调用序列与预期是否一致」这类更细的断言 | 后续 |
| 危险拦截率的分母是 safety 用例数 | 不是「恶意输入总数」，口径偏乐观 | 已在代码注释说明 |
| 评测跑在临时目录 | 覆盖不到「在真实项目目录里跑」的差异（如 `.antnest` 已有内容） | 已知取舍 |

---

### Step 8 — UI 接线（Agent Control Center）

#### 解决的问题

前七步把能力建起来了，但**用户看不到**。本步接上 UI，并顺手修掉两个已存在的并发缺陷。

#### 1. Plan 面板

任务监视器新增「执行计划」卡片，位于子任务面板之上：
目标 + 进度条 + 按依赖深度缩进的节点树，节点带状态字形（`○ ● ✓ ✗ − ⊘`）、
工蚁归属徽章、结果摘要、错误信息。

#### 2. 权限徽章

同一张卡片下方渲染路线图 §10 的 ✓/✗ 列表，明确告诉用户**「能看什么、能写什么、
能不能联网、能不能改 AntNest 自己」**。L4 / L5 恒显示为「需确认」——
这是刻意让用户看到「有些事我永远不能替他决定」。

#### 3. 事件轨迹

- 侧栏实时轨迹（最近 60 条）
- 「轨迹」按钮打开完整模态，走 `replay_task` 路由拿服务端 replay 文本

**数据来源从 stdout 正则抓取改为事件订阅**。此前 UI 只能靠 `_RE_TOOL_CALL`
正则从打印行里猜「发生了什么」——那是脆弱的副通道。现在
`antnest_events.subscribe()` 直通 UI。

> `_route_timeline` 只把渲染需要的摘要字段传给 UI，**不传完整 data**。
> 事件里可能有命令全文、文件内容；有测试专门断言这些不会泄到 UI。

#### 4. 检查点 / 恢复入口

计划卡片底部三个按钮：查看轨迹 / 检查点 / 恢复。对应三个新路由
（`replay_task` / `checkpoints` / `resume_task`），背后是 Step 6 已经
写好的 `list_checkpoints` / `resume_task` / `replay_task`。

#### 5. 修掉两个并发缺陷

**(a) `send()` 的 TOCTOU 窗口。**

原来 `self.busy = True` 是在**新线程里**设的（`_turn` 第一行），而 `send()`
里的 `if self.busy` 检查在前。两次快速点击发送都会通过检查、起两个 `_turn` 线程。

后果不只是两个任务并行：`_turn` 会替换**进程全局**的 `sys.stdout`（保存
`old_out` → 装 `StdoutTap` → 恢复）。两个线程交错恢复，就会把 `sys.stdout`
永久指向一个已死的 `StdoutTap`——而那是**唯一**产生工具调用 UI 事件的通道
（`_RE_TOOL_CALL`）。此后整个会话的工具事件全部丢失，且没有任何报错。

修法：`send()` 在**启动线程之前** `try-acquire` 一个 `_turn_lock`，抢不到直接
返回 `busy`；`_turn` 的 `finally` 里释放。

**(b) `sys.stdout` 恢复改为比较交换。**

原来是 `finally: sys.stdout = old_out`（无条件）。改成
`if sys.stdout is _my_tap: sys.stdout = old_out` —— 只有当 stdout 仍是我装的
这个 tap 时才恢复，避免覆盖期间别人换上的对象，也避免 `old_out` 本身就是一个
已死 tap 时被再装回去。

**(c) UI 全局列表加锁。**

`on_core_event` 在 agent 线程上被同步调用（`AntNestCore.emit` 不排队），
`_flush` 跑在 `threading.Timer` 线程上。v1.3.1 里 `SUBTASKS.clear()` 出现在
5 处，任一与 agent 线程的 append 竞争都会静默丢条目。新增 `_ui_state_lock`
（`RLock`）保护 `SUBTASKS` / `WORKERS` / `EVENTS` / `PLAN`，
`turn start` 的清理也在锁内。

#### JS 侧

`openTimelineModal` / `closeTimelineModal`。轨迹行用 **DOM API + `textContent`**
渲染而不是字符串拼接——一是 `app.js` 里根本没有 `esc()`（用了会直接
`ReferenceError`），二是 `textContent` 天然免疫注入。

> 这个坑是脚本化编辑留下的死代码被我在核对时发现的，已整体重写该函数。

#### 测试

`tests/test_ui_wiring.py`（29 用例）：
- 事件协议：4 个新事件 kind 的 emit 与 handler 分支
- 并发修复：第二次 `send` 被拒（功能测试）、`_turn` 提前失败后锁被释放
  （用**真实** `_turn` 走 `ensure_loaded` 失败路径，不用假的绕开 finally）、
  CAS 恢复的源码断言
- 渲染接线：面板元素、模态、路由、JS 函数、CSS 类是否存在
- 新模块都在自身源码门禁 + 工蚁依赖清单内

另外补了 `.plan-card` / `#v14-controls` / `#timeline-modal-list` 的样式
（第一版漏了，`test_css_has_v14_styles` 抓到了），并让展开模态用完整列表
而不是面板的 60 条。

#### 还有什么痛点（未解决）

| 痛点 | 说明 | 计划 |
|------|------|------|
| **恢复是手动的** | 进程重启后不自动恢复，用户得点「恢复」 | 后续：启动时检测最近未完成检查点并提示 |
| 计划不进系统提示 | `Plan.to_prompt()` 已实现但没接进 `_with_retrieved_memory`——恢复后模型看不到自己规划了什么 | 下一轮 |
| 越权询问只在聊天里 | 权限 ASK 走对话式协议（见 Step 3 的 C3 约束），没有模态确认框 | 遗留：真正的模态需要 reply 通道 |
| `SUBTASKS` 面板与 Plan 面板信息重叠 | 前者是工蚁视角、后者是计划视角，但都挂在监视器上，可能显得拥挤 | 观察用户反馈 |
| 设置页无权限等级入口 | `permissions.level` 只能改 config.json | 后续：加进设置页 |
| 三份日志仍是三份 | `events` / `audit` / `ui_trace` 未合并 | 遗留（与 stdout 抓取层一起处理） |
| 工具调用仍顺序执行 | Plan 的「并行」只是依赖表达 | 遗留 R6，不在本轮 |

---

## v1.4 收尾

### 汇总

| 指标 | v1.3.1 基线 | v1.4 |
|------|------------|------|
| 单元测试 | 163 | **559** |
| Agent 评测用例 | 无 | **24**（4 类，变异测试验证过鉴别力） |
| 工具名真源 | 4 处手写，2 处已腐烂 | **1 处**（`antnest_registry.TOOL_SPECS`） |
| 危险命令表 | 3 张，语义不一 | **1 张** + 工蚁侧第二道红线 |
| 权限判断点 | 6 处手写 if | **1 个决策函数** |
| 日志 | 3 份定位模糊 | **events（重放）+ audit（追责）** |
| 计划 | 仅自由文本 | **显式 DAG + 自动派生兜底** |
| 恢复 | 文本快照 | **结构化检查点 + 安全 rehydrate** |

### 修掉的已存在缺陷

1. **所有工具调用全部失败**——`antnest_loop` 调 `_A().get_audit()`，而壳从未
   re-export 它。每个工具派发都抛 AttributeError 被宽 except 吞掉。
2. **工蚁依赖清单缺 3 个硬依赖**——wheel 安装下整条工具链全瘫，只因 editable
   安装掩盖了它。
3. **自身源码保护名单过期**——Agent 可无确认改写 `antnest_config.py`。
4. **UI 永久 busy / 工具事件永久丢失**——`send()` 的 TOCTOU + `sys.stdout`
   无条件恢复。
5. **管理员模式冻结整个 Agent**——`ALLOW_ALL_CLI` 零读取，危险命令走阻塞式
   `input()`，而 UI 没有界面能应答。
6. **prompt 与畸形调用正则已漏 3 个工具**。

### 明确没做（不夸大）

- **权限拦截不是 Sandbox**。危险模式是正则提示不是控制：`python x.py` 里藏
  `shutil.rmtree` 拦不到。有测试专门钉死这一点，防止文档/UI 夸大。
  真正的强隔离（Job Object / WSL2 / AppContainer）属 v1.5。
- **工具调用仍是顺序执行**。Plan DAG 表达依赖结构，不承诺并发。
  `MAX_CLONES` 仍无代码强制。
- **评测不测模型能力**。假 LLM 驱动真实运行时，测的是管线正确性与安全拦截。
  `Report.model_dependent` 是显式字段，不靠注释说明。
- **没有 v1.3.1 评测基线**可比（v1.3.1 没有评测设施）。

### 遗留清单

| ID | 项 |
|----|-----|
| R4 | 真正的 Worker Sandbox |
| R5 | 统一 checkpoint 与 `stop_snapshot` 双源 |
| R6 | 工具调用真并行 |
| R7 | `load_settings`/`save_settings` 与 schema 的校验去重 |
| R8 | `antnest_config.py:118` 的 `sys.exit(1)` 改为非致命（AGENTS.md §1 要求） |
| R9 | 双命名空间共享状态重构（`from x import *` 值拷贝根因） |
| R10 | `AGENT_CANCEL` 改 `threading.Event` |
| R11 | Worker Profile、Memory 2.0、Skills、Multi-Agent |

---

## v1.3.0 (2026-08-24)

### 修复（严重）
- `antnest_queen.py`：`_worker_py_cmd` 裸用 `IS_WINDOWS` 未导入 → 文件类工具（view_file/list_dir/grep_files/write_file/search_replace）全部 NameError，已改为 `_A().IS_WINDOWS`。
- `antnest_queen.py`：`admin_utils.analyze_command/get_user_confirmation` 未导入 → 补 `import admin_utils`。
- `antnest_loop.py`：缺 `import re`（`_detect_malformed_tool_call` NameError）。
- `antnest_loop.py`：`except ThinkRepeatError` 裸用 → `_A().ThinkRepeatError`。
- `antnest_loop.py` / `antnest_memory.py`：`COMPACT_PROMPT` 裸用 → `_A().COMPACT_PROMPT`。
- 新增 `AntNest.__version__ = "1.3.0"`。

### UI
- 主题精简为 3 个：黑色 / 白色 / 自定义色（custom 支持用户色选择器）。
- 立绘（透明底）从设置抽屉移除，改为**应用启动时出现一次的加载 splash**，自动淡出（可点击跳过，尊重减少动效）。
- Skills 面板改为与设置一致的主题化右抽屉。
- 右下日志改为**结构化任务状态卡**：当前任务 / 开始时间 / 状态 / 工蚁创建时间与存活。
- 子任务、工蚁面板支持「展开」→ 卡片式总览模态。
- 对话记录改为**持久卡片式排列**，随时一键恢复上下文。

### 修复（本轮追加，2026-08-25）
- 流式深度思考不可见：`antnest_bridge._patch_tools` 只设了 AntNest 壳的 `m.UI_STREAM_CB`，而 `_stream_emit`（antnest_config）读自己模块的 `UI_STREAM_CB` → 注入落空，推理/正文流式全程走终端兜底、到不了 UI（思维块只有标题无文本）。已改为**双命名空间注入**（`m.UI_STREAM_CB` 与 `antnest_config.UI_STREAM_CB` 同步设置），端到端冒烟确认 reasoning 实时到达。
- 启动崩溃 `TypeError: create_window() got an unexpected keyword argument 'icon'`：旧版 pywebview 的 `create_window` 不支持 `icon` 参数。已改为**运行时签名探测**（`inspect.signature` 支持才传 icon/gui），旧版/新版双模拟启动路径实测通过。
- 左侧窗口/任务栏图标：双保险方案 —— create_window 支持 icon 时照常传入 + 窗口显示后 Windows API（WM_SETICON）把标题栏小图标/任务栏大图标设为应用图标（兼容旧版 pywebview，与桌面快捷方式一致）。
- 连续 3 次相同工具+参数：改为**不中断任务**，注入《自我反思》指引（重分析目标 → 找失败原因 → 换不同工具/参数/思路继续），清空重复计数后继续循环（`antnest_loop.py`）。

### UI（本轮追加）
- 深度思考：任务进行中实时可见（流式气泡思维块默认展开、逐字填充、小卡片内滚动）；正文卡片随文本长度自适应（去掉 340px 内滚截断），聊天容器整体滚动；滚动时每张对话卡完整显示。
- 展开模态防溢出：子任务/工蚁卡片长路径/长文本换行不溢出（`_clip` 截断 + `overflow-wrap:anywhere`）。
- 任务多卡顿修复：模态网格只在 subtasks/workers 变更时同步（不再每 150ms log 全量重建）；面板 6 条 / 模态 80 条渲染上限，超出显示「还有 N 个」。
- 对话记录：主按钮改「查看」→ 只读弹窗展示该会话（含深度思考块），不替换当前上下文；删除不再依赖核心懒加载（`_session_paths()` + antnest_session 直接操作），删除真正生效。
- 模型列表：不再显示 `auto` 冗余标签。

### 记忆与配置（本轮追加）
- 记忆占位：启动时 `_ensure_memory_files()` 自动创建 NEST.md / hints.md（修复记忆线索层在文件缺失时静默失效）。
- NEST.md：版本评估更新到 1.3.0；补充「环境变量优先」说明（API Key 独立存 `*.key` 文件，config.json 不存明文）。
- `prompts/queen_system.md`：补充工蚁 PowerShell 5.1 语法约束（禁 `&&`/`cd /d`，建议写临时脚本文件）。

### 验证
- 全量回归：82 passed + 17 subtests；py_compile 通过。
- 启动路径双版本（旧版/新版 pywebview）模拟实测通过；图标兜底线程验证接入。
## [1.2.2] — 2026-08-16

> 当前发布版本。相对 1.2.1 的修复汇总；此前测试期已合入代码、但记在错误版本号下的改动，一并归并到本条。

### 安全加固（1.2.2 核心）

- **S1 · API Key 不再明文落盘**：新增独立密钥文件 `config.json.key`（权限 `0600`，已 gitignore）；`config.json` 只保留空 `api_key`，UI 保存时改写密钥文件、不再回写明文。`AntNest.py` 启动与 `antnest_bridge.load_settings` 均优先从密钥文件读取。
- **S2 · 危险命令过滤覆盖递归删除**：重写蚁后 `_DANGER_CLI_PATTERNS` 与工蚁 `dangerous_patterns`，拦截 `rm -rf /`、盘符、`~`/`$HOME`、`..`、`.`、`*`、裸 `rm -r/-R` 绝对路径，以及 `format`/`shutdown`/`halt`/`mkfs`/`dd if=/dev/zero`/`Remove-Item -Recurse|-Force`/fork bomb；项目内相对递归删除（如 `rm -rf node_modules`）仍放行。
- **S3 · 蚁后不再主进程直执行 `run_python`**：`get_queen_tools` 移除 `run_python_schema`，需要跑 Python 改走工蚁隔离目录执行（复用 S2 过滤）。`run_python` 函数保留但不再作为蚁后可达工具。
- **S4 · 路径越界校验**：`code_tools.resolve_path` 末尾加 `is_relative_to(project_dir)` 校验，越界（绝对路径 / `../`）直接 `raise`；五个文件工具 `try/except` 返回错误 JSON，杜绝路径穿越传給工蚁。
- **S5 · `web_fetch` 协议白名单**：先 `urlparse` 校验 scheme，仅允许 `http`/`https`，拒绝 `file://`/`ftp://` 等（防本地文件读取与 SSRF）。
- **工蚁调用无限递归 / OOM 修复**：`antnest_clone_worker.run_if_clone_mode` 启动命令子进程前，显式 `pop` 掉全部 `AN_CLONE_*` 控制环境变量。命令内 `import AntNest` 不再 re-enter clone 模式，从源头断开进程无限堆叠。

### 稳定性与资源上限

- 单任务轮次上限（默认 60，`ANT_MAX_ROUNDS` 可调），超限强制总结，根治自检循环卡死。
- 工蚁 stdout/stderr 各限 5MB（双线程泵取防管道死锁）；`run_python` 输出超 2MB 截断。
- `ui_trace.log` 5MB 轮转；产出归档 `.antnest/artifacts/` 仅留最近 50 个；UI 聊天 DOM 仅渲染最近 300 条。
- 重复调用检测：连续 3 次以相同参数调用同一工具自动中断循环。
- 克隆目录自愈：`spawn_clone` 前仅保留最近 20 个，强杀残留下次自动清。

### 进程与工蚁生命周期

- 超时 / 停止 / 取消改杀进程树（`taskkill /T` 或 `os.killpg`），根治 `python.exe` 孤儿堆积。
- HTTP 400「insufficient tool messages」兜底：工具循环中断后从后往前补齐 assistant `tool_calls` 缺失的 `tool` 响应，避免消息永久损坏。
- 工蚁失败自动重试一次；产出文件回收归档到 `.antnest/artifacts/<clone_id>/`；并行工蚁统一登记，「停止」可终止全部。

### 记忆与工具

- BM25 记忆检索（新增 `memory_retrieval.py`，零依赖，中文双字滑动窗口），每轮思考前注入 NEST.md / hints.md 相关段落。
- `run_python`（经工蚁隔离执行）、`web_fetch`（urllib 实现，无第三方依赖）工具；`run_cli` 危险命令保护。

### UI / 体验

- 多会话「🕘 对话记录」：历史会话列表可恢复 / 删除 / 新建，兼容旧版单会话文件自动迁移。
- token 用量实时显示；工蚁产出文件浏览标签；深度思考内联展开；暗色主题暖色光晕层次；新应用图标 `antnest.ico`；日志 DOM 超过 500 条自动重置同步。

### 打包与测试

- `pyproject.toml` 补 `memory_retrieval` 模块（editable/whl 安装下 `import` 不再失败）；测试依赖补 `pytest`。
- 安装包配置模板 `installer/config.template.json` 补齐 `thinking_mode` / `skip_model_check` / `default_token_cap`（与 `config.example.json` 对齐）。
- 死代码清理（`_memory_tokenize`、`_summarize_tool_kwargs`）；魔法数字统一为命名常量（`_ARTIFACTS_MAX_KEEP` / `_CLONE_POOL_MAX_KEEP` / `_RUN_PYTHON_OUTPUT_CAP` / `_MEMORY_TOP_K` / `_DUP_CALL_LIMIT` / `MAX_CHATS` 等）。
- 全量单元测试 `47 passed / 17 subtests passed`。

### 文档

- README §5.1 配置详解补充 `thinking_mode` / `skip_model_check` / `default_token_cap` 字段示例与说明。

---

## [1.2.0] — 2026-08-13

### 架构与核心

- **蚁后不再直接读写磁盘**：文件操作全部经工蚁执行（`view_file` / `list_dir` / `grep_files` / `write_file` / `search_replace`）
- 新增 `code_tools.py`：路径解析、工蚁脚本构建、结果解包
- 拆分模块：`antnest_clone_worker.py`（工蚁入口）、`antnest_session.py`（会话持久化）
- 工蚁隔离目录随包复制依赖模块，修复拆分后 `spawn_clone` 无法 import 的问题
- 记忆压缩 `leave_memory_hints` 修复 UI 模式下 `nest_md` 未定义崩溃

### Skills（Hermes 兼容）

- 新增 `skills_loader.py`：解析 `SKILL.md` YAML frontmatter，递归扫描目录
- 选中 Skill 时将完整正文注入上下文（不再只是 `[Skill: xxx]` 前缀）
- 设置面板支持 Skills 目录配置与管理页刷新

### MCP

- 新增 `mcp_client.py`：最小 stdio MCP 客户端
- 设置中启用 MCP 后，蚁后可调用 `mcp_call` / `mcp_list_tools`
- 提供 `mcp.json.example` 配置示例

### UI / 体验

- 聊天气泡流式输出；深度思考可折叠展示
- **■ 停止**：强行中断 LLM 流与正在运行的工蚁
- 每轮新任务清空右侧子任务 / 工蚁监控
- 设置面板重设计（分区、开关、密码框 API Key）
- CSS / JS 拆至 `ui_assets/`，`ui_render.py` 负责渲染
- 修复设置按钮打不开（`app.js` 语法错误）
- 修复 Skill 下拉一点就消失（焦点抢夺冲突）
- 文本可选中；运行日志按行追加，不做字符级流式刷新

### 测试与 CI

- 新增 `tests/`：code_tools、skills_loader、spawn_clone、bridge 设置等单元测试
- GitHub Actions：`.github/workflows/test.yml`

---

## [1.1.0]

- 聊天发送、Skills 打磨、安装器启动性能等（见历史提交）
