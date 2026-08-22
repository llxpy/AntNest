def _settings_modal():
    """优化后的设置面板"""
    # LLM 连接部分
    llm = ui.div(cls="settings-grid")[
        _field("llm_base_url", "API Base URL", SETTINGS["llm_base_url"], code=True,
               hint="DeepSeek / Kimi / MiniMax / OpenAI 等兼容接口"),
        _field("llm_model", "模型名称", SETTINGS["llm_model"], code=True,
               hint="点击「检测模型列表」自动填充；不支持 /models 的请手填",
               list_id="model-list-options"),
        ui.raw('<datalist id="model-list-options"></datalist>'),
        _field("llm_api_key", "API Key", SETTINGS["llm_api_key"], full=True, code=True,
               hint="密钥独立存储，不写入 config.json", input_type="password"),
        _field("thinking_mode", "推理模式", SETTINGS.get("thinking_mode", "auto"), code=True,
               hint="auto=按模型自动 / on=强制思考 / off=关闭（MiniMax 建议 off）"),
        _toggle("skip_model_check", "跳过模型检测", SETTINGS.get("skip_model_check", "false"),
                hint="MiniMax 等不支持 /models 的 API 请开启"),
    ]

    # 蚁巢参数
    agent = ui.div(cls="settings-grid")[
        _field("max_depth", "最大递归深度", SETTINGS["max_depth"], code=True,
               hint="工蚁嵌套层数上限（推荐 2）"),
        _field("max_clones", "每层并发数", SETTINGS["max_clones"], full=True, code=True,
               hint='JSON 格式：{"0":10,"1":5,"2":3}'),
    ]

    # 集成
    integrate = ui.div(cls="settings-grid")[
        _toggle("mcp_enabled", "启用 MCP", SETTINGS["mcp_enabled"],
                hint="加载 mcp.json 中的 MCP 服务器，蚁后可调用外部工具"),
        _field("mcp_config", "MCP 配置", SETTINGS["mcp_config"], code=True,
               hint="mcp.json 的相对或绝对路径"),
        _toggle("skills_enabled", "启用 Skills", SETTINGS["skills_enabled"],
                hint="选中 Skill 时将 SKILL.md 注入上下文"),
        _field("skills_dir", "Skills 目录", SETTINGS["skills_dir"], full=True, code=True,
               hint="包含 SKILL.md 的文件夹", browse="onBrowseSkillsDir()"),
    ]

    # 外观
    appear = ui.div(cls="settings-grid")[
        ui.div(cls="field full")[
            ui.raw('<div class="field-label">主题</div><div class="field-hint">点击即时预览</div>'),
            _theme_buttons(),
        ],
        _field("bg_image", "背景图片", APPEARANCE["bg_image"], full=True, code=True,
               hint="URL 或本地路径，留空使用主题渐变"),
    ]

    # 自定义指令
    custom = _field_area(
        "custom_agent", "自定义 Agent 指令", APPEARANCE["custom_agent"], code=True,
        hint="追加到 system prompt 尾部，优先级最高",
    )

    return ui.div(cls="modal settings-modal", id="settings-modal",
                  onclick="if(event.target===this) closeSettings()")[
        ui.div(cls="modal-card settings-card", onclick="event.stopPropagation()")[
            # 头部
            ui.div(cls="settings-header")[
                ui.div()[
                    ui.h2()["⚙ 设置"],
                    ui.p(cls="settings-sub")["配置模型连接、蚁巢参数、集成与外观"],
                ],
                ui.raw('<button type="button" class="modal-close" onclick="closeSettings()" title="关闭">×</button>'),
            ],
            # 内容
            ui.div(cls="settings-body")[
                _settings_section("🔌 LLM 连接", llm),
                _settings_section("🐜 蚁巢参数", agent),
                _settings_section("🔗 集成", integrate),
                _settings_section("🎨 外观", appear),
                _settings_section("📝 自定义 Agent", custom),
                ui.raw(f'<input type="hidden" id="set-theme" value="{APPEARANCE["theme"]}">'),
            ],
            # 底部按钮
            ui.div(cls="settings-footer")[
                ui.raw('<span class="verify-hint" id="verify-hint"></span>'),
                ui.raw('<button type="button" class="btn ghost" onclick="openSkillsModal()">📦 Skills</button>'),
                ui.raw('<button type="button" class="btn ghost" onclick="onListModels()">🔍 检测模型</button>'),
                ui.raw('<button type="button" class="btn ghost" onclick="onTestApi()">🧪 测试连接</button>'),
                ui.raw('<button type="button" class="btn ghost" onclick="closeSettings()">取消</button>'),
                ui.raw('<button type="button" class="btn primary" onclick="onSaveSettings()">💾 保存并校验</button>'),
            ],
        ],
    ]
