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
import java.util.concurrent.ScheduledThreadPoolExecutor
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
    private val executor = ScheduledThreadPoolExecutor(1)
    private val handler = Handler(Looper.getMainLooper())
    @Volatile
    private var isDestroyed = false
    private val maxRetries = 90

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
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView?, url: String?) {
                    if (!isDestroyed && pythonReady.get()) showWebView()
                }
            }
            webChromeClient = WebChromeClient()
        }
    }

    @SuppressLint("SetTextI18n")
    private fun startPythonServer() {
        if (serverStarted.getAndSet(true)) return

        loadingView.visibility = View.VISIBLE
        webView.visibility = View.GONE
        statusText.text = getString(R.string.loading_message)
        detailText.text = ""
        progressBar.visibility = View.VISIBLE

        // 在后台线程启动 Python 服务器并轮询
        executor.execute {
            try {
                // Step 1: 在主线程获取 Python 实例和模块引用
                val latch = java.util.concurrent.CountDownLatch(1)
                var py: Python? = null
                var module: com.chaquo.python.PyObject? = null
                var initError: Exception? = null

                handler.post {
                    try {
                        val pyInstance = Python.getInstance()
                        py = pyInstance
                        module = pyInstance.getModule("crypto_quant_bridge")
                    } catch (e: Exception) {
                        initError = e
                    } finally {
                        latch.countDown()
                    }
                }
                latch.await()

                if (initError != null || py == null || module == null) {
                    val msg = initError?.message ?: "Python 模块加载失败"
                    updateUI("启动失败: $msg", initError?.javaClass?.simpleName ?: "UnknownError")
                    handler.post { progressBar.visibility = View.GONE }
                    serverStarted.set(false)
                    return@execute
                }

                val safePy = py!!
                val safeModule = module!!

                updateUI("正在启动交易引擎...", "加载量化系统模块")

                // Step 2: 在主线程调用 start_server (包含 import main)
                val resultLatch = java.util.concurrent.CountDownLatch(1)
                var result: Any? = null
                var callError: Exception? = null

                handler.post {
                    try {
                        result = safeModule.callAttr("start_server", serverPort)
                    } catch (e: Exception) {
                        callError = e
                    } finally {
                        resultLatch.countDown()
                    }
                }
                resultLatch.await()

                if (callError != null) {
                    updateUI("启动失败: ${callError!!.message}", callError!!.javaClass.simpleName)
                    handler.post { progressBar.visibility = View.GONE }
                    serverStarted.set(false)
                    return@execute
                }

                // Step 3: 后台线程轮询 HTTP
                pollServerHealth()
            } catch (e: Exception) {
                updateUI("启动失败: ${e.message}", e.javaClass.simpleName)
                handler.post { progressBar.visibility = View.GONE }
                serverStarted.set(false)
            }
        }
    }

    private fun pollServerHealth() {
        updateUI("等待交易引擎就绪...", "尝试连接本地服务")
        for (i in 1..maxRetries) {
            if (isDestroyed) return
            try {
                val url = java.net.URL("http://127.0.0.1:$serverPort/health")
                val conn = url.openConnection() as java.net.HttpURLConnection
                conn.connectTimeout = 2000
                conn.readTimeout = 2000
                if (conn.responseCode == 200) {
                    conn.disconnect()
                    if (isDestroyed) return
                    pythonReady.set(true)
                    updateUI("交易引擎已启动！", "正在加载界面...")
                    handler.post {
                        if (!isDestroyed) {
                            webView.loadUrl("http://127.0.0.1:$serverPort")
                        }
                    }
                    return
                }
                conn.disconnect()
            } catch (_: Exception) {}
            if (i % 5 == 0) {
                updateUI("等待交易引擎就绪... ($i/$maxRetries)", "正在启动量化系统服务")
            }
            try { Thread.sleep(500) } catch (_: InterruptedException) { break }
        }
        updateUI("启动超时", "服务器未能就绪，请尝试重启APP")
        handler.post { progressBar.visibility = View.GONE }
        serverStarted.set(false)
    }

    @SuppressLint("SetTextI18n")
    private fun updateUI(status: String, detail: String) {
        handler.post {
            if (isDestroyed) return@post
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
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .createNotificationChannel(channel)
        }
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else moveTaskToBack(true)
    }

    override fun onDestroy() {
        isDestroyed = true
        // Send shutdown signal and wait briefly for the HTTP call to complete
        // before shutting down the executor, to avoid killing the shutdown request.
        val shutdownFuture = executor.submit<Unit> {
            try {
                val url = java.net.URL("http://127.0.0.1:$serverPort/shutdown")
                val conn = url.openConnection() as java.net.HttpURLConnection
                conn.connectTimeout = 2000
                conn.readTimeout = 2000
                conn.requestMethod = "GET"
                conn.responseCode
                conn.disconnect()
            } catch (_: Exception) {}
        }
        try {
            // Give the shutdown request up to 3 seconds to complete
            shutdownFuture.get(3000, java.util.concurrent.TimeUnit.MILLISECONDS)
        } catch (_: Exception) {}
        executor.shutdownNow()
        try {
            webView.stopLoading()
            webView.loadUrl("about:blank")
            webView.clearHistory()
            webView.removeAllViews()
            webView.destroy()
        } catch (_: Exception) {}
        super.onDestroy()
    }

    companion object {
        const val CHANNEL_ID = "cryptoquant_service"
    }
}
