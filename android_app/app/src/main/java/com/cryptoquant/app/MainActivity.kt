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
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var loadingView: View
    private lateinit var progressBar: ProgressBar
    private lateinit var statusText: TextView
    private lateinit var detailText: TextView
    private val pythonReady = AtomicBoolean(false)
    private val serverStarted = AtomicBoolean(false)
    private val serverPort = 8000
    private val executor = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private val maxRetries = 90  // 90 * 500ms = 45s max wait

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

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        webView.apply {
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                allowFileAccess = false
                allowContentAccess = false
                mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                cacheMode = WebSettings.LOAD_DEFAULT
                setSupportZoom(true)
                builtInZoomControls = true
                displayZoomControls = false
                useWideViewPort = true
                loadWithOverviewMode = true
            }

            // 关键修复：只在服务器就绪后才处理页面事件
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView?, url: String?) {
                    if (pythonReady.get()) {
                        showWebView()
                    }
                }

                // 不自动重试——由轮询线程负责
                override fun onReceivedError(
                    view: WebView?,
                    errorCode: Int,
                    description: String?,
                    failingUrl: String?
                ) {
                    // 静默忽略加载过程中的错误
                    // 轮询线程会在服务器就绪后重新 loadUrl
                }
            }

            webChromeClient = WebChromeClient()
        }
    }

    @SuppressLint("SetTextI18n")
    private fun startPythonServer() {
        // 防止重复启动
        if (serverStarted.getAndSet(true)) return

        loadingView.visibility = View.VISIBLE
        webView.visibility = View.GONE
        statusText.text = getString(R.string.loading_message)
        detailText.text = ""
        progressBar.visibility = View.VISIBLE

        // Python 已由 CryptoQuantApp (extends PyApplication) 自动初始化
        // 无需手动调用 Python.start()

        // Start Python server on background thread
        executor.execute {
            try {
                updateUI("正在启动交易引擎...", "加载量化系统模块")

                val py = Python.getInstance()
                val module = py.getModule("crypto_quant_bridge")
                val bridgeReady = module.callAttr("start_server", serverPort) as? Boolean ?: false

                if (!bridgeReady) {
                    updateUI("交易引擎启动失败", "Python 服务器未能启动，请查看日志")
                    progressBar.visibility = View.GONE
                    serverStarted.set(false)
                    return@execute
                }

                // Poll health endpoint
                updateUI("等待交易引擎就绪...", "尝试连接本地服务")

                var connected = false
                for (i in 1..maxRetries) {
                    try {
                        val url = java.net.URL("http://127.0.0.1:$serverPort/health")
                        val conn = url.openConnection() as java.net.HttpURLConnection
                        conn.connectTimeout = 2000
                        conn.readTimeout = 2000
                        val code = conn.responseCode
                        conn.disconnect()
                        if (code == 200) {
                            connected = true
                            break
                        }
                    } catch (_: Exception) {
                        // Server not ready yet, keep polling
                    }

                    if (i % 5 == 0) {
                        updateUI("等待交易引擎就绪... ($i/$maxRetries)", "正在启动量化系统服务")
                    }
                    Thread.sleep(500)
                }

                if (connected) {
                    pythonReady.set(true)
                    updateUI("交易引擎已启动！", "正在加载界面...")

                    // 在主线程加载页面
                    handler.post {
                        webView.loadUrl("http://127.0.0.1:$serverPort")
                    }
                } else {
                    updateUI("启动超时", "服务器未能就绪，请尝试重启APP")
                    progressBar.visibility = View.GONE
                    serverStarted.set(false)
                }

            } catch (e: Exception) {
                val errMsg = e.message ?: "未知错误"
                val errDetail = e.cause?.message ?: e.javaClass.simpleName
                e.printStackTrace()
                updateUI("启动失败: $errMsg", errDetail)
                progressBar.visibility = View.GONE
                serverStarted.set(false)
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
        executor.shutdownNow()
        try {
            webView.stopLoading()
            webView.loadUrl("about:blank")
            webView.clearHistory()
            webView.removeAllViews()
            webView.destroy()
        } catch (_: Exception) {
        }
        super.onDestroy()
    }

    override fun onConfigurationChanged(newConfig: android.content.res.Configuration) {
        super.onConfigurationChanged(newConfig)
        webView.post { webView.requestLayout() }
    }

    companion object {
        const val CHANNEL_ID = "cryptoquant_service"
    }
}
