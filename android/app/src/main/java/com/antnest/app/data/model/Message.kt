package com.antnest.app.data.model

/**
 * 消息角色
 */
enum class MessageRole {
    USER,       // 用户
    ASSISTANT,  // AI
    SYSTEM,     // 系统
    TOOL        // 工具返回
}

/**
 * 对话消息
 */
data class Message(
    val role: MessageRole,
    val content: String,
    val toolCalls: List<ToolCall>? = null,
    val toolCallId: String? = null,
    val name: String? = null,
    val reasoning: String? = null
)

/**
 * 工具调用请求（LLM 返回的）
 */
data class ToolCall(
    val id: String,
    val type: String,  // "function"
    val function: FunctionCall
)

/**
 * 函数调用
 */
data class FunctionCall(
    val name: String,
    val arguments: String  // JSON 字符串
)

/**
 * 工具定义（传给 LLM 的 schema）
 */
data class ToolDefinition(
    val type: String = "function",
    val function: FunctionDefinition
)

/**
 * 函数定义
 */
data class FunctionDefinition(
    val name: String,
    val description: String,
    val parameters: Map<String, Any>
)

/**
 * API 请求体
 */
data class ChatRequest(
    val model: String,
    val messages: List<Map<String, Any>>,
    val tools: List<Map<String, Any>>? = null,
    val stream: Boolean = false,
    val temperature: Double = 0.6
)

/**
 * API 响应体
 */
data class ChatResponse(
    val choices: List<Choice>
)

data class Choice(
    val message: ResponseMessage,
    val finishReason: String? = null
)

data class ResponseMessage(
    val role: String,
    val content: String?,
    val toolCalls: List<ToolCall>?,
    val reasoningContent: String?
)
