# AntNest Android

<p align="center">
  <img src="app/src/main/res/mipmap-xxxhdpi/ic_launcher.png" width="120" alt="AntNest Logo">
</p>

<h3 align="center">🐜 AntNest — 运行在手机上的 AI 助手</h3>

<p align="center">
  <a href="https://github.com/llxpy/AntNest">桌面版</a> ·
  <strong>Android 版</strong> ·
  <a href="https://github.com/llxpy">GitHub</a>
</p>

---

## 功能

- **AI 对话** — 兼容 OpenAI API 格式，支持 DeepSeek / Kimi / MiniMax / OpenAI 等任意兼容 API
- **持久化记忆** — 对话记录本地保存，重启自动恢复，超 30 条自动压缩为摘要
- **文件查找** — 搜索手机中的文件（开发中）
- **WiFi 监控** — 查看当前 WiFi 连接信息（开发中）
- **蓝牙扫描** — 扫描周围蓝牙设备（开发中）
- **传感器读取** — 读取手机传感器数据（开发中）

## 截图

| 启动页 | 对话界面 | 设置页 |
|--------|----------|--------|
| 暗色主题 | 对话气泡 | API 配置 |

## 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Kotlin |
| UI | 原生 Android View + XML 布局 |
| 主题 | 暗色 (#0D1117)，GitHub 风格 |
| 网络 | HttpURLConnection（零依赖） |
| 存储 | SharedPreferences + 内部文件 |
| 记忆 | 短期（最近对话）+ 长期（压缩摘要） |
| 构建 | Gradle 8.5 + AGP 8.2.0 |

## 快速开始

### 安装 APK

从 [Releases](https://github.com/llxpy/AntNest-Android/releases) 下载 `app-debug.apk`，传到手机安装。

### 从源码构建

```bash
# 需要 Android SDK + JDK 17+
git clone https://github.com/llxpy/AntNest-Android.git
cd AntNest-Android
./gradlew assembleDebug
```

APK 输出：`app/build/outputs/apk/debug/app-debug.apk`

### 首次使用

1. 打开 App → 启动页 → 自动跳转到对话界面
2. 点击右上角 ⚙ 进入设置
3. 选择 API 提供商（DeepSeek / Kimi / OpenAI 等）
4. 填入你的 API Key
5. 保存 → 返回对话 → 开始聊天

## 项目结构

```
app/src/main/java/com/antnest/app/
├── SplashActivity.kt      # 启动页（2秒后跳转）
├── MainActivity.kt         # 主对话界面
├── SettingsActivity.kt     # 设置页（API 配置）
├── ApiClient.kt            # LLM API 客户端（OpenAI 兼容）
├── MemoryManager.kt        # 持久化记忆 + 压缩
├── AntNestApp.kt           # Application 类
└── tools/                  # 工具系统（预留）
    ├── ToolRegistry.kt     # 工具注册中心
    └── BuiltinTools.kt     # 内置工具（WiFi/蓝牙/传感器/文件）
```

## 记忆系统

| 类型 | 存储位置 | 最大条数 | 触发压缩 |
|------|----------|----------|----------|
| 短期记忆 | SharedPreferences | 50 条 | 超过 30 条 |
| 长期摘要 | 内部文件 `/memories/` | 20 个摘要文件 | 自动 |

压缩时保留最近 10 条对话，其余合并为摘要（时间、话题、关键词）。

## API 兼容性

任何兼容 OpenAI `/v1/chat/completions` 格式的 API 都可以使用：

| 提供商 | Base URL | 模型 |
|--------|----------|------|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Kimi | `https://api.moonshot.cn/v1` | `kimi-k2.6` |
| MiniMax | `https://api.minimax.chat/v1` | `MiniMax-M2.5` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| 兼容 API | 任意 | 任意 |

## 权限说明

| 权限 | 用途 |
|------|------|
| `INTERNET` | API 调用 |
| `ACCESS_WIFI_STATE` | WiFi 信息读取 |
| `BLUETOOTH_*` | 蓝牙扫描 |
| `ACCESS_FINE_LOCATION` | WiFi/蓝牙扫描需要 |
| `READ/WRITE_EXTERNAL_STORAGE` | 文件查找 |
| `BODY_SENSORS` | 传感器读取 |

## 许可证

MIT License — 与 [AntNest 桌面版](https://github.com/llxpy/AntNest) 相同。
