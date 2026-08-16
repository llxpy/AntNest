# 更新日志

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
