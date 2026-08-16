# AntNest 1.2.2 发布前 Bug 审查清单

> 审查日期：2026-08-10
> 审查对象：`E:\AntNest\AntNest-master`（用户声明版本 1.2.2，尚未发布）
> 审查范围：蚁后核心 `AntNest.py`、工蚁执行器 `antnest_clone_worker.py`、UI 桥 `antnest_bridge.py`、路径工具 `code_tools.py`、配置读写、HTTP 调用、版本/变更日志一致性、单测（`tests/`，39 passed / 6 subtests passed）。
> 说明：本次为发布前静态 + 动态审查，重点覆盖执行/安全关键路径；非逐行全量审计。

---

## 🔴 发布阻断

### B0 — 版本号严重脱节（pyproject 1.2.2 vs CHANGELOG 1.3.8）
- `pyproject.toml:3` → `version = "1.2.2"`
- `CHANGELOG.md:3` → 已记录至 `## [1.3.8] — 2026-08-16`，中间含 1.3.0~1.3.8 多轮「进程泄漏修复 / 循环堆叠防护 / HTTP 400 兜底 / 资源上限 / 死代码清理」等实质改动。
- **问题**：源码版本仍停在 1.2.2，但变更日志与代码实际状态已跑到 1.3.8。发布前必须对齐：要么把 `pyproject.toml` 提到真实版本（建议 1.3.8），要么把 CHANGELOG 截断回 1.2.2 对应的实际改动。
- **影响**：用户/评审看到 1.2.2 却拿到 1.3.8 的行为，版本可信度直接崩；`uvx`/`pip` 发布与 CHANGELOG 不一致也会误导使用者。
- **修复**：发布前统一版本号；建议 CI 加一步断言 `pyproject version == CHANGELOG 最新标题`。

---

## 🔴 安全类

### S1 — API Key 明文落盘（含 UI 保存回写）
- `config.json:5` 当前为 `"api_key": ""`（本机暂无明文泄露，但这是机制问题，不是某次的具体值）。
- `antnest_bridge.py:271-272, 291-292`：`save_settings` 把 UI 输入的 `llm_api_key` 直接 `str().strip()` 后 `json.dump` 写回 `config.json`，**无任何加密 / 环境变量 / 系统密钥库（keyring）处理**。
- **问题**：只要用户在界面填过 key，明文就永久躺在 `config.json`（虽被 `.gitignore` 排除，但仍在本地磁盘、易随备份/同步/截图泄露）。这与「密钥不该以明文驻留配置文件」的基本安全规范相悖。
- **修复建议**：
  - 优先：key 只存环境变量（`ANTNEST_API_KEY`）或系统 keyring（`keyring` 库），`config.json` 只留 `api_key_set: true` 标记。
  - 退路：至少 `config.json` 落地前做一次轻量混淆（base64 不算加密，仅防误看），并在 README 显著提醒「勿提交、勿同步」。

### S2 — 危险命令过滤多处漏网（递归删除未拦全）
两处正则都锚定过严，漏掉大量常见不可逆删除：
- 蚁后侧 `AntNest.py:1083-1093` `_DANGER_CLI_PATTERNS`：
  - `rm\s+-(?:rf|fr)\s+/\s*$` 要求行尾 `/` → `rm -rf /home`、`rm -rf /tmp/*` **不拦**。
  - 完全不覆盖相对目标：`rm -rf .`、`rm -rf ~`、`rm -rf *`、`rm -rf node_modules`、`rm -rf build/`。
- 工蚁侧 `antnest_clone_worker.py:108-115` `dangerous_patterns`：
  - `rm\s+-rf\s+/` 仅拦「`-rf` 后紧跟 `/`」→ `rm -rf /home/user` 能拦，但 `rm -rf node_modules`、`rm -rf .`、`rm -rf ~`、`rm -rf *` **全不拦**。
