# AntNest v1.1.0 · 发布草稿

> 本文件两份内容，打包发布时直接复制：
> 1. **【GitHub Release 正文】** —— 在 GitHub 起草 Release v1.1.0 时粘贴到描述框。
> 2. **【GitHub Issue 回复】** —— 回复那条「启动慢 + 假离线」的 issue，贴上后关掉。

---

## 【GitHub Release 正文】

### AntNest v1.1.0

蚁后 / 工蚁架构的 AI 编程助手，带原生桌面界面（pywebview + WebView2）。
本版本修了启动慢的 issue，并做了桌面 UI 打磨、内置更新检查、新增赞助入口。

#### 下载（Assets）
- **AntNest.exe**：解压后双击即开（无控制台），适合大多数 Windows 用户。
- **AntNest-Setup.exe**：Inno Setup 安装包，单用户装到 `%LOCALAPPDATA%\AntNest`，开始菜单 / 桌面生成快捷方式。
- **antnest-1.1.0-*.whl** + `uvx --from git+https://github.com/llxpy/AntNest antnest`：已装 uv 的开发者一行命令。

#### 相对 v1.0.0 的主要变更
- **启动性能修复（#issue）**：修掉每次双击 AntNest.exe 都等 ~30s 且强制联网的问题。原因是 `installer/` 下预检查脚本硬编码路径检测，在常见环境误判、每次重装依赖。现在 uv / WebView2 检测补全了路径与注册表键回退，并通过 marker 文件跳过已通过的预检查，离线也能秒开。
- **桌面 UI 打磨**：聊天框上方透明显示蚁后 / 工蚁思考状态；图片支持 `Ctrl+V` 粘贴与按钮上传，发送前检测模型是否支持图片（接口优先、模型名关键词兜底），不支持或无 API Key 时按钮直接灰化禁用；聊天框左侧可挂 Skill；设置页「Skills 目录」支持浏览选择，并新增「管理 Skills」弹窗。
- **内置更新检查（仅提示）**：启动后静默查 GitHub Release，发现新版本顶部弹提示条，点「查看更新」跳浏览器。不自动下载 / 执行，规避未签名分发的供应链风险。
- **设置弹窗全局关闭**：右上角固定 X + 点背景即可关闭。
- **赞助作者**：右上角「赞助作者喵~~」弹出本地微信 / 支付宝收款二维码（图片随安装包分发，不依赖本机路径）。

#### 配置
`config.example.json` 复制为 `config.json` 填入自己的 key（或设环境变量 `ANT_API_KEY`）即可。安装包内置空 key 模板，不携带任何真实密钥。

#### 已知限制
- 安装包 / uvx 分发未签名，首次运行有 SmartScreen 提示，点「更多信息 → 仍要运行」即可。
- 第三方输入法候选悬浮窗在 WebView2 后端可能不弹（微软拼音开「兼容性 → 使用以前版本」修复；或 `ANT_WEBVIEW_GUI=cef` 换 CEF 后端）。
- 仅 Windows 桌面 UI（WebView2/CEF）；macOS/Linux 暂未验证。

---

## 【GitHub Issue 回复】

@举报的同学 你好，问题已定位并修复，会在 v1.1.0 里发布，先回复下根因和验证方式：

**根因**
启动慢 + 假离线不是 UI 卡，而是 `installer/` 下 4 个预检查脚本（`ensure_prereqs.ps1` / `launcher.ps1` / `launch.ps1` / `install_deps.ps1`）硬编码了路径检测：
1. uv 只查 `%LOCALAPPDATA%\uv\uv.exe`，但新版 uv 默认装到 `~\.local\bin`，被误判为「没装」→ 每次重新触发 uv 安装（10~20s）。
2. WebView2 只查一个 HKLM 注册表键，Win11 中国版实际装了但缺那个键 → 被误判为「没装」→ 重新触发 WebView2 安装（~10s）。
3. `launcher.ps1` 每次启动无条件跑 `ensure_prereqs.ps1`，上面的误判叠加 → 每次都联网重装，没网就卡住 / 失败。

**修复**
- uv 检测补全 `~\.local\bin`、`%LOCALAPPDATA%\uvx` 等回退路径。
- WebView2 检测改为「文件系统存在性 + 多注册表键（HKLM/HKCU × WOW6432Node/正常）」。
- 预检查通过后写入 marker 文件 `%LOCALAPPDATA%\AntNest\.prereq_ok`，下次启动只做轻量本地复检即跳过，不再每次联网。

**验证方式**
拉 v1.1.0 的 Release（或 `git pull` 后 `uv run python prototype_antnest.py`）：
- 首次仍需联网装一次 uv + WebView2（正常）；
- 之后每次双击 AntNest.exe 应秒开、且离线可用。

如果你更习惯命令行，也可以直接用 `uvx --from git+https://github.com/llxpy/AntNest antnest`，每次都拉最新、无此问题。

麻烦你升级后帮确认一下，没问题我就关这个 issue 了，多谢反馈 🙏

---

*（本草稿由本地整理，发布前可按实际 Assets 文件名微调。）*
