# AntNest v1.3.0 Release Notes

> 发布：2026-08-25 · Tag：`v1.3.0` · Commit：`2fe5bb6`（main）
> 安装包：`AntNest-Setup.exe`（单用户安装到 `%LOCALAPPDATA%\AntNest`，无需管理员）

---

## 一、这次更新了什么（按模块）

### 1. 严重 Bug 修复（重构拆分遗留的 import 缺失，5 处）

| 位置 | 问题 | 修复 |
|---|---|---|
| `antnest_queen.py` | `IS_WINDOWS` / `admin_utils` 未导入 → 文件类工具全 NameError | → `_A().IS_WINDOWS` / 补 `import admin_utils` |
| `antnest_loop.py` | 缺 `import re`、`ThinkRepeatError` 裸用 | 补 import / → `_A().ThinkRepeatError` |
| `antnest_loop.py` / `antnest_memory.py` | `COMPACT_PROMPT` 裸用 | → `_A().COMPACT_PROMPT` |
| `AntNest.py` | 无版本号 | 新增 `__version__ = "1.3.0"` |

### 2. Agent 行为升级

- **重复调用自我反思**：连续 3 次同一工具 + 相同参数 → 不再「中断任务」，改注入《自我反思》指引（重分析目标 → 找失败原因 → 换不同工具/参数/思路继续），清空计数后继续循环。

### 3. 深度思考实时可见（核心体验）

- 修复流式思考不可见：`UI_STREAM_CB` **双命名空间注入**（bridge 之前只设 AntNest 壳，而 `_stream_emit` 读的是 `antnest_config` 模块变量 → 注入落空）。
- 思维块任务进行中**逐字实时流式显示**（小卡片内滚动）。
- 正文卡片**随文本长度自适应**（去掉 340px 内滚截断），聊天容器整体滚动。

### 4. UI 重构

- 主题精简为：黑色 / 白色 / **自定义色**（取色器 + 持久化）。
- 立绘 → 启动 splash（一次登场、自动淡出、可点击跳过、尊重减少动效）。
- 右下日志 → **结构化任务状态卡**（当前任务 / 开始时间 / 工蚁创建与存活）。
- 子任务 / 工蚁「展开」→ 卡片式总览模态；长路径 / 长文本**自动换行防溢出**。
- 任务多**不卡顿**：模态网格只在数据变更时同步（不再每 150ms 全量重灌）+ 渲染条数上限（面板 6 / 模态 80，超出显示「还有 N 个」）。
- 对话记录：主按钮改「**查看**」→ 只读弹窗（含思维过程）不替换当前上下文；**删除真正生效**（不再依赖核心懒加载）；保留「恢复上下文」。
- 模型列表去掉 `auto` 冗余标签。
- **窗口 / 任务栏图标** = 应用图标（签名探测兼容旧版 pywebview + Windows API `WM_SETICON` 兜底）。

### 5. 记忆与配置

- 启动自动创建 NEST.md / hints.md 占位（修复记忆线索层静默失效）。
- NEST.md 版本评估更新到 1.3.0。
- `prompts/queen_system.md` 增加工蚁 PowerShell 5.1 语法约束。
- API Key 独立存 `*.key` 文件（0600），config.json 不落明文。

---

## 二、采用的技术（按大类）

### 🧠 架构与工程

| 技术 | 应用点 |
|---|---|
| 单文件 → 多模块拆分 | `AntNest.py` 拆出 `antnest_config / llm / loop / memory / queen / bridge / session / runtime_state / schemas / toolforge` |
| 蚁后/工蚁（Queen/Worker）架构 | 蚁后决策，工蚁隔离执行（独立进程 + 临时目录） |
| 事件驱动 UI | `antnest_bridge` 订阅事件 → 150ms 合并刷新 → 局部更新 DOM |
| 流式事件模型 | `phase: start / reasoning_start / reasoning / reasoning_end / content / end` |
| 依赖解耦 | 主题文件 `queen_system.md` 懒加载；配置 / 密钥分文件存储 |