- **问题**：LLM 在「清理/重装/重建」场景下极易自发 `rm -rf node_modules` 或 `rm -rf .`，当前过滤**全部放行**，且蚁后侧对 `/home` 这类也放行。
- **修复建议**：
  - 把「递归强制删除」拆成两类：①绝对根/盘符（`rm -rf /`、`rm -rf C:\`）硬拦；②项目内相对递归删除（`rm -rf <proj内路径>`）放行但记录；③项目外相对/绝对（`rm -rf /home`、`rm -rf ~`、`rm -rf /Users/...`）硬拦。
  - 用「拒绝清单 + 路径白名单」替代纯行尾锚定正则，别再依赖 `$` 锚点。

### S3 — `run_python` 主进程直执行且无危险过滤（架构 + 安全双重问题）
- `AntNest.py:752-769` `get_queen_tools` 在 **764 行**把 `run_python_schema` 放进蚁后工具表。
- `AntNest.py:1143-1181` `run_python`：在主进程 `subprocess.run([sys.executable, script], cwd=PROJECT_DIR, ...)` 直接解释任意 Python，**未调用 `_check_danger_command`**（该函数只在 `run_cli` 内使用，行 1106）。
- **问题**：
  1. 违反设计时「蚁后不直接执行、只 spawn_clone 工蚁」的纪律 —— `run_python` 在蚁后（主进程、非隔离临时目录）直接跑代码，`cwd=PROJECT_DIR` 且拥有当前用户全部权限。
  2. 没有任何危险过滤：LLM/prompt 注入可借此 `os.system("rm -rf /")`、`shutil.rmtree(os.path.expanduser("~"))` 等，绕过 S2 的全部正则（正则只罩 `run_cli`，不罩 `run_python`）。
- **修复建议**：
  - 最好：从蚁后工具表移除 `run_python`，改为走 `spawn_clone`（工蚁隔离目录内执行，复用 S2 过滤）。
  - 退路：若必须保留主进程直执行，至少加与 `run_cli` 等价的 `_check_danger_command` 前置，并把 `cwd` 限到隔离临时目录而非 `PROJECT_DIR`。

### S4 — 路径解析无越界/沙箱校验（可写项目外）
- `code_tools.py:16-20` `resolve_path`：`Path(project_dir)/path` 后 `.resolve()`，**无 `is_relative_to(PROJECT_DIR)` / 越界回退检查**。
- `AntNest.py:1017-1018` `_resolve_path` 直接透传：`return ct.resolve_path(path, PROJECT_DIR)`。
- `write_file`(1061) / `search_replace`(1069) / `view_file` / `list_dir` / `grep_files` 全部经 `_resolve_path` 解析出**绝对路径**，再交给工蚁脚本在该绝对路径读写。
- **问题**：LLM 传入绝对路径（如 `C:\Users\xxx\.ssh\id_rsa`）或 `../../`（如 `../../.bashrc`、`../../../etc/passwd`）时，解析结果落在项目外，工蚁脚本会**照写不误**（工蚁侧也只有命令级危险过滤，无路径级边界检查）。这是典型的路径穿越/越权写。
- **修复建议**：
  - `resolve_path` 末尾加：`if not p.resolve().is_relative_to(Path(project_dir).resolve()): raise / 回退到 project_dir 内`。
  - 工蚁脚本写文件前再校验一次目标在 clone_dir/授权根内（纵深防御）。

### S5 — `web_fetch` 无 URL 协议校验（file:// 读本地 / SSRF）
- `AntNest.py:1184-1188`：`urlopen(_u.Request(url, ...))` 直接打开，**无 scheme 白名单**。
- **问题**：
  - 传入 `file:///C:/Users/xxx/.ssh/id_rsa` 或 `file:///etc/passwd` 时，`urllib` 在多数 Python 环境下会**读取并返回本地文件内容**给 LLM。
  - 传入内网地址（`http://169.254.169.254/...` 云元数据、或 `http://192.168.x.x`）构成 SSRF。
