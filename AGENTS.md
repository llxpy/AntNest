# AGENTS.md — AntNest 能力自适应约束

> 所有参与 AntNest 开发/运行的 Agent 必须遵守以下约束。

## 1. 能力检测是启动流程的一部分（非可选）
- 进程内首次会话前调用 `AntNest._ensure_model_cap()`：按 `/models` 实测校准
  `TOKEN_CAP`,并把「当前模型能力」画像注入 `SYSTEM_PROMPT`。
- 检测必须**非致命**：网络失败 / 超时 / HTTP 401 一律降级为默认画像,
  禁止 `sys.exit(1)` 阻塞启动（历史版本在 401 时退出进程,已修复）。
- 缓存遵循 `model_capabilities`（TTL 600s,`(base, model)` 维度）,避免重复探测。

## 2. 开关放在配置区
- 唯一开关：`config.json → api.skip_model_check`（或环境变量
  `ANT_SKIP_MODEL_CHECK`）。UI 设置页可同步该字段（设置 → API → 跳过模型检测）。
- 置 `true`：跳过一切主动探测与校准,使用 `DEFAULT_TOKEN_CAP`。

## 3. 根据能力动态调整思考预算
- `TOKEN_CAP`：探测命中时用实测上下文;压缩阈值（`COMPACT_THRESH`）与工具结果
  截断（`TOOL_RESULT_LEN`）都按 `TOKEN_CAP` 缩放。
- 模型类型影响预算分配：`reasoning` 型允许深思考;flash/高吞吐型控制思考轮次;
  未知模型按默认画像,不猜测。
- 能力画像写入系统提示词后,模型应据此决定思考深度与工具策略
  （例如 vision 可用时优先读图,不可用时明确告知用户）。

## 4. 结果与画像
- 探测结果摘要存放在 `model_capabilities`（`describe_capability` 一行式 /
  `summarize_capability` 分项式）,UI 能力展示与系统提示词共用同一数据源,
  禁止两套实现。
- 新增能力指标时优先扩展 `model_capabilities.py`,再接线
  `AntNest.py` / `antnest_bridge.py` / UI。