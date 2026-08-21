package com.antnest.app.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 设置页面
 * 配置 API Key、Base URL、模型名
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    baseUrl: String,
    apiKey: String,
    modelName: String,
    onSave: (baseUrl: String, apiKey: String, modelName: String) -> Unit,
    onBack: () -> Unit
) {
    var editBaseUrl by remember { mutableStateOf(baseUrl) }
    var editApiKey by remember { mutableStateOf(apiKey) }
    var editModelName by remember { mutableStateOf(modelName) }
    var showApiKey by remember { mutableStateOf(false) }
    var showSaveSuccess by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("设置", fontWeight = FontWeight.Bold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "返回")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer
                )
            )
        }
    ) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // API 配置卡片
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant
                )
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Text(
                        text = "API 配置",
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary
                    )

                    // Base URL
                    OutlinedTextField(
                        value = editBaseUrl,
                        onValueChange = { editBaseUrl = it },
                        label = { Text("API Base URL") },
                        placeholder = { Text("https://api.deepseek.com/v1") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
                    )

                    // API Key
                    OutlinedTextField(
                        value = editApiKey,
                        onValueChange = { editApiKey = it },
                        label = { Text("API Key") },
                        placeholder = { Text("sk-...") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        visualTransformation = if (showApiKey) VisualTransformation.None else PasswordVisualTransformation(),
                        trailingIcon = {
                            IconButton(onClick = { showApiKey = !showApiKey }) {
                                Icon(
                                    if (showApiKey) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                                    contentDescription = if (showApiKey) "隐藏" else "显示"
                                )
                            }
                        }
                    )

                    // 模型名
                    OutlinedTextField(
                        value = editModelName,
                        onValueChange = { editModelName = it },
                        label = { Text("模型名称") },
                        placeholder = { Text("deepseek-chat") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true
                    )
                }
            }

            // 常用配置快捷卡片
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant
                )
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Text(
                        text = "常用配置",
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary
                    )

                    // DeepSeek
                    QuickConfigButton(
                        name = "DeepSeek",
                        url = "https://api.deepseek.com/v1",
                        model = "deepseek-chat",
                        onClick = {
                            editBaseUrl = "https://api.deepseek.com/v1"
                            editModelName = "deepseek-chat"
                        }
                    )

                    // Kimi
                    QuickConfigButton(
                        name = "Kimi (Moonshot)",
                        url = "https://api.moonshot.cn/v1",
                        model = "kimi-k2.6",
                        onClick = {
                            editBaseUrl = "https://api.moonshot.cn/v1"
                            editModelName = "kimi-k2.6"
                        }
                    )

                    // MiniMax
                    QuickConfigButton(
                        name = "MiniMax",
                        url = "https://api.minimax.chat/v1",
                        model = "MiniMax-M2.5",
                        onClick = {
                            editBaseUrl = "https://api.minimax.chat/v1"
                            editModelName = "MiniMax-M2.5"
                        }
                    )

                    // OpenAI
                    QuickConfigButton(
                        name = "OpenAI",
                        url = "https://api.openai.com/v1",
                        model = "gpt-4o",
                        onClick = {
                            editBaseUrl = "https://api.openai.com/v1"
                            editModelName = "gpt-4o"
                        }
                    )
                }
            }

            // 保存按钮
            Button(
                onClick = {
                    onSave(editBaseUrl, editApiKey, editModelName)
                    showSaveSuccess = true
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp),
                enabled = editBaseUrl.isNotBlank() && editApiKey.isNotBlank() && editModelName.isNotBlank()
            ) {
                Text("保存配置", fontSize = 16.sp)
            }

            // 保存成功提示
            if (showSaveSuccess) {
                LaunchedEffect(Unit) {
                    kotlinx.coroutines.delay(2000)
                    showSaveSuccess = false
                }
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.primaryContainer
                    )
                ) {
                    Text(
                        text = "✓ 配置已保存",
                        modifier = Modifier.padding(16.dp),
                        color = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                }
            }

            // 关于信息
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant
                )
            ) {
                Column(
                    modifier = Modifier.padding(16.dp)
                ) {
                    Text(
                        text = "关于 AntNest",
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text("版本: 1.0.0", fontSize = 14.sp)
                    Text("架构: Kotlin + Jetpack Compose", fontSize = 14.sp)
                    Text("开源: github.com/llxpy/AntNest", fontSize = 14.sp)
                }
            }
        }
    }
}

@Composable
fun QuickConfigButton(
    name: String,
    url: String,
    model: String,
    onClick: () -> Unit
) {
    OutlinedButton(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(
            modifier = Modifier.fillMaxWidth(),
            horizontalAlignment = Alignment.Start
        ) {
            Text(name, fontWeight = FontWeight.Bold)
            Text(url, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("模型: $model", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
