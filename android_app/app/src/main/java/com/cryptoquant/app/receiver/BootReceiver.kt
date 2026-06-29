package com.cryptoquant.app.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import com.cryptoquant.app.service.QuantForegroundService

/**
 * 开机自启 & 网络变化监听广播接收器。
 *
 * 开机完成后自动启动量化引擎前台服务。
 * 网络恢复时通知服务重新连接。
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED -> {
                Log.i(TAG, "设备启动完成，启动量化引擎服务")
                startQuantService(context)
            }
            "android.net.conn.CONNECTIVITY_CHANGE" -> {
                Log.d(TAG, "网络状态变化，量化引擎将自动重连")
                // ForegroundService 内部的 Python 引擎会自行处理重连
            }
        }
    }

    private fun startQuantService(context: Context) {
        val serviceIntent = Intent(context, QuantForegroundService::class.java).apply {
            putExtra("server_port", 8000)
        }
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(serviceIntent)
            } else {
                context.startService(serviceIntent)
            }
        } catch (e: Exception) {
            Log.e(TAG, "启动量化引擎服务失败", e)
        }
    }

    companion object {
        const val TAG = "BootReceiver"
    }
}
