package com.antnest.app

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.*

/**
 * 记忆管理器
 * - 短期记忆：SharedPreferences 中的最近对话
 * - 长期记忆：压缩后的历史摘要
 * - 自动压缩：当对话超过阈值时，自动压缩旧对话
 */
class MemoryManager(private val context: Context) {

    private val prefs: SharedPreferences = context.getSharedPreferences("antnest_memory", Context.MODE_PRIVATE)
    private val memoryDir = File(context.filesDir, "memories").apply { mkdirs() }

    companion object {
        private const val MAX_SHORT_TERM = 50        // 短期记忆最大条数
        private const val COMPRESS_THRESHOLD = 30    // 超过此条数触发压缩
        private const val MAX_LONG_TERM = 20         // 长期记忆最大摘要数
    }

    /**
     * 保存一条对话到短期记忆
     */
    fun saveMessage(role: String, content: String) {
        val history = loadShortTerm()
        history.put(JSONObject().apply {
            put("role", role)
            put("content", content)
            put("timestamp", System.currentTimeMillis())
        })

        // 超过阈值时压缩
        if (history.length() >= COMPRESS_THRESHOLD) {
            compressMemory(history)
        } else {
            saveShortTerm(history)
        }
    }

    /**
     * 加载短期记忆（最近的对话）
     */
    fun loadShortTerm(): JSONArray {
        val json = prefs.getString("short_term", null) ?: return JSONArray()
        return try { JSONArray(json) } catch (e: Exception) { JSONArray() }
    }

    /**
     * 加载长期记忆（压缩摘要）
     */
    fun loadLongTerm(): List<String> {
        val result = mutableListOf<String>()
        val files = memoryDir.listFiles()?.sortedByDescending { it.name }?.take(MAX_LONG_TERM) ?: emptyList()
        for (file in files) {
            try {
                result.add(file.readText(Charsets.UTF_8))
            } catch (_: Exception) {}
        }
        return result
    }

    /**
     * 获取当前记忆状态
     */
    fun getStatus(): String {
        val shortCount = loadShortTerm().length()
        val longCount = memoryDir.listFiles()?.size ?: 0
        return "短期记忆: $shortCount 条 | 长期记忆: $longCount 条摘要"
    }

    /**
     * 构建包含记忆的系统提示
     */
    fun buildMemoryPrompt(basePrompt: String): String {
        val sb = StringBuilder(basePrompt)
        sb.append("\n\n## 记忆状态\n")
        sb.append(getStatus())
        sb.append("\n")

        // 加载长期记忆摘要
        val longTerm = loadLongTerm()
        if (longTerm.isNotEmpty()) {
            sb.append("\n## 历史记忆摘要\n")
            for (summary in longTerm) {
                sb.append("- ").append(summary).append("\n")
            }
        }

        // 加载最近短期记忆（最近 10 条）
        val shortTerm = loadShortTerm()
        if (shortTerm.length() > 0) {
            sb.append("\n## 最近对话\n")
            val start = maxOf(0, shortTerm.length() - 10)
            for (i in start until shortTerm.length()) {
                val msg = shortTerm.getJSONObject(i)
                val role = msg.getString("role")
                val content = msg.getString("content").take(200)
                sb.append(if (role == "user") "用户: " else "AI: ")
                sb.append(content).append("\n")
            }
        }

        return sb.toString()
    }

    /**
     * 压缩记忆：将旧对话合并为摘要，保留最近的
     */
    private fun compressMemory(history: JSONArray) {
        // 保留最近 10 条
        val keep = 10
        val toCompress = JSONArray()
        val toKeep = JSONArray()

        for (i in 0 until history.length()) {
            if (i < history.length() - keep) {
                toCompress.put(history.getJSONObject(i))
            } else {
                toKeep.put(history.getJSONObject(i))
            }
        }

        // 生成摘要
        val summary = compressToSummary(toCompress)
        if (summary.isNotEmpty()) {
            val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
            val summaryFile = File(memoryDir, "summary_$timestamp.txt")
            summaryFile.writeText(summary, Charsets.UTF_8)
        }

        // 清理旧摘要（保留最近 MAX_LONG_TERM 个）
        val allSummaries = memoryDir.listFiles()?.sortedByDescending { it.name }
        if (allSummaries != null && allSummaries.size > MAX_LONG_TERM) {
            for (file in allSummaries.drop(MAX_LONG_TERM)) {
                file.delete()
            }
        }

        // 保存保留的短期记忆
        saveShortTerm(toKeep)
    }

    /**
     * 将对话压缩为摘要文本
     */
    private fun compressToSummary(messages: JSONArray): String {
        val sb = StringBuilder()
        val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault())

        // 统计信息
        var userMsgCount = 0
        var aiMsgCount = 0
        val topics = mutableSetOf<String>()
        val toolsUsed = mutableSetOf<String>()

        for (i in 0 until messages.length()) {
            val msg = messages.getJSONObject(i)
            val role = msg.getString("role")
            val content = msg.getString("content")

            if (role == "user") {
                userMsgCount++
                // 提取关键词作为话题
                val keywords = extractKeywords(content)
                topics.addAll(keywords)
            } else {
                aiMsgCount++
            }
        }

        // 时间范围
        val firstTime = messages.getJSONObject(0).optLong("timestamp", 0)
        val lastTime = messages.getJSONObject(messages.length() - 1).optLong("timestamp", 0)
        if (firstTime > 0 && lastTime > 0) {
            sb.append("时间: ${dateFormat.format(Date(firstTime))} ~ ${dateFormat.format(Date(lastTime))}\n")
        }

        sb.append("对话: 用户 $userMsgCount 条, AI $aiMsgCount 条\n")

        if (topics.isNotEmpty()) {
            sb.append("话题: ${topics.joinToString(", ")}\n")
        }

        // 提取最后一条用户消息作为摘要
        for (i in messages.length() - 1 downTo 0) {
            val msg = messages.getJSONObject(i)
            if (msg.getString("role") == "user") {
                sb.append("最后话题: ${msg.getString("content").take(100)}\n")
                break
            }
        }

        return sb.toString()
    }

    /**
     * 从文本中提取关键词
     */
    private fun extractKeywords(text: String): List<String> {
        val keywords = mutableListOf<String>()
        val lower = text.lowercase()

        // 简单关键词提取
        val patterns = listOf(
            "文件" to "文件管理",
            "wifi" to "WiFi",
            "蓝牙" to "蓝牙",
            "内存" to "内存",
            "传感器" to "传感器",
            "设置" to "设置",
            "api" to "API配置",
            "图片" to "图片",
            "搜索" to "搜索",
            "代码" to "编程",
            "bug" to "问题排查",
            "错误" to "问题排查"
        )

        for ((keyword, topic) in patterns) {
            if (lower.contains(keyword)) {
                keywords.add(topic)
            }
        }

        return keywords.distinct().take(5)
    }

    /**
     * 清空所有记忆
     */
    fun clearAll() {
        prefs.edit().clear().apply()
        memoryDir.listFiles()?.forEach { it.delete() }
    }

    private fun saveShortTerm(history: JSONArray) {
        prefs.edit().putString("short_term", history.toString()).apply()
    }
}
