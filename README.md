# AntNest

AntNest 是一个采用**蚁后 / 工蚁架构**（或蜂后 / 工蜂）的 AI 编程助手。你可以把它想象成一个蚁群：

- **蚁后（本体）**：只负责思考、规划、决策。蚁后从不亲自执行任何命令。
- **工蚁（复制体）**：蚁后需要干活时，会生成一只工蚁——把自身代码复制一份，派到隔离的临时目录里执行命令。工蚁干完活把结果交回来，然后被立即销毁。

本仓库在原始命令行版本基础上，新增了 **桌面图形界面**（基于 pywebview 的 WebView2 原生窗口）和 **Windows 安装包**（Inno Setup + uv 前置）。

```
┌───────────────────────────────────────────────┐
│                 蚁后（本体）                  │
│          LLM 推理 · 记忆管理 · 决策           │
│                                               │
│          "绝不触碰 Shell，只派工蚁"           │
└─────┬─────────────────┬─────────────────┬─────┘
      │                 │                 │      
 spawn_clone       spawn_clone       spawn_clone 
      │                 │                 │      
      ▼                 ▼                 ▼      
┌───────────┐     ┌───────────┐     ┌───────────┐
│  工蚁-1   │     │  工蚁-2   │     │  工蚁-3   │
│ 执行命令  │     │ 执行命令  │     │ 执行命令  │
│ 返回结果  │     │ 返回结果  │     │ 返回结果  │
│  × 销毁   │     │  × 销毁   │     │  × 销毁   │
└───────────┘     └───────────┘     └───────────┘
```

## 一、核心原理

### 1.1 为什么用蚁后 / 工蚁架构

传统 AI Agent（如 Claude Code、Cursor）的结构是：

```
LLM 推理 → 直接调用 Shell → 结果返回
```

这种模式的根本问题是：**Agent 和 Shell 之间没有隔离层**。命令执行错误可能污染运行环境，恶意命令可能突破安全边界。

AntNest 在两者之间插入了一层**一次性执行容器**：

```
LLM 推理 → spawn_clone（生成工蚁） → 工蚁调用 Shell → 结果返回 → 销毁工蚁
```

| 特性 | 传统 Agent | AntNest |
| --- | --- | --- |
| 执行隔离 | 命令在 Agent 进程中执行 | 命令在独立子进程中执行 |
| 环境保护 | 命令可能污染工作目录 | 工蚁 cwd 限制在临时目录，执行完目录即删 |
| 并行能力 | 通常串行 | 可同时派出多只工蚁并发执行 |

> **安全模型**：这是**进程级隔离**，不是容器 / 虚拟机沙箱。工蚁仍可通过绝对路径访问系统磁盘、通过 `cd ..` 跳出临时目录。代码内置危险命令正则过滤（拒绝 `format`、`rm -rf /`、`shutdown` 等），但无法覆盖所有恶意场景。安全由三层共同保障：**LLM 指令遵循 → 危险命令过滤 → 进程级隔离 + 执行即销毁**。

### 1.2 工蚁的完整生命周期

每只工蚁的一生只有五个阶段：`生成 → 执行 → 写结果 → 退出 → 销毁`。

1. **生成**：蚁后在 `.antnest/clone_pool/` 下创建一个 `clone_xxxxx` 目录，把 `AntNest.py` 复制进去。
2. **执行**：蚁后以子进程方式启动工蚁，通过环境变量传递命令和超时时间（工蚁检测到 `AN_CLONE_MODE=1` 后只执行命令、不加载 LLM）。
3. **写结果**：工蚁把执行结果（stdout、stderr、exit_code）写入 `result.json`。
4. **退出**：工蚁进程退出。
5. **销毁**：蚁后读取 `result.json` 后，调用 `shutil.rmtree` 彻底删除工蚁目录。

工蚁从被创建到被销毁通常只存活几秒到几分钟，没有任何持久化痕迹留下。

### 1.3 深度限制——防止无限复制

如果工蚁也可以生成工蚁，会不会无限递归？AntNest 有严格的深度限制：

```
depth=0   蚁后（本体）           可 spawn 最多 10 只工蚁
depth=1   工蚁                   可 spawn 最多 5 只子工蚁
depth=2   子工蚁                 可 spawn 最多 3 只子子工蚁
depth≥3   禁止 spawn_clone      降级为直接执行（run_cli）
```

每次 `spawn_clone` 调用时自动传递 `depth+1`。到达硬上限（默认 2，可在 `config.json` 中修改）后，工蚁降级为直接执行命令，任务不会中断，但不能再复制。

