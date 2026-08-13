# 更新日志

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
