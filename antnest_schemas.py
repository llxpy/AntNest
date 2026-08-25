# -*- coding: utf-8 -*-
"""AntNest · 工具 JSON Schema（纯数据，无运行状态）。

从 AntNest.py 拆出的「工具定义区」：12 个 function schema。
SHELL 为运行时壳传入（antnest_config 提供），因此这里在 import 期
一次性构建，AntNest.py 壳 re-export 同名 schema 变量。
"""

from __future__ import annotations

from antnest_config import SHELL


spawn_clone_schema = {
    "type": "function",
    "function": {
        "name": "spawn_clone",
        "description": (
            f"生成一只工蚁（自身副本）去执行命令。工蚁在隔离的临时目录中运行，"
            f"执行完毕后将结果写入文件，然后退出。本体会读取结果并销毁工蚁文件。"
            f"当前为蚁后模式（depth=0），所有执行必须通过此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "工蚁要执行的完整命令"},
                "timeout": {
                    "type": "integer",
                    "default": 0,
                    "description": "超时时间（秒），0 表示使用默认值（config 中的 worker_timeout，默认 300 秒）",
                },
                "label": {
                    "type": "string",
                    "description": "工蚁标签，用于日志追踪，如 '统计repo-a'",
                },
                "verify": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否派验证工蚁确认执行结果。设为 true 时，执行完成后会自动派验证工蚁检查结果是否正确。",
                },
            },
            "required": ["command"],
        },
    },
}

get_task_status_schema = {
    "type": "function",
    "function": {
        "name": "get_task_status",
        "description": (
            "查看工蚁任务的状态。返回任务的状态机信息（pending/running/done/verified/failed/timeout/cancelled）。"
            "可用于检查任务是否完成、是否已验证。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（从 spawn_clone 返回结果中的 task_id 字段获取）",
                },
            },
            "required": ["task_id"],
        },
    },
}

view_file_schema = {
    "type": "function",
    "function": {
        "name": "view_file",
        "description": (
            "派工蚁只读查看文件内容（蚁后自己不读文件）。"
            "相对路径基于项目目录；大文件请用 start_line/end_line 分段查看。"
            "返回带行号的文本。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（相对或绝对）"},
                "start_line": {
                    "type": "integer",
                    "default": 1,
                    "description": "起始行号（从 1 开始，含）",
                },
                "end_line": {
                    "type": "integer",
                    "default": 0,
                    "description": "结束行号（含）；0 表示读到文件末尾",
                },
            },
            "required": ["path"],
        },
    },
}

list_dir_schema = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": (
            "派工蚁只读列出目录内容（蚁后自己不列目录）。"
            "相对路径基于项目目录。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "default": ".",
                    "description": "目录路径（相对或绝对）",
                },
                "max_entries": {
                    "type": "integer",
                    "default": 100,
                    "description": "最多返回条目数",
                },
            },
        },
    },
}

grep_files_schema = {
    "type": "function",
    "function": {
        "name": "grep_files",
        "description": (
            "派工蚁在目录或文件中搜索匹配行（正则）。"
            "用于定位符号、函数、配置项。相对路径基于项目目录。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式"},
                "path": {
                    "type": "string",
                    "default": ".",
                    "description": "搜索根路径（文件或目录）",
                },
                "glob": {
                    "type": "string",
                    "default": "*",
                    "description": "文件名 glob，如 *.py",
                },
                "max_matches": {
                    "type": "integer",
                    "default": 50,
                    "description": "最多返回匹配数",
                },
            },
            "required": ["pattern"],
        },
    },
}

write_file_schema = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "派工蚁写入或覆盖文件（蚁后自己不写盘）。"
            "会自动创建父目录。相对路径基于项目目录。"
            "改已有文件时优先用 search_replace。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "完整文件内容"},
            },
            "required": ["path", "content"],
        },
    },
}

