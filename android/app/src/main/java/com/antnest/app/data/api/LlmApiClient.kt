package com.antnest.app.data.api

import com.antnest.app.data.model.*
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.google.gson.reflect.TypeToken
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * LLM API 客户端
 * 和桌面版 AntNest 的 llm_chat_stream 同一个设计
 */
class LlmApiClient {
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private val gson = Gson()

    /**
     * 发送对话请求（非流式）
     */
    suspend fun chat(
        baseUrl: String,
        apiKey: String,
        model: String,
        messages: List<Map<String, Any>>,
        tools: List<Map<String, Any>>? = null,
        temperature: Double = 0.6
    ): Result<ChatResponse> = withContext(Dispatchers.IO) {
        try {
            val body = mutableMapOf<String, Any>(
                "model" to model,
                "messages" to messages,
                "temperature" to temperature
            )
            if (!tools.isNullOrEmpty()) {
                body["tools"] = tools
            }

            val json = gson.toJson(body)
            val request = Request.Builder()
                .url("$baseUrl/chat/completions")
                .addHeader("Authorization", "Bearer $apiKey")
                .addHeader("Content-Type", "application/json")
                .post(json.toRequestBody("application/json".toMediaType()))
                .build()

            val response = client.newCall(request).execute()
            val responseBody = response.body?.string() ?: ""

            if (!response.isSuccessful) {
                return@withContext Result.failure(IOException("API 错误 ${response.code}: $responseBody"))
            }

            val chatResponse = gson.fromJson(responseBody, ChatResponse::class.java)
            Result.success(chatResponse)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    /**
     * 获取模型列表
     */
    suspend fun listModels(
        baseUrl: String,
        apiKey: String
    ): Result<List<String>> = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url("$baseUrl/models")
                .addHeader("Authorization", "Bearer $apiKey")
                .get()
                .build()

            val response = client.newCall(request).execute()
            val body = response.body?.string() ?: ""

            if (!response.isSuccessful) {
                return@withContext Result.failure(IOException("API 错误 ${response.code}: $body"))
            }

            val json = JsonParser.parseString(body).asJsonObject
            val data = json.getAsJsonArray("data")
            val models = data.map { it.asJsonObject.get("id").asString }

            Result.success(models)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
}
