package com.antnest.app

import android.app.Activity
import android.content.Context
import android.os.Bundle
import android.widget.EditText
import android.widget.ImageButton
import android.widget.TextView

class SettingsActivity : Activity() {

    private lateinit var inputBaseUrl: EditText
    private lateinit var inputApiKey: EditText
    private lateinit var inputModel: EditText
    private lateinit var saveStatus: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        inputBaseUrl = findViewById(R.id.input_base_url)
        inputApiKey = findViewById(R.id.input_api_key)
        inputModel = findViewById(R.id.input_model)
        saveStatus = findViewById(R.id.save_status)

        // 加载已保存的配置
        val prefs = getSharedPreferences("antnest_config", Context.MODE_PRIVATE)
        inputBaseUrl.setText(prefs.getString("base_url", "https://api.deepseek.com/v1"))
        inputApiKey.setText(prefs.getString("api_key", ""))
        inputModel.setText(prefs.getString("model_name", "deepseek-chat"))

        // 返回按钮（ImageButton，不是 TextView）
        findViewById<ImageButton>(R.id.btn_back)?.setOnClickListener {
            finish()
        }

        // 保存按钮
        findViewById<TextView>(R.id.btn_save)?.setOnClickListener {
            saveConfig()
        }

        // 快捷配置
        findViewById<TextView>(R.id.preset_deepseek)?.setOnClickListener {
            inputBaseUrl.setText("https://api.deepseek.com/v1")
            inputModel.setText("deepseek-chat")
        }
        findViewById<TextView>(R.id.preset_kimi)?.setOnClickListener {
            inputBaseUrl.setText("https://api.moonshot.cn/v1")
            inputModel.setText("kimi-k2.6")
        }
        findViewById<TextView>(R.id.preset_minimax)?.setOnClickListener {
            inputBaseUrl.setText("https://api.minimax.chat/v1")
            inputModel.setText("MiniMax-M2.5")
        }
        findViewById<TextView>(R.id.preset_openai)?.setOnClickListener {
            inputBaseUrl.setText("https://api.openai.com/v1")
            inputModel.setText("gpt-4o")
        }
    }

    private fun saveConfig() {
        val baseUrl = inputBaseUrl.text.toString().trim()
        val apiKey = inputApiKey.text.toString().trim()
        val model = inputModel.text.toString().trim()

        if (baseUrl.isEmpty() || apiKey.isEmpty() || model.isEmpty()) {
            saveStatus.text = "❌ 请填写所有配置项"
            saveStatus.setTextColor(android.graphics.Color.parseColor("#F85149"))
            return
        }

        getSharedPreferences("antnest_config", Context.MODE_PRIVATE).edit().apply {
            putString("base_url", baseUrl)
            putString("api_key", apiKey)
            putString("model_name", model)
            apply()
        }

        saveStatus.text = "✓ 配置已保存"
        saveStatus.setTextColor(android.graphics.Color.parseColor("#3FB950"))

        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            saveStatus.text = ""
        }, 2000)
    }
}
