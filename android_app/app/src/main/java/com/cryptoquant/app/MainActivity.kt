package com.cryptoquant.app

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var loadingView: View
    private lateinit var progressBar: ProgressBar
    private lateinit var statusText: TextView
    private lateinit var detailText: TextView
    private var pythonReady = false
    private val serverPort = 8000
    private val executor = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private var retryCount = 0
    private val maxRetries = 60

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
        startPythonServer()
    }

    private fun setupWebView() {
        webView.apply {
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                allowFileAccess = false
                allowContentAccess = false
                // 本地 127.0.0.1 是回环地址，不需要 cleartext 特殊处理
                mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                cacheMode = WebSettings.LOAD_DEFAULT
                setSupportZoom(true)
                builtInZoomControls = true
                displayZoomControls = false
                useWideViewPort = true
                loadWithOverviewMode = true
            }

            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView?, url: String?) {
                    super.onPageFinished(view, url)
                    if (pythonReady) {
                        showWebView()
                    }
                }

                override fun onReceivedError(
                    view: WebView?,
                    errorCode: Int,
                    description: String?,
                    failingUrl: String?
                ) {
                    if (!pythonReady) {
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
    private fun startPythonServer() {
        loadingView.visibility = View.VISIBLE
        webView.visibility = View.GONE
        statusText.text = getString(R.string.loading_message)
        detailText.text = ""
        progressBar.visibility = View.VISIBLE

        executor.execute {
            try {
                updateUI("正在初始化 Python 环境...", "")

                // Initialize Python using Application context to avoid leaks
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(applicationContext))
                }

                updateUI("正在启动交易引擎...", "加载量化系统模块")

                // Get Python instance and run the bridge (returns immediately)
                val py = Python.getInstance()
                val module = py.getModule("crypto_quant_bridge")
                module.callAttr("start_server", serverPort)

                // Poll for server readiness
                updateUI("等待交易引擎就绪...", "尝试连接本地服务")
                pythonReady = false

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
                    } catch (e: Exception) {
                        // Server not ready yet
                    }

                    val msg = "等待交易引擎就绪... ($i/$maxRetries)"
                    updateUI(msg, "正在启动量化系统服务")
                    Thread.sleep(500)
                }

                if (pythonReady) {
                    updateUI("交易引擎已启动！", "正在加载界面...")
                    handler.post {
                        webView.loadUrl("http://127.0.0.1:$serverPort")
                    }
                } else {
                    updateUI("启动超时", "服务器未能就绪，请尝试重启APP")
                    progressBar.visibility = View.GONE
                }

            } catch (e: Exception) {
                val errMsg = e.message ?: "未知错误"
                val errDetail = e.cause?.message ?: e.javaClass.simpleName
                e.printStackTrace()
                updateUI("启动失败: $errMsg", errDetail)
                progressBar.visibility = View.GONE
                handler.post {
                    statusText.setOnClickListener {
                        retryCount++
                        startPythonServer()
                    }
                }
            }
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

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.foreground_service_channel),
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "CryptoQuant 量化交易引擎服务通知"
            }
            val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            notificationManager.createNotificationChannel(channel)
        }
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            moveTaskToBack(true)
        }
    }

    override fun onDestroy() {
        // Clean up executor and WebView to prevent leaks and port conflicts
        executor.shutdownNow()
        try {
            webView.stopLoading()
            webView.loadUrl("about:blank")
            webView.clearHistory()
            webView.removeAllViews()
            webView.destroy()
        } catch (e: Exception) {
            e.printStackTrace()
        }
        super.onDestroy()
    }

    override fun onConfigurationChanged(newConfig: android.content.res.Configuration) {
        super.onConfigurationChanged(newConfig)
        // Re-layout WebView on rotation
        webView.post { webView.requestLayout() }
    }

    companion object {
        const val CHANNEL_ID = "cryptoquant_service"
    }
}
