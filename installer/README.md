# AntNest 安装包（Windows）

目标：**小包 + uv 前置**，不打包 Python 运行时（目标用户本就有 uv/Python）。
单用户安装到 `%LOCALAPPDATA%\AntNest`，无需管理员权限。暂不签名。

## 安装器做了什么

1. 把应用源码复制到 `%LOCALAPPDATA%\AntNest`（4 个 .py + pyproject.toml + uv.lock + antnest.ico）。
2. 在开始菜单 / 桌面建快捷方式，隐藏窗口调用 `launch.ps1`。
3. 首次安装时跑 `ensure_prereqs.ps1`：
   - **uv**：若未安装，自动装（Astral 官方脚本）。
   - **WebView2 运行时**：pywebview 在 Windows 的默认后端，若注册表无则下载 Evergreen 引导器静默安装（可能弹 UAC，允许即可）。
4. 首次启动 `launch.ps1` → `uv run --project … python prototype_antnest.py`：
   - uv 自动建 `.venv` 并从 PyPI 装 `pywebview`（**首次需联网**）。
   - 之后启动走已建好的 venv，离线也能开。
5. 用户数据（config.json / ui_config.json / .antnest / trace）落在 `%LOCALAPPDATA%\AntNest`，
   与源码同目录但升级时不覆盖（Inno `onlyifdoesntexist`）。

## 构建安装器

需要 [Inno Setup 6](https://jrsoftware.org/isdl.php)（免费）。

```bat
cd installer
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" AntNest.iss
```

产物：`installer\out\AntNest-Setup.exe`。双击即装。

> 未签名：用户首次运行会看到 SmartScreen「Windows 已保护你的电脑」→ 点「更多信息」→「仍要运行」。
> 签名方案见下方「后续」。

## dev 模式（仓库内直接跑，不变）

```bat
cd AntNest-master
uv run python prototype_antnest.py
```

没设 `ANT_INSTALLED=1` 时，config/ui_config/状态仍写在**仓库目录**（保持旧行为），与安装版互不干扰。

## 关键代码改动（坑2 修复）

- `AntNest.py`：`_config_path` 现读 `ANT_CONFIG_PATH` 环境变量，未设则回退 `THIS_DIR/config.json`。
- `antnest_bridge.py`：`CORE_CFG / UI_CFG / TRACE_DIR` 读对应环境变量（`ANT_CONFIG_PATH / ANT_UI_CONFIG_PATH / ANT_TRACE_DIR`），未设回退本地。
- `prototype_antnest.py`：顶部新增 `ANT_INSTALLED==1` 开关，安装版启动时把上述 env 指到 `%LOCALAPPDATA%/AntNest`。

## 中文输入法（IME）候选窗问题

和蚁后对话的输入框是普通 `<input>`，中文能正常打；但 pywebview 在 Windows 默认走
**edgechromium（WebView2）** 后端，有个已知缺陷：宿主窗口不向前端 IME（TSF）转发光标坐标，
导致**微软拼音的候选悬浮窗可能不弹 / 弹在错误位置**。JS/CSS 修不了这个（不是 DOM 层问题）。

已做的两层处理：

1. **Enter 拦截修复（已合入）**：`keydown` 监听加了 `if(e.isComposing || e.keyCode===229) return;`，
   拼音合成中按 Enter 不再把半截拼音发出去，交给 IME 正常上屏。
2. **后端可切换（已留好开关，默认不变）**：`phtmlwin.Win` 现支持 `gui=` 参数，
   `prototype_antnest.py` 读环境变量 `ANT_WEBVIEW_GUI`。想换后端时只要启动前设：
   ```bat
   set ANT_WEBVIEW_GUI=cef
   ```
   可选值：`edgechromium`（默认，WebView2，现代轻量）/`cef`（CEF，IME 候选窗可靠）。

**CEF 的代价（重要，选前须知）**：`pywebview[cef]` 会拉 `cefpython3 66.1`（2021 年的 Chromium 66，
引擎很老），且其 wheel 是 `py2.py3-none-win_amd64`，**与 Python 3.13 ABI 不保证兼容**——
真要上 CEF，得把 `pyproject.toml` 的 `requires-python` 降到 `>=3.9,<3.12` 并把 venv 锁到 3.11，
且 venv 体积 +~150MB。对一个现代小包来说代价偏大。

**不换后端时的用户侧兜底（已验证有效）**：Windows「微软拼音」设置里开「兼容性 → 使用以前版本的微软拼音」，
让 IME 回退旧 GDI 渲染路径，候选窗就回来了（系统级开关，无需改代码）。**2026-08-09 用户实测：微软拼音开此开关后候选窗正常。**
注意：该开关是**微软拼音专属**，对第三方输入法（如用户另装的墨奇）无效——第三方输入法仍可能不弹候选窗（同一 WebView2 缺陷），
属已知限制；要么在该第三方输入法里找自己的兼容模式，要么在此 app 用微软拼音。

> 现状结论：默认保留 WebView2（现代、小、契合小包哲学），已修 Enter 拦截；IME 候选窗若在你机器上
> 不弹，优先试「旧版微软拼音」兼容开关；真要可靠 IME 再用 `ANT_WEBVIEW_GUI=cef`（自担老引擎+Python 降级）。

## 后续 / TODO

- **签名**：Windows Authenticode 证书（$70–300/年）或开源免费 Azure Trusted Signing / Sigstore；
  两套都可后续接入 CI 自动签。
- **全机安装（Program Files）**：用户提到为安全以后要装 Program Files。届时需把用户数据目录与安装目录明确分离
  （现在同目录，因为 LOCALAPPDATA 可写）；并把快捷方式/前置脚本的写权限理清。
- **Linux/macOS**：本期只做 Windows。跨平台可复用同一套 `launch.ps1` 思路（换成 sh / 处理 WebKit / webkit2gtk）。
- **PROJECT_ANT_DIR**：`AntNest.py` 里 `PROJECT_DIR = os.getcwd()`，会话/提示文件落在启动目录的 `.antnest`。
  安装版启动目录已设为安装根目录（可写），可接受；若要更干净可后续加 `ANT_PROJECT_DIR` 环境变量。