search_replace_schema = {
    "type": "function",
    "function": {
        "name": "search_replace",
        "description": (
            "派工蚁在文件中精确替换一段文本（蚁后自己不写盘）。"
            "默认只替换第一处；若 old_string 多处出现且未设 replace_all，会报错以防误改。"
            "修改已有代码时应优先于 write_file。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "old_string": {
                    "type": "string",
                    "description": "要被替换的原文（需与文件内容完全一致，含缩进与换行）",
                },
                "new_string": {"type": "string", "description": "替换后的内容"},
                "replace_all": {
                    "type": "boolean",
                    "default": False,
                    "description": "true=替换所有匹配；false=仅第一处，且多处匹配时报错",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}

run_cli_schema = {
    "type": "function",
    "function": {
        "name": "run_cli",
        "description": (
            f"直接执行 {SHELL} 命令。仅在工蚁模式（depth>0）下可用。"
            f"蚁后模式必须使用 spawn_clone。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "timeout": {
                    "type": "integer",
                    "default": 300,
                    "description": "超时时间（秒）",
                },
            },
            "required": ["command"],
        },
    },
}

run_python_schema = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "直接解释执行 Python 代码（不经过 shell，中文与引号完整保留）。"
            "适合数据处理、快速验证；复杂任务建议先用 write_file 写文件再执行。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的 Python 代码"},
                "timeout": {
                    "type": "integer",
                    "default": 120,
                    "description": "超时时间（秒）",
                },
            },
            "required": ["code"],
        },
    },
}

web_fetch_schema = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": "抓取网页内容并转为纯文本（查文档、查资料用）。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的完整 URL"},
                "max_bytes": {
                    "type": "integer",
                    "default": 30000,
                    "description": "最多返回的字符数",
                },
            },
            "required": ["url"],
        },
    },
}

memory_hints_schema = {
    "type": "function",
    "function": {
        "name": "leave_memory_hints",
        "description": (
            "对消息历史进行压缩并将记忆线索写入 hints.md。"
            "参数 hints 是要保存的记忆线索文本。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hints": {"type": "string", "description": "记忆线索文本"},
            },
            "required": ["hints"],
        },
    },
}

mcp_call_schema = {
    "type": "function",
    "function": {
        "name": "mcp_call",
        "description": (
            "调用已连接的 MCP 服务器工具（需先在设置中启用 MCP）。"
            "server 为 mcp.json 里配置的服务器名，tool 为 tools/list 返回的工具名，"
            "arguments 为 JSON 对象字符串。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "MCP 服务器名称"},
                "tool": {"type": "string", "description": "工具名称"},
                "arguments": {
                    "type": "string",
                    "default": "{}",
                    "description": "JSON 格式的参数对象",
                },
            },
            "required": ["server", "tool"],
        },
    },
}

mcp_list_tools_schema = {
    "type": "function",
    "function": {
        "name": "mcp_list_tools",
        "description": "列出所有已连接 MCP 服务器及其工具（启用 MCP 后可用）。",
        "parameters": {"type": "object", "properties": {}},
    },
}

register_tool_schema = {
    "type": "function",
    "function": {
        "name": "register_tool",
        "description": (
            "将手搓的 Python 工具标准化登记进工具库（.antnest/tools/）。"
            "登记后下次可直接复用，避免重复造轮子并减少记忆占用。"
            "用完后可用 list_tools 查看、get_tool_source 取源码改进。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名（字母/数字/中划线/下划线，≤64 字符）"},
                "description": {"type": "string", "description": "工具用途说明（供复用匹配）"},
                "parameters": {"type": "string", "default": "{}", "description": "JSON 参数 schema 字符串"},
                "code": {"type": "string", "description": "工具完整 Python 源码"},
                "category": {"type": "string", "default": "general", "description": "工具分类（general/code/data/web/system）"},
                "source_note": {"type": "string", "default": "", "description": "来源说明（由哪次任务演化而来）"},
            },
            "required": ["name", "code"],
        },
    },
}

list_tools_schema = {
    "type": "function",
    "function": {
        "name": "list_tools",
        "description": "列出工具库全部已登记工具（名称/用途/分类/源码行数），供挑选复用。",
        "parameters": {"type": "object", "properties": {}},
    },
}

get_tool_source_schema = {
    "type": "function",
    "function": {
        "name": "get_tool_source",
        "description": "按名从工具库取回某工具的源码（复制/复用/改进后重新登记）。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名（list_tools 返回）"},
            },
            "required": ["name"],
        },
    },
}