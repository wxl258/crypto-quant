package com.cryptoquant.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.view.View
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.cryptoquant.app.service.QuantForegroundService

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var loadingView: View
    private lateinit var progressBar: ProgressBar
    private lateinit var statusText: TextView
    private lateinit var detailText: TextView
    private val serverPort = 8000
    private val handler = Handler(Looper.getMainLooper())

    // ForegroundService 绑定
    private var quantService: QuantForegroundService? = null
    private var serviceBound = false

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            val binder = service as QuantForegroundService.LocalBinder
            quantService = binder.getService()
            serviceBound = true

            // 注册回调
            quantService?.onStatusUpdate = { status, detail ->
                updateUI(status, detail)
            }
            quantService?.onServerReady = {
                handler.post {
                    webView.loadUrl("http://127.0.0.1:$serverPort")
                }
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            quantService = null
            serviceBound = false
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        loadingView = findViewById(R.id.loadingView)
        progressBar = findViewById(R.id.progressBar)
        statusText = findViewById(R.id.statusText)
        detailText = findViewById(R.id.detailText)

        setupWebView()
        createNotificationChannel()

        // 请求通知权限（Android 13+）
        requestNotificationPermissionIfNeeded()

        // 启动 ForegroundService 来管理 Python 引擎
        startQuantService()

        // 检查电池优化白名单
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            if (!pm.isIgnoringBatteryOptimizations(packageName)) {
                // Show a dialog suggesting user add to whitelist
                // For now, just log a warning
                android.util.Log.w("MainActivity", "App not in battery optimization whitelist")
            }
        }
    }

    private fun startQuantService() {
        loadingView.visibility = View.VISIBLE
        webView.visibility = View.GONE
        statusText.text = getString(R.string.loading_message)
        detailText.text = ""
        progressBar.visibility = View.VISIBLE

        val intent = Intent(this, QuantForegroundService::class.java).apply {
            putExtra("server_port", serverPort)
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }

        bindService(
            Intent(this, QuantForegroundService::class.java),
            serviceConnection,
            Context.BIND_AUTO_CREATE
        )
    }

    private fun setupWebView() {
        webView.apply {
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                // 生产环境关闭文件访问
                if (BuildConfig.DEBUG) {
                    allowFileAccess = true
                } else {
                    allowFileAccess = false
                }
                allowContentAccess = true
                mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                cacheMode = WebSettings.LOAD_CACHE_ELSE_NETWORK
                setSupportZoom(true)
                builtInZoomControls = true
                displayZoomControls = false
                useWideViewPort = true
                loadWithOverviewMode = true
            }

            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView?, url: String?) {
                    super.onPageFinished(view, url)
                    if (quantService?.isServerReady() == true) {
                        showWebView()
                    }
                }

                override fun onReceivedError(
                    view: WebView?,
                    errorCode: Int,
                    description: String?,
                    failingUrl: String?
                ) {
                    if (quantService?.isServerReady() != true) {
                        view?.postDelayed({
                            view?.loadUrl("http://127.0.0.1:$serverPort")
                        }, 2000)
                    }
                }
            }

            webChromeClient = WebChromeClient()
        }
    }

    @SuppressLint("SetTextI18n")
    private fun updateUI(status: String, detail: String) {
        handler.post {
            statusText.text = status
            detailText.text = detail
        }
    }

    private fun showWebView() {
        loadingView.visibility = View.GONE
        webView.visibility = View.VISIBLE
    }

    // ── 通知权限（Android 13+） ──

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(
                    this, Manifest.permission.POST_NOTIFICATIONS
                ) != PackageManager.PERMISSION_GRANTED
            ) {
                ActivityCompat.requestPermissions(
                    this,
                    arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                    REQUEST_NOTIFICATION_PERMISSION
                )
            }
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQUEST_NOTIFICATION_PERMISSION) {
            // 无论用户是否授权，服务仍需启动
            if (grantResults.isNotEmpty() && grantResults[0] != PackageManager.PERMISSION_GRANTED) {
                // 用户拒绝了通知权限 — 仍可运行但前台通知不会显示
            }
        }
    }

    // ── 通知渠道 ──

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.foreground_service_channel),
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "CryptoQuant 量化交易引擎服务通知"
                setShowBadge(false)
            }
            val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            notificationManager.createNotificationChannel(channel)
        }
    }

    // ── 生命周期 ──

    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            // 退到后台，但服务继续运行
            moveTaskToBack(true)
        }
    }

    override fun onDestroy() {
        if (serviceBound) {
            unbindService(serviceConnection)
            serviceBound = false
        }
        super.onDestroy()
        // 注意：不在此处 stopService — 用户可能只是旋转屏幕
        // 服务会在 APP 进程被杀死时自动清理
    }

    companion object {
        const val CHANNEL_ID = "cryptoquant_service"
        private const val REQUEST_NOTIFICATION_PERMISSION = 100
    }
}