### 1.4 记忆系统——蚁后的长期记忆

AntNest 有三层记忆结构：

| 层级 | 文件 | 内容 | 更新时机 |
| --- | --- | --- | --- |
| 长期知识 | `.antnest/NEST.md` | 固化技能和规则 | 记忆压缩时 LLM 自主写入 |
| 记忆线索 | `.antnest/hints.md` | 索引和关键词 | 记忆压缩时 LLM 自主写入 |
| 会话记忆 | `.antnest/sessions/` | 完整对话历史 JSON | 每次对话自动保存 |

当 token 用量达到上限的 85% 时，蚁后进入"紧急状态"，整理关键信息写入记忆文件，提炼可复用技能写入 `NEST.md`，调用 `leave_memory_hints` 留下线索，对话历史重置但线索保留。这模拟了人类的**学习 → 固化 → 遗忘 → 通过线索回忆**。

### 1.5 环境探针——启动时的自我认知

每次启动自动收集三类环境信息注入系统提示：操作系统版本、工具链（Python / Node / Git / Docker 等的安装状态和版本）、当前目录文件列表（最多 100 条、2000 字符）。让 LLM 在第一轮就知道能调用什么工具、当前目录有什么文件。

## 二、桌面界面（新）

AntNest 现在带一个**原生桌面窗口**（不是浏览器标签页）：左侧和蚁后聊天下达任务，右侧实时看子任务 / 工蚁状态 / 运行日志，右上角可打开设置填 LLM key、切换主题与背景、配置 MCP / Skills。界面用 Windows 的 WebView2 内核渲染。

