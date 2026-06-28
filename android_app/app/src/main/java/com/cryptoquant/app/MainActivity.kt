package com.cryptoquant.app

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import android.os.Bundle
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
    private var pythonReady = false
    private val serverPort = 8000
    private val executor = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        loadingView = findViewById(R.id.loadingView)
        progressBar = findViewById(R.id.progressBar)
        statusText = findViewById(R.id.statusText)

        setupWebView()
        createNotificationChannel()
        startPythonServer()
    }

    private fun setupWebView() {
        webView.apply {
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                allowFileAccess = true
                allowContentAccess = true
                mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
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
                    // Retry loading if server might not be ready yet
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
        progressBar.visibility = View.VISIBLE

        executor.execute {
            try {
                this@MainActivity.runOnUiThread {
                    statusText.text = "正在初始化 Python 环境..."
                }

                // Initialize Python if not already done
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(this@MainActivity))
                }

                this@MainActivity.runOnUiThread {
                    statusText.text = "正在启动交易引擎..."
                }

                // Get Python instance and run the bridge
                val py = Python.getInstance()
                val module = py.getModule("crypto_quant_bridge")
                module.callAttr("start_server", serverPort)

                this@MainActivity.runOnUiThread {
                    pythonReady = true
                    statusText.text = "交易引擎已启动，正在加载界面..."
                    // Load the web interface
                    webView.loadUrl("http://127.0.0.1:$serverPort")
                }

            } catch (e: Exception) {
                e.printStackTrace()
                this@MainActivity.runOnUiThread {
                    statusText.text = "启动失败: ${e.message}"
                    progressBar.visibility = View.GONE
                    // Show retry option
                    statusText.setOnClickListener {
                        startPythonServer()
                    }
                }
            }
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
            // Minimize instead of closing to keep server running
            moveTaskToBack(true)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        // Note: Python server keeps running in background
        // It will be cleaned up when the process is killed
    }

    companion object {
        const val CHANNEL_ID = "cryptoquant_service"
    }
}
