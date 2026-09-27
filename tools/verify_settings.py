# -*- coding: utf-8 -*-
"""设置页深度验证：对象 repr 必须为零，每个分区内容必须真实存在。"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import prototype_antnest as P  # noqa: E402

html = P._settings_modal().render()
io.open("tools/_settings_render.html", "w", encoding="utf-8").write(html)

fails = []
if "object at 0x" in html:
    fails.append("仍存在对象 repr")

must_have = [
    # LLM 分区
    "API Base URL",
    "Temperature 温度",
    "服务商预设",
    "Thinking 模式",
    "检测模型列表",
    # 蚁巢参数
    "最大递归深度",
    "每层并发数",
    # 集成
    "启用 MCP",
    "MCP 服务器",
    "Skills 目录",
    "管理 Skills",
    # 外观
    "自定义主题色",
    # Agent
    "自定义 Agent 行为偏好",
    # 高级
    "上下文压缩阈值",
    "工蚁超时（秒）",
    "配置与数据",
    "打开目录",
    # 结构
    'data-panel="advanced"',
    'id="set-theme"',
    'class="settings-panel active"',
]
for probe in must_have:
    if probe not in html:
        fails.append("MISSING: " + probe)

if fails:
    print("FAIL:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL PASS,", len(html), "chars")