运行（需先装 [uv](https://docs.astral.sh/uv/)）：

```bat
uv run python prototype_antnest.py
```

首次运行 uv 会自动建虚拟环境并安装 pywebview（**需联网**）。之后离线也能开。

> **中文输入法提示**：WebView2 后端在部分输入法下候选悬浮窗可能不弹（输入法在合成、字能打，只是候选列表不显示）。解决：Windows「设置 → 时间和语言 → 语言 → 中文 → 微软拼音 → 键盘选项 → 常规 → 兼容性」打开「使用以前版本的微软拼音」，候选窗即恢复。这一开关是微软拼音专属，第三方输入法不受影响（属已知限制）。详见 `installer/README.md`。

## 三、快速开始

### 3.1 环境要求

- Python 3.13+（本项目用 uv 管理，会自动建环境）
- [uv](https://docs.astral.sh/uv/)
- 一个 OpenAI 兼容 API 的大模型服务（DeepSeek / Ollama / vLLM 等）
- 桌面界面额外需要 WebView2 运行时（安装包会自动装；dev 模式一般系统已带）

### 3.2 配置

仓库内提供 `config.example.json`，首次运行前复制为 `config.json` 并填入你的 key：

```bat
cp config.example.json config.json
```

编辑 `config.json`：

```json
{
  "api": {
    "base_url": "https://api.deepseek.com/v1",
    "model_name": "deepseek-chat",
    "api_key": "sk-你的密钥"
  },
  "agent": {
    "max_depth": 2,
    "max_clones": { "0": 10, "1": 5, "2": 3 },
    "compact_threshold": 0.85,
    "tool_result_max_len": 8000
  }
}
```

> key 获取：DeepSeek 控制台 https://platform.deepseek.com/api_keys 。使用其他兼容 API 相应调整 `base_url` 和 `model_name` 即可（如 Ollama：`http://localhost:11434/v1`，`api_key` 填 `ollama`）。

### 3.3 运行

- **桌面界面**：`uv run python prototype_antnest.py`
- **命令行核心**（原 AntNest 用法）：`uv run python AntNest.py -u "你的任务"`

## 四、分发方式（给最终用户）

本项目给最终用户三种安装方式。v1 主推 **B / C**（面向已装 uv 的开发者，零摩擦）；**A（Inno 安装包）** 作为 GitHub Release 的附加产物，给没有 uv 的普通用户。

### 4.1 方式 B：下载 Release zip，双击 AntNest.exe（推荐给大多数用户）

从 GitHub Release 下载源码 zip，解压后**双击根目录的 `AntNest.exe`** 即可（原生启动器，无控制台窗口）：

- 首次运行会自动装 [uv](https://docs.astral.sh/uv/) + WebView2 运行时并预装 pywebview（**仅需联网一次**）；
- 安装包（方式 A）这一步在安装时就已经做完，首次启动秒开；方式 B 在首次双击 `AntNest.exe` 时完成；
- 之后离线也能开；
- 运行时数据落到可写位置 `%LOCALAPPDATA%\AntNest`，不污染解压目录。

解压目录里也保留了 `launch.bat`（控制台回退）和 `launcher.ps1`（启动器源码）。

> `AntNest.exe` 由 `installer\build_launcher.ps1` 通过 ps2exe 从 `installer\launcher.ps1` 编译而来；重新生成：
> ```bat
> powershell -ExecutionPolicy Bypass -File installer\build_launcher.ps1
> ```

### 4.2 方式 C：uvx 一行命令（面向已装 uv 的开发者）

若你已装 uv，无需下载/解压，直接一行拉起（首次会从本仓库构建并缓存）：

```bat
uvx --from git+https://github.com/llxpy/AntNest antnest
```

若已 clone 本仓库，也可在仓库内：

```bat
uv run --project . antnest
```

> `antnest` 是 `pyproject.toml` 里定义的命令入口（`antnest_launcher:run`），等价于 `uv run python prototype_antnest.py` 且自动把状态写到 `%LOCALAPPDATA%\AntNest`。

### 4.3 方式 A：Windows 安装包（Inno Setup，给没有 uv 的普通用户）

源码自带 Inno Setup 安装脚本（`installer/AntNest.iss`）。打包策略是**小包 + uv 前置**：不把 Python 运行时打进去，安装时自动装 uv 和 WebView2，首次启动 `uv run` 建环境并装 pywebview。单用户安装到 `%LOCALAPPDATA%\AntNest`，无需管理员权限。装完后开始菜单 / 桌面会出现 **`AntNest.exe` 快捷方式**（原生启动器，双击即开，无控制台）。该产物作为 GitHub Release 的附加下载（`AntNest-Setup.exe`），不在主推之列。

构建（需在本机装 [Inno Setup 6](https://jrsoftware.org/isdl.php)）：

```bat
cd installer
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" AntNest.iss
```

产物：`installer\out\AntNest-Setup.exe`。**未签名**，首次运行有 SmartScreen 提示，点「更多信息 → 仍要运行」即可。

安装包内置的是**空 key 模板**（`installer/config.template.json`），不会携带任何真实密钥；用户首次运行后在设置界面填自己的 key。

详见 `installer/README.md`。

## 五、配置详解

### 5.1 config.json 完整字段

```json
{
  "api": {
    "base_url": "LLM API 地址，必须以 /v1 结尾",
    "model_name": "模型名称，推荐使用 thinking 模型",
    "api_key": "API 密钥"
  },
  "agent": {
    "max_depth": 2,
    "max_clones": { "0": 10, "1": 5, "2": 3 },
    "compact_threshold": 0.85,
    "tool_result_max_len": 8000
  }
}
```

| 字段 | 说明 | 默认值 |
| --- | --- | --- |
| `api.base_url` | LLM API 地址 | `https://api.deepseek.com/v1` |
| `api.model_name` | 模型名称 | `deepseek-chat` |
| `api.api_key` | API 密钥 | 无（必填） |
| `agent.max_depth` | 最大复制深度（≥此值降级直接执行） | `2` |
| `agent.max_clones.0` | 蚁后最大派出的工蚁数 | `10` |
| `agent.max_clones.1` | 工蚁最大派出的子工蚁数 | `5` |
| `agent.max_clones.2` | 子工蚁最大派出的子子工蚁数 | `3` |
| `agent.compact_threshold` | 触发记忆压缩的 token 比例 | `0.85` |
| `agent.tool_result_max_len` | 工具结果最大字符数（超出截断） | `8000` |

### 5.2 配置优先级与环境变量

大部分配置写在 `config.json`；以下环境变量可覆盖（部分）：

| 用途 | 环境变量 |
| --- | --- |
| config.json 路径 | `ANT_CONFIG_PATH` |
| 运行时目录（默认 `.antnest`） | `ANT_HOME` |
| 最大复制深度 | `AN_MAX_DEPTH` |
| 工蚁执行相关（命令 / 超时 / 目录等，内部使用） | `AN_CLONE_*` |
| 安装版模式开关（启动器自动设） | `ANT_INSTALLED=1` |
| 改用 CEF 渲染后端（IME 候选窗更稳，体积更大） | `ANT_WEBVIEW_GUI=cef` |

### 5.3 工作目录

```
AntNest/
├── AntNest.py            # 核心（蚁后/工蚁逻辑，含 CLI）
├── antnest_bridge.py     # UI 与核心的零侵入桥接层
├── phtmlwin.py           # 桌面 UI 框架（pywebview 封装）
├── prototype_antnest.py  # 桌面 UI 入口（聊天 + 监控 + 设置）
├── antnest_launcher.py   # 方式 C 命令入口（antnest = antnest_launcher:run）
├── launch.bat            # 方式 B 回退启动器（自动装 uv + 跑 UI）
├── AntNest.exe           # 方式 B/C 原生启动器（ps2exe 编译，gitignore）
├── launcher.ps1          # 启动器源码（编译为 AntNest.exe）
├── build_launcher.ps1    # 用 ps2exe 把 launcher.ps1 编译成 AntNest.exe
├── config.json           # 你的配置（由 config.example.json 复制，不入库）
├── pyproject.toml        # uv 依赖 + 命令入口定义
├── uv.lock
├── antnest.ico
├── installer/            # Windows 安装包（Inno Setup）
└── .antnest/             # 运行时私人空间（自动创建）
    ├── NEST.md           # 长期记忆
    ├── sessions/         # 会话存档
    └── clone_pool/       # 工蚁临时目录（用完即清）
```

## 六、使用模式

### 6.1 交互模式（桌面界面）

```bat
uv run python prototype_antnest.py
```

启动后进入桌面窗口。也可走原命令行交互（无界面）：

```bat
uv run python AntNest.py
```

### 6.2 无交互模式（命令行核心）

```bat
uv run python AntNest.py -u "统计当前目录下所有 Python 文件的代码行数"
```

| 选项 | 说明 |
| --- | --- |
| `-u "任务描述"` | 单次执行 |
| `-s` | 搭配 `-u`，载入并保存 session |
| `--until "停止字符串"` | 搭配 `-u`，蚁后输出包含此字符串时自动停止 |
| `-a` | 跳过所有安全确认 |
| `-l` | 列出所有 session |
| `-c` | 清除当前目录的 session |

示例——让蚁后自动完成任务并确认：

```bat
uv run python AntNest.py -asu "分析当前项目结构并写入 README.md" --until "任务完成"
```

### 6.3 嵌入自动化流程

AntNest 可被任意脚本调用。例如 CI/CD 中自动生成测试报告：

```bat
cd /path/to/project
uv run python /path/to/AntNest/AntNest.py -asu "运行所有测试，汇总失败用例，写入 test-report.md" --until "DONE"
```

## 七、常见问题

**Q: 工蚁会不会泄露敏感信息？**
工蚁创建时只复制 `AntNest.py`，不继承蚁后的内存状态和对话历史。工蚁通过环境变量或临时文件接收命令，执行结果写入本地 JSON 文件，蚁后读取后立即删除工蚁目录。但需注意：这是进程级隔离，不是安全沙箱——若 LLM 被诱导生成读取敏感文件的命令（如 `type C:\Users\xxx\.ssh\id_rsa`），工蚁照样会执行。代码有危险命令正则过滤作为兜底，但不覆盖所有场景。

**Q: 如果工蚁执行时间很长怎么办？**
工蚁有超时机制（默认 300 秒）。超时后蚁后会杀死工蚁进程并销毁目录。可在 `spawn_clone` 时指定 `timeout` 参数。

**Q: 工蚁可以调用 LLM 吗？**
不可以。工蚁检测到 `AN_CLONE_MODE=1` 环境变量后，在导入阶段就直接执行命令并退出，不会加载 LLM 模块、不会连接 API。工蚁是纯粹的 Shell 执行器。

**Q: 为什么推荐使用 thinking 模型？**
thinking 模型（如 `deepseek-reasoner`、带思考链的模型）在流式输出时区分 `reasoning_content`（思考过程）和 `content`（正文）。AntNest 内置了 Think 重复检测器（Rabin-Karp 滚动哈希），能防止模型陷入思考循环。

**Q: 候选悬浮窗不弹 / 中文输入法异常？**
见第二节「中文输入法提示」：开微软拼音「兼容性 → 使用以前版本的微软拼音」即可；第三方输入法属已知限制，或在启动时设 `ANT_WEBVIEW_GUI=cef` 换 CEF 后端。

**Q: NEST.md 什么时候更新？**
token 用量达到上限的 85% 时触发记忆压缩，LLM 自主整理技能知识写入 `NEST.md`。也可手动编辑 `NEST.md` 教蚁后新技能，重启后生效。

## 八、许可证

本项目使用 MIT 许可证，详见仓库 LICENSE 文件。
