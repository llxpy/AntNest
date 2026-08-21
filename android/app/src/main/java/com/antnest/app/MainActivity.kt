package com.antnest.app

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.EditText
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.util.concurrent.Executors

class MainActivity : Activity() {

    private lateinit var inputText: EditText
    private lateinit var btnSend: ImageButton
    private lateinit var btnSettings: ImageButton
    private lateinit var messagesContainer: LinearLayout
    private lateinit var scrollMessages: ScrollView
    private lateinit var memoryStatus: TextView

    private val apiClient by lazy { ApiClient(this) }
    private val memoryManager by lazy { MemoryManager(this) }
    private val executor = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        inputText = findViewById(R.id.input_text)
        btnSend = findViewById(R.id.btn_send)
        btnSettings = findViewById(R.id.btn_settings)
        messagesContainer = findViewById(R.id.messages_container)
        scrollMessages = findViewById(R.id.scroll_messages)

        // 记忆状态
        memoryStatus = TextView(this).apply {
            textSize = 11f
            setTextColor(Color.parseColor("#484F58"))
            setPadding(0, 4, 0, 4)
            gravity = Gravity.CENTER
        }

        // 加载历史对话
        loadHistory()

        // 更新记忆状态
        updateMemoryStatus()

        // 发送按钮
        btnSend.setOnClickListener { sendMessage() }

        // 设置按钮
        btnSettings.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        // 快捷建议
        findViewById<TextView>(R.id.suggest_files)?.setOnClickListener {
            inputText.setText("帮我找一下手机里的 PDF 文件")
        }
        findViewById<TextView>(R.id.suggest_wifi)?.setOnClickListener {
            inputText.setText("现在 WiFi 连接的是什么网络？")
        }
        findViewById<TextView>(R.id.suggest_bluetooth)?.setOnClickListener {
            inputText.setText("扫描周围的蓝牙设备")
        }
        findViewById<TextView>(R.id.suggest_memory)?.setOnClickListener {
            inputText.setText("手机内存还剩多少？")
        }

        // 检查 API Key
        val prefs = getSharedPreferences("antnest_config", MODE_PRIVATE)
        if (prefs.getString("api_key", "").isNullOrEmpty()) {
            addSystemMessage("请先点击右上角 ⚙ 配置 API Key")
        }
    }

    override fun onResume() {
        super.onResume()
        updateMemoryStatus()
    }

    private fun loadHistory() {
        val history = memoryManager.loadShortTerm()
        if (history.length() == 0) return

        // 隐藏欢迎区域
        hideWelcome()

        // 显示最近的历史对话（最多 20 条）
        val start = maxOf(0, history.length() - 20)
        for (i in start until history.length()) {
            val msg = history.getJSONObject(i)
            val role = msg.getString("role")
            val content = msg.getString("content")
            if (role == "user") {
                addUserMessage(content, false)  // false = 不保存到记忆（已经在了）
            } else {
                addAIMessage(content, false)
            }
        }
        scrollToBottom()
    }

    private fun updateMemoryStatus() {
        memoryStatus.text = memoryManager.getStatus()
    }

    private fun sendMessage() {
        val text = inputText.text.toString().trim()
        if (text.isEmpty()) return

        inputText.setText("")
        addUserMessage(text, true)
        hideWelcome()

        // 保存到记忆
        memoryManager.saveMessage("user", text)

        // 显示思考中
        val thinkingView = addThinkingMessage()

        // 构建带记忆的系统提示
        val systemPrompt = memoryManager.buildMemoryPrompt(
            "你是 AntNest，一个运行在手机上的 AI 助手。\n" +
            "你可以帮用户查找手机文件、查看 WiFi、扫描蓝牙、读取传感器等。\n" +
            "回复使用中文，简洁明了。"
        )

        // 后台调用 API
        executor.execute {
            val response = apiClient.chat(text, systemPrompt)
            handler.post {
                messagesContainer.removeView(thinkingView)
                addAIMessage(response, true)
                memoryManager.saveMessage("assistant", response)
                updateMemoryStatus()
            }
        }
    }

    private fun hideWelcome() {
        if (messagesContainer.childCount > 0) {
            val firstChild = messagesContainer.getChildAt(0)
            if (firstChild.tag == "welcome") {
                messagesContainer.removeViewAt(0)
            }
        }
    }

    private fun addUserMessage(text: String, save: Boolean = true) {
        val container = LinearLayout(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = 16 }
            gravity = Gravity.END
            orientation = LinearLayout.VERTICAL
        }

        val label = TextView(this).apply {
            this.text = "你"
            textSize = 11f
            setTextColor(Color.parseColor("#8B949E"))
            setPadding(0, 0, 0, 4)
            gravity = Gravity.END
        }

        val bubble = TextView(this).apply {
            this.text = text
            textSize = 14f
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.parseColor("#1F6FEB"))
            setPadding(24, 16, 24, 16)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply {
                maxWidth = (resources.displayMetrics.widthPixels * 0.75).toInt()
            }
        }

        container.addView(label)
        container.addView(bubble)
        messagesContainer.addView(container)
        scrollToBottom()
    }

    private fun addAIMessage(text: String, save: Boolean = true) {
        val container = LinearLayout(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = 16 }
            gravity = Gravity.START
            orientation = LinearLayout.VERTICAL
        }

        val label = TextView(this).apply {
            this.text = "🐜 AntNest"
            textSize = 11f
            setTextColor(Color.parseColor("#8B949E"))
            setPadding(0, 0, 0, 4)
        }

        val bubble = TextView(this).apply {
            this.text = text
            textSize = 14f
            setTextColor(Color.parseColor("#C9D1D9"))
            setBackgroundColor(Color.parseColor("#161B22"))
            setPadding(24, 16, 24, 16)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply {
                maxWidth = (resources.displayMetrics.widthPixels * 0.75).toInt()
            }
        }

        container.addView(label)
        container.addView(bubble)
        messagesContainer.addView(container)
        scrollToBottom()
    }

    private fun addThinkingMessage(): LinearLayout {
        val container = LinearLayout(this).apply {
            tag = "thinking"
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = 16 }
            gravity = Gravity.START
            orientation = LinearLayout.VERTICAL
        }

        val label = TextView(this).apply {
            this.text = "🐜 AntNest"
            textSize = 11f
            setTextColor(Color.parseColor("#8B949E"))
            setPadding(0, 0, 0, 4)
        }

        val bubble = TextView(this).apply {
            this.text = "思考中..."
            textSize = 14f
            setTextColor(Color.parseColor("#484F58"))
            setBackgroundColor(Color.parseColor("#161B22"))
            setPadding(24, 16, 24, 16)
        }

        container.addView(label)
        container.addView(bubble)
        messagesContainer.addView(container)
        scrollToBottom()
        return container
    }

    private fun addSystemMessage(text: String) {
        val msg = TextView(this).apply {
            this.text = text
            textSize = 13f
            setTextColor(Color.parseColor("#58A6FF"))
            setPadding(0, 12, 0, 12)
            gravity = Gravity.CENTER
            tag = "welcome"
        }
        messagesContainer.addView(msg)
        scrollToBottom()
    }

    private fun scrollToBottom() {
        scrollMessages.post {
            scrollMessages.fullScroll(ScrollView.FOCUS_DOWN)
        }
    }
}