- **修复建议**：先 `urlparse(url)`，仅允许 `http`/`https`，其余 scheme 直接拒绝；额外对 `http(s)` 做内网/保留段黑名单（可选）。

---

## 合并说明（与初版 7 项清单的对应关系）
原审查的 7 项里，「config.json 明文 key」与「UI 保存明文回写 key」实为同一机制（均落在 `save_settings` → 明文 `config.json`），本清单合并为 **S1**；其余 5 项（版本脱节 / 危险过滤漏网 / run_python 直执行 / 路径越界 / web_fetch 协议）即 B0、S2、S3、S4、S5。无新增 bug，仅纠正了「config.json 当前已无明文 key」这一状态变化。

---

## ✅ 已验证非 bug（安心项，无需修）
- **URL 拼接正确**：`base_url` 带 `/v1` + 拼 `/chat/completions` 得到 `https://api.deepseek.com/v1/chat/completions`，与 DeepSeek 兼容端点一致。
- **无裸 `except`**：异常均有具体类型/兜底处理，未见 `except:` 吞错。
- **单测通过**：`tests/` 下 `39 passed, 6 subtests passed`，无报错。
- **HTTP 400「insufficient tool messages」兜底已做**：`agent_single_loop` 在工具循环中断后从后往前补齐 assistant tool_calls 缺失的 `tool` 响应（对应 CHANGELOG 1.3.6）。
- **进程隔离/清理到位**：蚁后 `spawn_clone` 进隔离临时目录、执行后 `shutil.rmtree` 自愈；`run_cli`/`run_python` 的 `scratch` 均在 `finally` 清理；超时、重复调用检测、进程杀树均已实现。

---

## 修复优先级建议
1. **B0**（发布阻断，必须先对齐版本）
2. **S3**（主进程直执行 + 无过滤，最高危）→ 改为走工蚁或加过滤
3. **S4**（路径越界写）→ `resolve_path` 加边界校验
4. **S2**（递归删除漏网）→ 重写危险命令判定为「拒绝清单 + 路径白名单」
5. **S5**（web_fetch 协议）→ 加 scheme 白名单
6. **S1**（key 明文落盘）→ 改环境变量/keyring，或至少显著提醒

---

## 修复记录（2026-08-16，当前版本 1.2.2）

> 用户确认：当前发布版本即 **1.2.2**（由 1.2.1 修复而来），版本号本身无误。故本次只修安全类 S1–S5，不动版本号；CHANGELOG 1.3.8 的脱节由用户后续统一处理。

- **S1 ✅ 已修（key 不再明文落盘）**
  - 新增独立密钥文件 `config.json.key`（权限 `0600`，已加入 `.gitignore`）。
  - `antnest_bridge.save_settings` 不再把明文 key 写回 `config.json`（仅留 `api_key: ""`），改写密钥文件；`load_settings` 优先从密钥文件读取。
  - `AntNest.py` 启动读取 `ANT_API_KEY` 时也优先读密钥文件。
  - `prototype_antnest.py` UI 提示文案同步改为「仅保存在本机（独立密钥文件，不进 config.json）」。

- **S2 ✅ 已修（危险命令覆盖递归删除）**
  - `_DANGER_CLI_PATTERNS`（`AntNest.py`）与 `dangerous_patterns`（`antnest_clone_worker.py`）重写为「递归删除 + 危险目标拒绝清单」：
    - 覆盖 `rm -rf /`、`/home`、`/Users`、`~`、`..`、`.`、`*`、`$HOME`、盘符 等绝对/上级/当前/通配/主目录目标；裸 `rm -r`/`rm -R` 也拦绝对路径。
    - 保留 `format` / `shutdown` / `halt` / `mkfs` / `dd if=/dev/zero` / `Remove-Item -Recurse|-Force` / fork bomb。
  - 项目内相对递归删除（如 `rm -rf node_modules`）仍放行，符合原设计意图（只拦系统级不可逆操作）。

