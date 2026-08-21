package com.antnest.app.tools

import android.content.Context
import com.antnest.app.data.model.ToolDefinition

/**
 * 工具接口：所有工具都实现这个接口
 */
interface Tool {
    /** 工具名称 */
    val name: String

    /** 工具描述（给 LLM 看的） */
    val description: String

    /** 参数 schema（JSON Schema 格式） */
    val parameters: Map<String, Any>

    /** 执行工具 */
    suspend fun execute(args: Map<String, Any>): String
}

/**
 * 工具注册表：管理所有可用工具
 * 和桌面版的 tool_executors 是同一个设计
 */
class ToolRegistry(private val context: Context) {
    private val tools = mutableMapOf<String, Tool>()

    /** 注册工具 */
    fun register(tool: Tool) {
        tools[tool.name] = tool
    }

    /** 获取工具 */
    fun get(name: String): Tool? = tools[name]

    /** 获取所有工具定义（传给 LLM） */
    fun getDefinitions(): List<Map<String, Any>> {
        return tools.values.map { tool ->
            mapOf(
                "type" to "function",
                "function" to mapOf(
                    "name" to tool.name,
                    "description" to tool.description,
                    "parameters" to mapOf(
                        "type" to "object",
                        "properties" to tool.parameters,
                        "required" to tool.parameters.keys.toList()
                    )
                )
            )
        }
    }

    /** 执行工具 */
    suspend fun execute(name: String, args: Map<String, Any>): String {
        val tool = tools[name]
            ?: return """{"error": "工具 '$name' 不存在"}"""
        return try {
            tool.execute(args)
        } catch (e: Exception) {
            """{"error": "${e.message?.replace("\"", "'") ?: "未知错误"}"}"""
        }
    }

    /** 已注册的工具列表 */
    fun listTools(): List<String> = tools.keys.toList()
}
