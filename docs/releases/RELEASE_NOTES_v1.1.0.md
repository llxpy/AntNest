# AntNest v1.1.0

蚁后 / 工蚁架构的 AI 编程助手，带原生桌面界面（pywebview + WebView2）。
本版本聚焦**启动性能修复**与**桌面 UI 打磨**，并新增**内置更新检查**、**赞助作者二维码**与**设置弹窗体验优化**，无破坏性变更，分发方式与 v0.1.0 一致。

## 相对 v0.1.0 的变更

### 1. 启动性能修复（GitHub Issue：双击 AntNest.exe 每次等 ~30s 且必须联网）
根因：`installer/` 下 4 个 ps1 硬编码路径检测逻辑在常见环境误判，每次启动都重跑预检查。
- `uv` 检测补全路径：除 `%LOCALAPPDATA%\uv\uv.exe` 外，新增 `~\.local\bin`、`%LOCALAPPDATA%\uvx` 等回退，避免新版 uv 被误判为未装。
- `WebView2` 检测改为「文件系统存在性 + 多注册表键」（HKLM/HKCU × WOW6432Node/正常），兼容 Win11 中国版（本机装了但缺某注册表键）。
- 新增 marker 文件 `%LOCALAPPDATA%\AntNest\.prereq_ok`：预检查通过后写入，下次启动做轻量本地复检即跳过，不再每次联网重装。
- 涉及文件：`installer/ensure_prereqs.ps1`、`installer/launcher.ps1`、`installer/launch.ps1`、`installer/install_deps.ps1`。

### 2. 桌面 UI 打磨
- **思考状态透明提示**：聊天框上方小字透明显示蚁后 / 工蚁当前状态（拆解任务、派发子任务、工蚁执行中 / 归巢）。
- **图片输入 + 发送前 vision 检测**：
  - 支持 `Ctrl+V` 粘贴图片、点击图片按钮（SVG 图标）上传；粘贴同时兜底 `clipboardData.items` 与 `clipboardData.files`（WebView2 兼容）。
  - 发送前检测：必须「**已配置 API Key 且**模型支持图片」才放行。没填 key 或模型不支持（如 deepseek-chat）时，图片按钮**直接灰化禁用**，粘贴 / 上传 / 发送全部被拦截，hover 提示原因。
- **Skill 选择**：聊天框左侧下拉选择要附加的 Skill，发送时自动加 `[Skill: xxx]` 前缀。
- **Skills 目录配置更顺手**：设置页「Skills 目录」增加「浏览…」按钮（tkinter 文件夹对话框，缺失时优雅降级）；新增「管理 Skills」弹窗，列出 `skills_dir` 下 `.baim` 文件与含 `SKILL.md` 的子目录（名称 / 类型 / 路径 / 描述）。
- **Bug 修复**：修复设置页「Skills 目录」输入框误显示 `[<phtmlwin.El object ...>]`（子元素列表未展开）。

### 3. 安全模型（同 v0.1.0，无变化）
进程级隔离（非容器 / 虚拟机）：工蚁在独立子进程、临时目录执行，完即销毁。代码内置危险命令正则过滤。安全由「LLM 指令遵循 → 危险命令过滤 → 进程级隔离 + 执行即销毁」共同保障。工蚁不加载 LLM、不连 API，只作 Shell 执行器。

### 4. 配置（同 v0.1.0，无变化）
仓库内 `config.example.json` 复制为 `config.json` 并填入 key（或用环境变量 `ANT_API_KEY`）：
```json
{
  "api": { "base_url": "https://api.deepseek.com/v1", "model_name": "deepseek-chat", "api_key": "sk-你的密钥" },
  "agent": { "max_depth": 2, "max_clones": { "0": 10, "1": 5, "2": 3 }, "compact_threshold": 0.85, "tool_result_max_len": 8000 }
}
```
安装包内置**空 key 模板**，不携带任何真实密钥；首次运行后在设置界面填自己的 key。

### 5. 内置更新检查（仅提示，不自动下载）
- 启动后后台线程查询 GitHub Release `releases/latest`，与当前版本（读自 `pyproject.toml`）比较，发现新版本时顶部弹出提示条「发现新版本 vX.Y.Z（当前 v1.1.0）」+「查看更新」「忽略」。
- 「查看更新」在系统默认浏览器打开 Release 页；**不自动下载 / 执行**，规避未签名分发的供应链投毒风险。
- 离线 / API 限流 / 解析失败全部静默，不影响主流程；走 `urllib` 标准库，零新增依赖。

### 6. 设置弹窗全局关闭
- 关闭按钮从卡片标题行移到遮罩层右上角固定定位，视觉上更像「全局退出」。
- 额外支持**点击黑色半透明背景（遮罩）关闭**设置弹窗。

### 7. 赞助作者二维码弹窗
- 右上角按钮文案改为「赞助作者喵~~」，点击弹出本地收款二维码弹窗（暗面构建风格卡片，两列网格展示微信 / 支付宝收款码）。
- 收款图来自项目内 `Page/` 目录（随安装包分发，不依赖本机路径）；只读取文件名含 `wx`/`zfb`/`微信`/`支付宝` 等的图片，自动过滤无关 / 敏感文件。
- 打包已确认：Inno 安装包、uvx/whl（`[tool.setuptools.data-files]` + `MANIFEST.in`）、zip 三种分发均带 `Page/`。

## 已知限制
- 安装包 / uvx 分发**未签名**，无 SmartScreen 信任（首次运行点「更多信息 → 仍要运行」）。
- 第三方输入法候选悬浮窗在 WebView2 后端可能不弹（微软拼音开「兼容性 → 使用以前版本」即可；或 `ANT_WEBVIEW_GUI=cef` 换 CEF 后端）。
- 仅 Windows 桌面 UI（WebView2/CEF）；macOS/Linux 暂未验证。
- 单用户安装（`%LOCALAPPDATA%`），全机 / Program Files 安装未做。

## 完整文档
见仓库 `README.md`。
