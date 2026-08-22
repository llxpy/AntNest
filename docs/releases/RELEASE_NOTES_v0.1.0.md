# AntNest v0.1.0

蚁后 / 工蚁架构的 AI 编程助手，带原生桌面界面（pywebview + WebView2）。

本版本首次提供**面向最终用户的分发方式**：用 uv 管理环境与依赖，不再把 Python 运行时打进安装包。

## 分发方式（三选一）

v1 主推 **B / C**（已装 uv 的开发者，零摩擦）；**A 仅作附加产物**，给没有 uv 的普通用户。

### B · 下载 Release 源码 zip，双击 AntNest.exe（推荐大多数用户）
- 解压后双击根目录的 **`AntNest.exe`**（原生启动器，无控制台窗口）。
- 首次运行自动装 uv + WebView2 运行时（若未装），再自动建环境装 pywebview（需联网一次，之后离线可开）。
- 运行时数据落到可写位置 `%LOCALAPPDATA%\AntNest`，不污染解压目录。
- 备用：`launch.bat`（控制台回退）、`launcher.ps1`（启动器源码）。`AntNest.exe` 由 `installer\build_launcher.ps1`（ps2exe）编译，需重新生成时运行它即可。

### C · uvx 一行命令（已装 uv 的开发者）
```bat
uvx --from git+https://github.com/llxpy/AntNest antnest
```
已 clone 仓库也可在仓库内：
```bat
uv run --project . antnest
```
`antnest` 是命令入口，等价于 `uv run python prototype_antnest.py` 且自动把状态写到 `%LOCALAPPDATA%\AntNest`。

### A · Windows 安装包（Inno Setup，给没有 uv 的普通用户）
本仓库 `installer/AntNest.iss` 可编译出 `AntNest-Setup.exe`（小包 + uv 前置，单用户装到 `%LOCALAPPDATA%\AntNest`）。
- 装完后开始菜单 / 桌面生成 **`AntNest.exe` 快捷方式**，双击即开，无控制台。
- **未签名**：首次运行有 SmartScreen 提示，点「更多信息 → 仍要运行」即可。
- 安装包内置**空 key 模板**，不携带任何真实密钥；首次运行后在设置界面填自己的 key。

## 配置
仓库内 `config.example.json` 复制为 `config.json` 并填入 key：
```json
{
  "api": {
    "base_url": "https://api.deepseek.com/v1",
    "model_name": "deepseek-chat",
    "api_key": "sk-你的密钥"
  },
  "agent": { "max_depth": 2, "max_clones": { "0": 10, "1": 5, "2": 3 }, "compact_threshold": 0.85, "tool_result_max_len": 8000 }
}
```
也支持其他 OpenAI 兼容 API（Ollama / vLLM 等）。

## 中文输入法
WebView2 后端在部分输入法下候选悬浮窗可能不弹（能打字、只是候选列表不显示）。解决：Windows「设置 → 时间和语言 → 语言 → 中文 → 微软拼音 → 键盘选项 → 常规 → 兼容性」打开「使用以前版本的微软拼音」，候选窗即恢复。该开关是微软拼音专属，第三方输入法不受影响（已知限制）；也可启动时设 `ANT_WEBVIEW_GUI=cef` 换 CEF 后端（体积更大）。

## 安全模型
进程级隔离（不是容器 / 虚拟机）：工蚁在独立子进程、临时目录执行，完即销毁。代码内置危险命令正则过滤（拒绝 `format`、`rm -rf /`、`shutdown` 等）。安全由「LLM 指令遵循 → 危险命令过滤 → 进程级隔离 + 执行即销毁」共同保障。工蚁不加载 LLM、不连 API，只作 Shell 执行器。

## 已知限制
- 安装包 / uvx 分发**未签名**，无 SmartScreen 信任。
- 第三方输入法候选窗不弹（见上）。
- 仅 Windows 桌面 UI（WebView2/CEF）；macOS/Linux 暂未验证。
- 单用户安装（`%LOCALAPPDATA%`），全机 / Program Files 安装未做。

## 完整文档
见仓库 `README.md`。
