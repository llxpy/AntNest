package com.antnest.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.TextView

/**
 * 启动页 Activity
 * 显示 logo 和加载动画，2 秒后跳转到主界面
 */
class SplashActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_splash)

        val statusText = findViewById<TextView>(R.id.status_text)

        // 模拟初始化过程
        Handler(Looper.getMainLooper()).postDelayed({
            statusText?.text = "加载配置..."
        }, 500)

        Handler(Looper.getMainLooper()).postDelayed({
            statusText?.text = "准备就绪"
        }, 1200)

        // 2 秒后跳转到主界面
        Handler(Looper.getMainLooper()).postDelayed({
            startActivity(Intent(this, MainActivity::class.java))
            finish()
        }, 2000)
    }
}
