package com.antnest.app.ui.screens

import android.app.Application
import android.content.Context
import android.content.SharedPreferences
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.antnest.app.data.api.LlmApiClient
import com.antnest.app.data.model.*
import com.antnest.app.tools.*
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * 对话 ViewModel
 */
class ChatViewModel(application: Application) : AndroidViewModel(application) {
    private val context = application.applicationContext
    private val api = LlmApiClient()
    private val gson = Gson()
    private val prefs: SharedPreferences = context.getSharedPreferences("antnest_config", Context.MODE_PRIVATE)

    // 工具注册表（延迟初始化，防止构造时崩溃）
    private val toolRegistry: ToolRegistry by lazy {
        ToolRegistry(context).apply {
            try {
                register(FindFilesTool(context))
                register(PreviewFileTool(context))
                register(GetWifiInfoTool(context))
                register(ScanBluetoothTool(context))
                register(GetSensorDataTool(context))
                register(GetMemoryInfoTool(context))
            } catch (e: Exception) {
                // 工具注册失败不影响主流程
                android.util.Log.e("AntNest", "Tool registration failed", e)
            }
        }
    }

    // 对话消息列表
    private val _messages = MutableStateFlow<List<Message>>(emptyList())
    val messages: StateFlow<List<Message>> = _messages.asStateFlow()

    // 是否正在生成
    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    // 状态文本
    private val _statusText = MutableStateFlow("")
    val statusText: StateFlow<String> = _statusText.asStateFlow()

    // API 配置
    private val _baseUrl = MutableStateFlow(
        try { prefs.getString("base_url", "https://api.deepseek.com/v1") ?: "https://api.deepseek.com/v1" }
        catch (e: Exception) { "https://api.deepseek.com/v1" }
    )
    val baseUrl: StateFlow<String> = _baseUrl.asStateFlow()

    private val _apiKey = MutableStateFlow(
        try { prefs.getString("api_key", "") ?: "" }
        catch (e: Exception) { "" }
    )
    val apiKey: StateFlow<String> = _apiKey.asStateFlow()

    private val _modelName = MutableStateFlow(
        try { prefs.getString("model_name", "deepseek-chat") ?: "deepseek-chat" }
        catch (e: Exception) { "deepseek-chat" }
    )
    val modelName: StateFlow<String> = _modelName.asStateFlow()

    // 当前页面
    private val _currentScreen = MutableStateFlow("chat")
    val currentScreen: StateFlow<String> = _currentScreen.asStateFlow()

    // 错误信息
    private val _errorMessage = MutableStateFlow<String?>(null)
    val errorMessage: StateFlow<String?> = _errorMessage.asStateFlow()

    init {
        // 启动时检查配置，没有 key 则跳到设置页
        try {
            if (_apiKey.value.isBlank()) {
                _currentScreen.value = "settings"
            }
        } catch (e: Exception) {
            _currentScreen.value = "settings"
            _errorMessage.value = "初始化失败: ${e.message}"
        }
    }

    /**
     * 保存配置
     */
    fun saveConfig(baseUrl: String, apiKey: String, modelName: String) {
        _baseUrl.value = baseUrl
        _apiKey.value = apiKey
        _modelName.value = modelName

        try {
            prefs.edit().apply {
                putString("base_url", baseUrl)
                putString("api_key", apiKey)
                putString("model_name", modelName)
                apply()
            }
        } catch (e: Exception) {
            _errorMessage.value = "保存失败: ${e.message}"
        }
    }

    /**
     * 切换页面
     */
    fun navigateTo(screen: String) {
        _currentScreen.value = screen
    }

    /**
     * 清除错误
     */
    fun clearError() {
        _errorMessage.value = null
    }

    /**
     * 发送用户消息
     */
    fun sendMessage(userText: String) {
        if (userText.isBlank() || _isLoading.value) return
        if (_apiKey.value.isBlank()) {
            _messages.value = _messages.value + Message(
                role = MessageRole.SYSTEM,
                content = "请先在设置中配置 API Key"
            )
            _currentScreen.value = "settings"
            return
        }

        _messages.value = _messages.value + Message(role = MessageRole.USER, content = userText)
        _isLoading.value = true

        viewModelScope.launch {
            try {
                agentLoop()
            } catch (e: Exception) {
                _messages.value = _messages.value + Message(
                    role = MessageRole.ASSISTANT,
                    content = "❌ 错误: ${e.message}"
                )
            } finally {
                _isLoading.value = false
                _statusText.value = ""
            }
        }
    }

