package com.antnest.app.tools

import android.content.Context
import android.net.wifi.WifiManager
import android.bluetooth.BluetoothAdapter
import android.hardware.Sensor
import android.hardware.SensorManager
import android.os.Environment
import android.provider.MediaStore
import java.io.File

/**
 * 文件搜索工具
 */
class FindFilesTool(private val context: Context) : Tool {
    override val name = "find_files"
    override val description = "在手机中搜索文件。支持按文件名关键词搜索。返回匹配的文件列表（路径、大小、修改时间）。"
    override val parameters = mapOf(
        "keyword" to mapOf(
            "type" to "string",
            "description" to "搜索关键词（文件名）"
        ),
        "directory" to mapOf(
            "type" to "string",
            "description" to "搜索目录（可选，默认搜索整个共享存储）"
        )
    )

    override suspend fun execute(args: Map<String, Any>): String {
        val keyword = args["keyword"] as? String ?: return """{"error": "缺少 keyword 参数"}"""
        val directory = args["directory"] as? String
        
        val root = if (directory != null) {
            File(directory)
        } else {
            Environment.getExternalStorageDirectory()
        }

        if (!root.exists() || !root.canRead()) {
            return """{"error": "目录不存在或无法读取: ${root.absolutePath}"}"""
        }

        val results = mutableListOf<Map<String, Any>>()
        searchFiles(root, keyword.lowercase(), results, maxResults = 20)

        return buildString {
            append("""{"results": [""")
            results.forEachIndexed { index, file ->
                if (index > 0) append(",")
                append("""{"path": "${file["path"]}", "size": ${file["size"]}, "modified": "${file["modified"]}"}""")
            }
            append("""], "count": ${results.size}}""")
        }
    }

    private fun searchFiles(dir: File, keyword: String, results: MutableList<Map<String, Any>>, maxResults: Int) {
        if (results.size >= maxResults) return
        try {
            dir.listFiles()?.forEach { file ->
                if (results.size >= maxResults) return
                if (file.name.lowercase().contains(keyword)) {
                    results.add(mapOf(
                        "path" to file.absolutePath,
                        "size" to file.length(),
                        "modified" to java.text.SimpleDateFormat("yyyy-MM-dd HH:mm").format(file.lastModified())
                    ))
                }
                if (file.isDirectory) {
                    searchFiles(file, keyword, results, maxResults)
                }
            }
        } catch (_: Exception) { }
    }
}

/**
 * 文件预览工具
 */
class PreviewFileTool(private val context: Context) : Tool {
    override val name = "preview_file"
    override val description = "预览文件内容。支持文本文件（读取前 2000 字符）和图片文件（返回路径）。"
    override val parameters = mapOf(
        "path" to mapOf(
            "type" to "string",
            "description" to "文件路径"
        )
    )

    override suspend fun execute(args: Map<String, Any>): String {
        val path = args["path"] as? String ?: return """{"error": "缺少 path 参数"}"""
        val file = File(path)

        if (!file.exists()) return """{"error": "文件不存在: $path"}"""
        if (!file.canRead()) return """{"error": "无法读取文件: $path"}"""

        val ext = file.extension.lowercase()
        return when (ext) {
            "txt", "md", "json", "xml", "csv", "log", "kt", "java", "py", "js", "html", "css" -> {
                val content = file.readText(Charsets.UTF_8).take(2000)
                """{"type": "text", "content": ${escapeJson(content)}, "truncated": ${content.length >= 2000}}"""
            }
            "jpg", "jpeg", "png", "gif", "webp", "bmp" -> {
                """{"type": "image", "path": "${file.absolutePath}", "size": ${file.length()}}"""
            }
            else -> {
                """{"type": "unknown", "path": "${file.absolutePath}", "size": ${file.length()}, "extension": "$ext"}"""
            }
        }
    }

    private fun escapeJson(s: String): String {
        return "\"" + s.replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t") + "\""
    }
}

/**
 * WiFi 信息工具
 */
class GetWifiInfoTool(private val context: Context) : Tool {
    override val name = "get_wifi_info"
    override val description = "获取当前 WiFi 连接信息，包括网络名称、信号强度、IP 地址、连接速度。"
    override val parameters = mapOf<String, Map<String, String>>()  // 无参数

