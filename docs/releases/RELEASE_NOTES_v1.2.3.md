# AntNest v1.2.3

> 发布日期：2026-08-21

## 新增功能

### 🐜 任务状态机与验证工蚁
- 新增 `task_manager.py`：任务全生命周期管理（pending → running → done → verified）
- `spawn_clone` 新增 `verify` 参数：设为 `true` 时自动派验证工蚁确认执行结果
- 新增 `get_task_status` 工具：蚁后可随时查看任务状态
- 返回结果自动注入 `task_id` 和 `task_status` 字段

### 🪟 窗口持久化与会话恢复
- **单实例锁**：防止多开，重复启动时弹窗提示
- **最小化到任务栏**：关闭窗口时最小化而非退出，后台持续运行
- **会话自动加载**：启动时自动恢复上次对话记录，打开即可继续
- 标题栏新增「⏻ 退出」按钮用于真正退出应用

### ⏱ 工蚁超时可配置
- 新增配置项 `agent.worker_timeout`（默认 300 秒）
- 支持环境变量 `ANT_WORKER_TIMEOUT`
- `spawn_clone` 的 `timeout` 参数默认使用配置值

## 错误修复

### 🌐 API 配置校验
- 启动时校验 Base URL：空值或缺少协议前缀时自动回退默认值并提示
- `detect_model_len()` 对无效 URL 安全回退，不再抛出 `ValueError`
- GUI 模型检测拒绝无效 URL，返回结构化错误

### 🖥 WebView2 稳定性
- WebView2 初始化失败时自动回退到浏览器模式，不再崩溃
- 失败详情写入 `.antnest/startup_error.log`，包含完整 traceback

### 🔧 MCP 服务端容错
- 单个 MCP server 初始化失败不再阻塞其他 server
- 新增 `server_status` 字段，追踪每个 server 的状态（ready / failed / starting）

## 工程改进

### 📦 发布流程
- 新增 `tools/release_check.ps1`：26 项发布前检查（版本同步、文件完整性、密钥泄露、测试通过）
- 版本号统一由 `pyproject.toml` 管理，构建脚本自动同步到 `installer/version.iss`
- GitHub Actions 先安装项目依赖再运行测试

### 📋 稳定性优化日志
- 完整记录见 `稳定性优化日志_2026-08-20.md`

## 安装方式

### 安装包（推荐）
下载 `AntNest-Setup.exe`，双击运行。

### 便携使用
```bash
git clone https://github.com/llxpy/AntNest.git
cd AntNest
uv sync
uv run python prototype_antnest.py
```

### 直接启动
双击项目根目录 `AntNest.exe`（ps2exe 编译的 GUI 启动器，无控制台窗口）。

## 系统要求

- Windows 10/11（64 位）
- Python 3.12+
- uv（首次运行自动安装）
- WebView2 Runtime（Windows 11 自带，Windows 10 需手动安装）

## 完整提交记录

```
d30d97e release: rebuild AntNest.exe launcher for v1.2.3
1027454 feat: singleton lock, minimize-on-close, session auto-load
6fb5160 feat: configurable worker timeout via config/agent/worker_timeout
90fb341 fix: validate Base URL on startup, fallback to default if invalid
45bd60e fix: log WebView2 init failure to startup_error.log
6ea160e release: AntNest v1.2.3
1ea5913 feat: task state machine and verification worker for spawn_clone
2e6e9a2 fix: MCP server status tracking and resilient load_config
64a2c8d fix: auto-fallback to browser mode when WebView2 init fails
df7dae0 chore: add release preflight check script
b8f0fa0 fix: harden model URL validation and CI dependencies
```
