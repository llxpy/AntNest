# AntNest v1.2.1

蚁后 / 工蚁架构的 AI 编程助手，带原生桌面界面（pywebview + WebView2）。
本版本聚焦**多模型 API 兼容**、**MCP 健壮性修复**与**大模型检测**，让 AntNest 不再只认 DeepSeek——Kimi / MiniMax / OpenAI 兼容服务均可稳定对话。

## 相对 v1.1.0 的变更

### 1. 多模型 API 兼容层（新增 `api_compat.py`）
之前不同大模型 API 参数不兼容：Kimi 拒绝空 assistant content、MiniMax 不支持 thinking 字段、模型列表接口差异导致校验误报。本版新增提供商识别与自适应：

- **提供商自动识别**：按 `base_url` 识别 DeepSeek / MiniMax / Kimi / OpenAI / 本地兼容（Ollama 等），无需手动配置。
- **参数自适应**：
  - Kimi：`temperature` 强制 1.0、自动关闭 thinking、发送前规范化 messages（空 assistant content 补占位符、剥离 `reasoning_content`）
  - MiniMax：自动关闭 thinking、跳过 `/models` 检测（部分服务该接口不可用）
  - DeepSeek：保留 `reasoning_content`，thinking 按 auto 适配
  - OpenAI / 本地兼容：按默认参数走
- **strict 消息键过滤**：Kimi / MiniMax / OpenAI 兼容服务发送前只保留 `role/content/name/tool_calls/tool_call_id` 五个键，杜绝多余字段被拒。
- 涉及文件：`api_compat.py`（新增）、`antnest_bridge.py`、`prototype_antnest.py`。

### 2. MCP 空问题修复
- MCP 配置文件不存在时**优雅跳过**（之前空配置会走加载流程导致报错/卡顿），并提示「复制 mcp.json.example 为 mcp.json，或在设置中关闭 MCP」。
- 空 `mcp_config` 设置不再触发异常路径。
- 涉及文件：`antnest_bridge.py`、`mcp_client.py`。

### 3. 大模型检测（设置面板「检测模型列表」）
- **后台拉取 `/models` 列表**：新增 `list_models_async`，设置面板可一键检测当前 API 下所有可用模型。
- **模型能力标注**：每个模型标注是否支持图片输入（vision），新增 MiniMax / abab / `vl-` / `-vl` 视觉关键词。
- **推荐参数随模型联动**：每个检测到的模型附带推荐配置（thinking / skip_model_check / 提示语），选择后自动应用。
- 涉及文件：`antnest_bridge.py`、`ui_assets/app.js`。

### 4. 配置新增
```json
{
  "api": {
    "base_url": "https://api.deepseek.com/v1",
    "model_name": "deepseek-chat",
    "api_key": "sk-你的密钥",
    "thinking_mode": "auto",
    "skip_model_check": false
  }
}
```
- `thinking_mode`：`auto` / `on` / `off`，按提供商自动适配，可手动覆盖。
- `skip_model_check`：跳过 `/models` 校验（部分服务该接口返回异常）。
- 安装包内置空 key 模板，不携带真实密钥；首次运行后在设置界面填自己的 key。

### 5. 测试与质量
- 新增测试：`test_api_compat.py`（169 行）、`test_mcp_client.py`（174 行）、`test_skills_bridge.py`（103 行）。
- 测试套件 **38 项全部通过**（3.97s）。
- CI 沿用 `.github/workflows/test.yml`，改动即时回归。

## 已知限制
- 安装包 / uvx 分发未签名，无 SmartScreen 信任（首次运行点「更多信息 → 仍要运行」）。
- 第三方输入法候选悬浮窗在 WebView2 后端可能不弹（微软拼音开「兼容性 → 使用以前版本」即可；或 `ANT_WEBVIEW_GUI=cef` 换 CEF 后端）。
- 仅 Windows 桌面 UI（WebView2/CEF）；macOS/Linux 暂未验证。
- 单用户安装（`%LOCALAPPDATA%`），全机 / Program Files 安装未做。
- `/models` 检测依赖服务端支持该接口；不支持的提供商（如部分 MiniMax 区域）会自动走 `skip_model_check`。

## 完整文档
见仓库 `README.md`。