    override suspend fun execute(args: Map<String, Any>): String {
        val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val info = wifiManager.connectionInfo

        return if (info.networkId == -1) {
            """{"connected": false, "message": "未连接 WiFi"}"""
        } else {
            """{"connected": true, "ssid": "${info.ssid}", "bssid": "${info.bssid}", "rssi": ${info.rssi}, "linkSpeed": ${info.linkSpeed}, "ip": "${formatIp(info.ipAddress)}"}"""
        }
    }

    private fun formatIp(ip: Int): String {
        return "${ip and 0xFF}.${ip shr 8 and 0xFF}.${ip shr 16 and 0xFF}.${ip shr 24 and 0xFF}"
    }
}

/**
 * 蓝牙设备扫描工具
 */
class ScanBluetoothTool(private val context: Context) : Tool {
    override val name = "scan_bluetooth"
    override val description = "获取已配对的蓝牙设备列表，包括设备名称和地址。"
    override val parameters = mapOf<String, Map<String, String>>()  // 无参数

    override suspend fun execute(args: Map<String, Any>): String {
        val adapter = BluetoothAdapter.getDefaultAdapter()
            ?: return """{"error": "设备不支持蓝牙"}"""

        if (!adapter.isEnabled) {
            return """{"error": "蓝牙未开启"}"""
        }

        val devices = adapter.bondedDevices.map { device ->
            mapOf("name" to (device.name ?: "未知设备"), "address" to device.address)
        }

        return buildString {
            append("""{"paired_devices": [""")
            devices.forEachIndexed { index, device ->
                if (index > 0) append(",")
                append("""{"name": "${device["name"]}", "address": "${device["address"]}"}""")
            }
            append("""], "count": ${devices.size}}""")
        }
    }
}

/**
 * 传感器数据工具
 */
class GetSensorDataTool(private val context: Context) : Tool {
    override val name = "get_sensor_data"
    override val description = "读取手机传感器数据。支持加速度计、陀螺仪、光线、距离、磁力计等。"
    override val parameters = mapOf(
        "sensor_type" to mapOf(
            "type" to "string",
            "description" to "传感器类型: accelerometer(加速度), gyroscope(陀螺仪), light(光线), proximity(距离), magnetic(磁力)"
        )
    )

    override suspend fun execute(args: Map<String, Any>): String {
        val sensorType = args["sensor_type"] as? String ?: return """{"error": "缺少 sensor_type 参数"}"""

        val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
        val type = when (sensorType) {
            "accelerometer" -> Sensor.TYPE_ACCELEROMETER
            "gyroscope" -> Sensor.TYPE_GYROSCOPE
            "light" -> Sensor.TYPE_LIGHT
            "proximity" -> Sensor.TYPE_PROXIMITY
            "magnetic" -> Sensor.TYPE_MAGNETIC_FIELD
            else -> return """{"error": "不支持的传感器类型: $sensorType"}"""
        }

        val sensor = sensorManager.getDefaultSensor(type)
            ?: return """{"error": "设备没有该传感器: $sensorType"}"""

        return """{"sensor": "${sensor.name}", "vendor": "${sensor.vendor}", "type": "$sensorType", "maxRange": ${sensor.maximumRange}, "resolution": ${sensor.resolution}}"""
    }
}

/**
 * 内存信息工具
 */
class GetMemoryInfoTool(private val context: Context) : Tool {
    override val name = "get_memory_info"
    override val description = "获取手机内存使用情况，包括总内存、可用内存、是否低内存状态。"
    override val parameters = mapOf<String, Map<String, String>>()  // 无参数

    override suspend fun execute(args: Map<String, Any>): String {
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as android.app.ActivityManager
        val memInfo = android.app.ActivityManager.MemoryInfo()
        am.getMemoryInfo(memInfo)

        val totalMb = memInfo.totalMem / (1024 * 1024)
        val availMb = memInfo.availMem / (1024 * 1024)
        val usedMb = totalMb - availMb
        val usagePercent = (usedMb * 100.0 / totalMb).toInt()

        return """{"total_mb": $totalMb, "available_mb": $availMb, "used_mb": $usedMb, "usage_percent": $usagePercent, "low_memory": ${memInfo.lowMemory}}"""
    }
}
