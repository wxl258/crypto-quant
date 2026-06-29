package com.cryptoquant.app.service

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Binder
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import com.cryptoquant.app.MainActivity
import com.cryptoquant.app.R
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * 前台服务 — 确保量化交易引擎在后台持续运行。
 *
 * 启动顺序：
 *   1. 初始化 Chaquopy Python 环境
 *   2. 调用 Python bridge 启动 FastAPI 服务器
 *   3. 轮询 health 端点确认就绪
 *   4. 通过 LiveData/callback 通知 Activity
 */
class QuantForegroundService : Service() {

    private val binder = LocalBinder()
    private var pythonReady = false
    private var serverPort = 8000
    private var serverThread: Thread? = null

    // 回调接口：Activity 通过它获取服务状态
    var onServerReady: (() -> Unit)? = null
    var onStatusUpdate: ((String, String) -> Unit)? = null

    inner class LocalBinder : Binder() {
        fun getService(): QuantForegroundService = this@QuantForegroundService
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        serverPort = intent?.getIntExtra("server_port", 8000) ?: 8000

        startForeground(NOTIFICATION_ID, buildNotification("正在启动..."))

        startPythonServer()
        return START_STICKY
    }

    override fun onDestroy() {
        stopPythonServer()
        super.onDestroy()
    }

    // ── Python 服务器管理 ──

    private fun startPythonServer() {
        serverThread = Thread {
            try {
                updateStatus("正在初始化 Python 环境...", "")

                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(this@QuantForegroundService))
                }

                updateStatus("正在启动交易引擎...", "加载量化系统模块")
                val py = Python.getInstance()
                try {
                    val module = py.getModule("crypto_quant_bridge")
                    module.callAttr("start_server", serverPort)
                } catch (e: Exception) {
                    Log.e(TAG, "Python bridge start failed", e)
                    updateStatus("启动失败: ${e.message}", e.javaClass.simpleName)
                    return@Thread
                }

                // 轮询等待服务器就绪
                updateStatus("等待交易引擎就绪...", "尝试连接本地服务")
                val maxRetries = 60
                var pollDelay = 100L
                for (i in 1..maxRetries) {
                    try {
                        val url = java.net.URL("http://127.0.0.1:$serverPort/health")
                        val conn = url.openConnection() as java.net.HttpURLConnection
                        conn.connectTimeout = 2000
                        conn.readTimeout = 2000
                        val code = conn.responseCode
                        conn.disconnect()
                        if (code == 200) {
                            pythonReady = true
                            break
                        }
                    } catch (_: Exception) {
                        // 服务器尚未就绪
                    }
                    updateStatus(
                        "等待交易引擎就绪... ($i/$maxRetries)",
                        "正在启动量化系统服务"
                    )
                    Thread.sleep(pollDelay)
                    pollDelay = minOf(pollDelay * 2, 1000L)
                }

                if (pythonReady) {
                    updateStatus("交易引擎已启动！", "量化系统运行中")
                    updateNotification("量化引擎运行中")
                    onServerReady?.invoke()
                } else {
                    updateStatus("启动超时", "服务器未能就绪")
                }
            } catch (e: Exception) {
                Log.e(TAG, "Python server start failed", e)
                updateStatus("启动失败: ${e.message}", e.javaClass.simpleName)
            }
        }.apply {
            name = "quant-python-server"
            isDaemon = false
            start()
        }
    }

    private fun stopPythonServer() {
        try {
            if (Python.isStarted()) {
                val py = Python.getInstance()
                val module = py.getModule("crypto_quant_bridge")
                module.callAttr("stop_server")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error stopping Python server", e)
        }
        serverThread?.interrupt()
    }

    // ── 通知管理 ──

    private fun buildNotification(text: String): Notification {
        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val pendingIntent = PendingIntent.getActivity(
            this, 0, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, MainActivity.CHANNEL_ID)
            .setContentTitle(getString(R.string.foreground_service_title))
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .apply {
                if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                    setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE)
                }
            }
            .build()
    }

    private fun updateNotification(text: String) {
        val notification = buildNotification(text)
        val manager = getSystemService(NOTIFICATION_SERVICE) as android.app.NotificationManager
        manager.notify(NOTIFICATION_ID, notification)
    }

    private fun updateStatus(status: String, detail: String) {
        onStatusUpdate?.invoke(status, detail)
    }

    fun isServerReady(): Boolean = pythonReady

    companion object {
        const val TAG = "QuantForegroundService"
        const val NOTIFICATION_ID = 1001
    }
}