- **S3 ✅ 已修（蚁后不再直执行 run_python）**
  - `get_queen_tools` 已移除 `run_python_schema`，蚁后不再于主进程直执行任意代码；需要跑 Python 走 `run_cli` 经工蚁隔离目录执行。
  - `run_python` 函数保留但不再作为蚁后工具可达。

- **S4 ✅ 已修（路径越界校验）**
  - `code_tools.resolve_path` 末尾加 `is_relative_to(project_dir)` 校验，越界（绝对路径 / `../`）直接 `raise ValueError`。
  - `AntNest.py` 的 `view_file` / `list_dir` / `grep_files` / `write_file` / `search_replace` 均已 `try/except` 捕获并返回错误 JSON，杜绝把路径穿越传給工蚁。

- **S5 ✅ 已修（web_fetch 协议白名单）**
  - `web_fetch` 先 `urlparse` 校验 scheme，仅允许 `http` / `https`，其余（如 `file://`、`ftp://`）直接拒绝。

- **新 bug（用户 2026-08-16 报告）：工蚁调用无限递归堆叠 / OOM —— ✅ 已修**
  - **现象**：在测试环境调用工蚁（跑命令）时，进程会一层套一层无限叠加，每级新开一个 `python.exe`，最终把内存挤爆。
  - **根因**：`antnest_clone_worker.run_if_clone_mode` 用 `run_env = os.environ.copy()` 启动命令子进程，但 clone 进程自身的环境里带着 `AN_CLONE_MODE=1` + `AN_CLONE_COMMAND` + `AN_CLONE_DIR` 等控制变量。于是命令里只要出现 `python AntNest.py` 或 `import AntNest`（LLM 在调试/自检脚本里极易触发），就会被再次判定为 clone 模式、用**同一道命令**重新进入 `run_if_clone_mode` → 蚁后 spawn 蚁后 spawn 蚁后…无限自嵌套。`MAX_DEPTH` 的硬拦截只罩 `spawn_clone`（蚁后侧），罩不到这种「命令子进程再 import 父模块」的旁路递归。
  - **修复**：在 `run_env` 交给 `subprocess.Popen` 之前，显式 `pop` 掉全部 clone 控制变量（`AN_CLONE_MODE` / `AN_CLONE_COMMAND` / `AN_CLONE_COMMAND_FILE` / `AN_CLONE_DIR` / `AN_RESULT_FILE` / `AN_CLONE_TIMEOUT` / `AN_DEPTH`），保留 `PYTHONIOENCODING` / `PYTHONUTF8` 等编码变量。命令子进程从此看不到 `AN_CLONE_MODE`，即使 `import AntNest` 也只会落到 `main()`（普通主流程），不会再 re-enter clone 模式 —— 递归链在源头断开。
  - **纵深**：`MAX_DEPTH` 的蚁后侧深度护栏仍保留；本修复针对的是「命令内部 import 父模块」这条蚁后护栏罩不到的旁路。
  - **验证**：新增 `tests/test_security_fixes.py::CloneRecursionTest.test_worker_command_env_stripped`，断言命令子进程 stdout 中 `AN_CLONE_MODE` 必须为 `None`。

- **验证**
  - 改动文件全部 `py_compile` 通过。
  - 新增 `tests/test_security_fixes.py` 锁定 S1–S5（含「项目内相对递归删除仍放行」反向用例）+ 工蚁递归防护（Request C）。
  - 全量 `pytest tests`：`47 passed, 17 subtests passed`（原 39 passed + 新 8 项：S1–S5 共 7 + 递归防护 1）。

- **尚未处理（用户已知）**
  - B0 版本脱节：`pyproject 1.2.2` vs `CHANGELOG 1.3.8`，用户确认版本号无误，CHANGELOG 统一待办。
  - 内网/保留段 SSRF 黑名单（S5 的进阶项，当前仅做 scheme 白名单，未禁 `169.254.169.254` 等元数据地址）。
