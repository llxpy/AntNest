<p align="center">
  <img src="ui_assets/queen-avatar.png" width="96" height="96" alt="AntNest 蚁后徽标"/>
</p>

<h1 align="center">AntNest · 蚁巢</h1>

<p align="center">
  <strong>本地运行 · 蚁后/工蚁架构 · 深度思考可见 · 全程掌控本机</strong>
</p>

<p align="center">
  <a href="https://github.com/llxpy/AntNest/releases"><img src="https://img.shields.io/badge/Windows-v1.3.0-blue?logo=windows" alt="Windows"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.12+-yellow?logo=python" alt="Python"></a>
  <a href="https://github.com/llxpy/AntNest"><img src="https://img.shields.io/badge/GitHub-源码-purple" alt="GitHub"></a>
</p>

---

## AntNest 是什么

**AntNest（蚁巢）是一个跑在本机的 AI Agent 桌面应用。** 它用「蚁后 + 工蚁」的架构，把复杂任务拆成子任务，派给隔离沙箱中的工蚁去执行——蚁后负责思考、规划与决策，工蚁负责在隔离环境里替你真的操作这台电脑。

- **你的 AI，你的电脑，你的规则**：全部数据与操作留在本机，模型通过 OpenAI 兼容接口接入（DeepSeek / MiniMax / Kimi / OpenAI / Ollama 等）。
- **不是聊天玩具，是干活的手**：文件读写、代码工程、系统管理、网络诊断、命令执行——只要命令能做的事，交给蚁后就能做。

---

## 为什么是「蚁后 / 工蚁」

| 角色 | 职责 | 隔离 |
|---|---|---|
| **蚁后** | 理解任务 → 推理拆解 → 派发决策 → 汇总回复 | 主进程，不直接跑命令 |
| **工蚁** | 在隔离目录内执行文件/命令/Python，产出后销毁 | 独立进程 + 临时目录 |

分工带来三件事：

1. **安全**：危险命令有规则过滤（递归删除/格式化/关机等模式拦截），工蚁只在自己的临时巢穴里动土，不碰主进程。
2. **并行**：一层最多派 10 只工蚁，层深 2→5→3，多任务同时跑。
3. **恢复**：工蚁失败自动重试一次；用户强停后保存决策摘要，下一轮先理解「为什么停」再继续。

---

## 核心能力（v1.3.0）

### 代码与文件
- 读文件 / 列目录 / 全文检索 / 写入文件 / 搜索替换——本地代码工程的日常全部覆盖
- 工蚁隔离执行 Python / shell，产出文件自动收回归档

### 深度思考 · 全程可见
- **任务进行中实时流式显示「深度思考」**（逐字填充，小卡片内滚动）
- 回复正文随文本长度自适应，长文不再被截断
- 每条回复都保留思考过程，随时回看蚁后为什么这么做

### 记忆
- **B+ 树思想索引 + RAG 检索**的记忆层（`memory_tree.py`）
- 每轮自动检索 NEST.md / hints.md 相关段落注入上下文
- 动态用户画像 / 行为偏好 / 停止快照恢复，跨会话持续演进

### Agent 自律
- 连续 3 次相同工具+相同参数 → 注入**《自我反思》**指引换一种方法继续，不再卡死中断
- thinking 重复自动打断擦除；单任务最大轮次兜底；工具格式错误自动纠正

### UI / 体验
- 主题：黑色 / 白色 / 自定义色
- 右下**结构化任务状态卡**：当前任务、开始时间、工蚁创建与存活
- 子任务 / 工蚁支持「展开」卡片总览，长路径自动换行
- **对话记录**：只读「查看」历史对话（含思维过程），可随时**恢复上下文**或删除
- 应用启动时立绘 splash 一次登场
- 标题栏 / 任务栏图标 = 应用图标（兼容新旧 pywebview）

---

## 快速开始

### 方式一：安装包（推荐）
从 [**Releases**](https://github.com/llxpy/AntNest/releases) 下载 `AntNest-Setup.exe`，单用户安装到 `%LOCALAPPDATA%\AntNest`，**无需管理员权限**。首次运行自动用 uv 拉取依赖（需要联网）。

### 方式二：便携（开发）
```bash
git clone https://github.com/llxpy/AntNest.git
cd AntNest
uv run python prototype_antnest.py
```

### 方式三：直接启动
仓库根目录运行 `AntNest.exe`（编译的 GUI 启动器，零配置）。

> 首次使用：打开「⚙ 设置」填入 API Base URL / 模型 / Key，点「保存并校验」即可开聊。

---

## 支持的模型服务

| 服务 | 说明 |
|---|---|
| DeepSeek | 深度思考（reasoning）原生支持 |
| MiniMax | 推理快；建议 thinking_mode=auto/off |
| Kimi / Moonshot | 优化参数推荐 |
| OpenAI | 标准接口 |
| Ollama / 本地端点 | 任意 OpenAI 兼容服务 |

---

## 安全模型（纵深防御）

| 层 | 保护 | 可配置 |
|---|---|---|
| 命令过滤 | 正则拦截危险模式（递归删除/格式化/关机…） | ✅ |
| 工蚁隔离 | 独立进程 + 临时目录执行 | ❌ |
| 资源回收 | 进程树强杀、临时文件清理、目录自愈 | ❌ |
| 审计日志 | 全部命令落 `ui_trace.log` 可回溯 | ✅ |
| 密钥隔离 | API Key 存独立 `*.key` 文件（0600），config.json 不落明文 | ✅ |

> ⚠️ 进程级隔离而非容器沙箱。过滤器清单见 `antnest_clone_worker.py`。

---

## 工作流程

```
你下达任务
   ↓
蚁后流式「深度思考」：分析 → 拆子任务 → 决定方案
   ↓
派发工蚁：隔离目录执行文件/命令/Python（失败自动重试一次）
   ↓
工蚁归巢：产出回收归档，状态回传监控面板
   ↓
蚁后汇总回复（附思考过程），本轮自动保存会话
```

---

## 项目结构（v1.3.0）

```
AntNest/
├── prototype_antnest.py      # 桌面 GUI 入口
├── phtmlwin.py               # 轻量 GUI 框架（webview/浏览器双模式）
├── antnest_bridge.py         # GUI ↔ 核心适配层（事件订阅/流式/工具包装）
├── antnest_config.py         # 配置与系统提示词（主题文件解耦）
├── antnest_llm.py            # LLM 调用层（流式/重复检测/用量）
├── antnest_loop.py           # Agent 循环（工具调用/自我反思/记忆压缩）
├── antnest_memory.py         # 记忆管理（B+树 + 检索）
├── antnest_queen.py          # 蚁后工具集与命令安全过滤
├── antnest_session.py        # 会话持久化与目录锁
├── antnest_runtime_state.py  # 动态画像/人设/停止快照
├── memory_tree.py            # B+树思想索引 + RAG 记忆检索
├── model_capabilities.py     # 模型能力探测与画像注入
├── mcp_client.py             # MCP 工具集成（stdio/http/sse）
├── antnest_clone_worker.py   # 工蚁隔离执行系统
├── ui_render.py / ui_render_loader.py  # UI 渲染
├── ui_assets/                # 样式/脚本/蚁后形象/图标
├── prompts/                  # queen_system.md（主题提示词）
├── installer/                # Inno Setup 安装包
├── tests/                    # 回归测试（82+17 通过）
└── android/                  # 移动端伴生（实验性）
```

---

## 测试

```bash
python -m pytest tests/ -q   # 当前 82 passed + 17 subtests
```

---

## License

[MIT](LICENSE) — Copyright © 2026 LLXPY