    /**
     * Agent 循环
     */
    private suspend fun agentLoop() {
        val maxRounds = 10
        var round = 0

        while (round < maxRounds) {
            round++
            _statusText.value = "思考中...（第 $round 轮）"

            val apiMessages = buildApiMessages()

            val response = api.chat(
                baseUrl = _baseUrl.value,
                apiKey = _apiKey.value,
                model = _modelName.value,
                messages = apiMessages,
                tools = try { toolRegistry.getDefinitions() } catch (e: Exception) { emptyList() }
            ).getOrElse { e ->
                _messages.value = _messages.value + Message(
                    role = MessageRole.ASSISTANT,
                    content = "❌ API 调用失败: ${e.message}"
                )
                return
            }

            val choice = response.choices.firstOrNull() ?: return
            val msg = choice.message

            if (!msg.toolCalls.isNullOrEmpty()) {
                _messages.value = _messages.value + Message(
                    role = MessageRole.ASSISTANT,
                    content = msg.content ?: "",
                    toolCalls = msg.toolCalls
                )

                for (toolCall in msg.toolCalls) {
                    _statusText.value = "执行工具: ${toolCall.function.name}"

                    val args: Map<String, Any> = try {
                        val type = object : TypeToken<Map<String, Any>>() {}.type
                        gson.fromJson(toolCall.function.arguments, type)
                    } catch (e: Exception) {
                        emptyMap()
                    }

                    val result = try {
                        toolRegistry.execute(toolCall.function.name, args)
                    } catch (e: Exception) {
                        """{"error": "工具执行异常: ${e.message}"}"""
                    }

                    _messages.value = _messages.value + Message(
                        role = MessageRole.TOOL,
                        content = result,
                        toolCallId = toolCall.id,
                        name = toolCall.function.name
                    )
                }
                continue
            }

            _messages.value = _messages.value + Message(
                role = MessageRole.ASSISTANT,
                content = msg.content ?: "",
                reasoning = msg.reasoningContent
            )
            return
        }

        _messages.value = _messages.value + Message(
            role = MessageRole.ASSISTANT,
            content = "⚠️ 达到最大轮次（$maxRounds），已停止。"
        )
    }

    /**
     * 构建 API 消息列表
     */
    private fun buildApiMessages(): List<Map<String, Any>> {
        val systemPrompt = """
你是 AntNest，一个运行在手机上的 AI 助手。

你可以：
- 帮用户查找手机里的文件
- 预览文件内容
- 查看 WiFi 连接信息
- 扫描蓝牙设备
- 读取传感器数据
- 查看内存使用情况

用户不会直接调用工具，而是用自然语言告诉你需求，你根据需要调用合适的工具。
调用工具后，用通俗易懂的语言把结果告诉用户。
回复使用中文。
""".trimIndent()

        val apiMessages = mutableListOf<Map<String, Any>>()
        apiMessages.add(mapOf("role" to "system", "content" to systemPrompt))

        for (msg in _messages.value) {
            when (msg.role) {
                MessageRole.USER -> {
                    apiMessages.add(mapOf("role" to "user", "content" to msg.content))
                }
                MessageRole.ASSISTANT -> {
                    val m = mutableMapOf<String, Any>("role" to "assistant", "content" to (msg.content.ifEmpty { " " }))
                    if (!msg.toolCalls.isNullOrEmpty()) {
                        m["tool_calls"] = msg.toolCalls.map { tc ->
                            mapOf(
                                "id" to tc.id,
                                "type" to "function",
                                "function" to mapOf(
                                    "name" to tc.function.name,
                                    "arguments" to tc.function.arguments
                                )
                            )
                        }
                    }
                    apiMessages.add(m)
                }
                MessageRole.TOOL -> {
                    apiMessages.add(mapOf(
                        "role" to "tool",
                        "tool_call_id" to (msg.toolCallId ?: ""),
                        "content" to msg.content
                    ))
                }
                MessageRole.SYSTEM -> {
                    apiMessages.add(mapOf("role" to "system", "content" to msg.content))
                }
            }
        }

        return apiMessages
    }

    /**
     * 清空对话
     */
    fun clearMessages() {
        _messages.value = emptyList()
    }
}
