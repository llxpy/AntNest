package com.antnest.app

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

/**
 * LLM API 客户端
 * 直接用 HttpURLConnection，不依赖 OkHttp
 */
class ApiClient(private val context: Context) {

    /**
     * 发送消息到 LLM API
     * @return AI 回复文本，失败返回错误信息
     */
    fun chat(userMessage: String, systemPrompt: String = ""): String {
        val prefs = context.getSharedPreferences("antnest_config", Context.MODE_PRIVATE)
        val baseUrl = prefs.getString("base_url", "") ?: ""
        val apiKey = prefs.getString("api_key", "") ?: ""
        val model = prefs.getString("model_name", "deepseek-chat") ?: "deepseek-chat"

        if (apiKey.isEmpty()) {
            return "❌ 请先在设置中配置 API Key"
        }
        if (baseUrl.isEmpty()) {
            return "❌ 请先在设置中配置 API Base URL"
        }

        // 构建请求体
        val messages = JSONArray()

        // 系统提示
        val sysPrompt = if (systemPrompt.isNotEmpty()) systemPrompt else
            "你是 AntNest，一个运行在手机上的 AI 助手。你可以帮用户查找手机文件、查看 WiFi、扫描蓝牙、读取传感器等。回复使用中文，简洁明了。"

        messages.put(JSONObject().apply {
            put("role", "system")
            put("content", sysPrompt)
        })

        messages.put(JSONObject().apply {
            put("role", "user")
            put("content", userMessage)
        })

        val requestBody = JSONObject().apply {
            put("model", model)
            put("messages", messages)
            put("temperature", 0.7)
            put("max_tokens", 2000)
        }

        // 发送请求
        return try {
            val url = URL("${baseUrl.trimEnd('/')}/chat/completions")
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("Authorization", "Bearer $apiKey")
            conn.doOutput = true
            conn.connectTimeout = 30000
            conn.readTimeout = 60000

            OutputStreamWriter(conn.outputStream, Charsets.UTF_8).use { writer ->
                writer.write(requestBody.toString())
                writer.flush()
            }

            val responseCode = conn.responseCode
            if (responseCode == 200) {
                val response = BufferedReader(InputStreamReader(conn.inputStream, Charsets.UTF_8)).use { it.readText() }
                val json = JSONObject(response)
                val choices = json.getJSONArray("choices")
                if (choices.length() > 0) {
                    val message = choices.getJSONObject(0).getJSONObject("message")
                    message.getString("content")
                } else {
                    "❌ API 返回空响应"
                }
            } else {
                val error = BufferedReader(InputStreamReader(conn.errorStream ?: conn.inputStream, Charsets.UTF_8)).use { it.readText() }
                "❌ API 错误 ($responseCode): ${error.take(200)}"
            }
        } catch (e: java.net.ConnectException) {
            "❌ 无法连接到 $baseUrl\n请检查网络和 API 地址"
        } catch (e: java.net.SocketTimeoutException) {
            "❌ 请求超时，请稍后重试"
        } catch (e: Exception) {
            "❌ 错误: ${e.message}"
        }
    }
}