### 🧠 AI / Agent 技术

| 技术 | 应用点 |
|---|---|
| 流式 LLM 调用 | OpenAI 兼容接口，`stream: true` 分块接收 |
| ReAct 循环 | `llm_chat_stream` ↔ 工具调用 ↔ 结果回传（`agent_single_loop`） |
| 深度思考（reasoning）透明化 | 把模型 `reasoning_content` 流式注入 UI（此前仅终端可见） |
| 自我反思策略 | 重复检测 → 注入反思指令 → 换方法（防死循环） |
| Rabin-Karp 滚动哈希 | 检测 thinking 重复内容，自动打断擦除 |
| 模型能力画像 | `/models` 探测 → 注入系统提示词（TOKEN_CAP / vision / reasoning） |

### 🧠 记忆技术

| 技术 | 应用点 |
|---|---|
| B+ 树思想索引 | `memory_tree.py` 层级索引记忆条目 |
| RAG 检索 | 每轮检索 NEST.md / hints.md 相关段落注入上下文 |
| BM25 关键词检索 | 中文双字滑动窗口（零依赖） |
| 动态用户画像 | 行为偏好 → 策略参数（verification / autonomy / clarification） |
| 停止快照 | `runtime_state.json` 保存决策摘要 / 证据 / 下一步 |

### 💻 工程 / 桌面技术

| 技术 | 应用点 |
|---|---|
| pywebview | 桌面窗口（webview / 浏览器双模式回退） |
| Windows API | `WM_SETICON`（标题栏/任务栏图标）、`taskkill /T`（进程树）、UAC 检测 |
| Inno Setup | 单用户安装包（`%LOCALAPPDATA%`，无管理员权限） |
| uv | 依赖管理（pyproject + uv.lock） |
| MCP 客户端 | stdio / HTTP（Streamable）/ SSE 三种传输 |

### 🔧 安全技术

| 技术 | 应用点 |
|---|---|
| 危险命令过滤 | 正则拦截递归删除 / 格式化 / 关机等模式 |
| 工蚁进程隔离 | 隔离目录 + 独立子进程 |
| 进程树强杀 | `taskkill /T` / `os.killpg` 防孤儿进程 |
| 输出限额 | stdout/stderr 5MB 泵取防管道死锁；TOOL_RESULT_LEN 截断 |
| 密钥隔离 | 独立 `.key` 文件（0600），config.json 不落明文 |
| 路径越界校验 | 文件工具末尾 `is_relative_to(project_dir)` 防路径穿越 |

---

## 三、一句话总结

> **v1.3.0 = 架构重构落地（多模块拆分）＋ 深度思考从「黑盒」变「实时可见」＋ Agent 从「会卡死」变「会自我反思」＋ UI 从「堆功能」变「结构化不卡顿」＋ 记忆从「文件缺失静默失效」变「自动占位可检索」。**
> 全程用事件驱动、流式推送、B+ 树 / RAG 记忆、进程隔离这些本地方案的技术，让你的 AI 真正掌控你的电脑。

---

## 四、验证

- 全量回归：**82 passed + 17 subtests**；关键文件 `py_compile` 通过。
- 启动路径旧版 / 新版 pywebview 双模拟实测通过；图标兜底线程验证接入。

## 五、上传文件清单

| 文件 | 路径 | 大小 | SHA256 |
|---|---|---|---|
| AntNest-Setup.exe | `installer/out/AntNest-Setup.exe` | 5.91 MB | `29ce60c00db8ea88213672c167af1e161ec0392f2ff82730ed4b1cca03848170` |
| antnest-1.3.0-py3-none-any.whl | `dist/antnest-1.3.0-py3-none-any.whl` | 0.28 MB | `b18bf537fb07a4c54c001bc62c543ef1af7aed73d86891c7800708f2a30d13ac` |
| AntNest.exe | `AntNest.exe` | 0.31 MB | `63e9e098a6508ab4e4c3218e140005dfd7035e750d17c2d969d21c70f13abc6f` |