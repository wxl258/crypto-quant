package com.cryptoquant.app

import com.chaquo.python.Python
import com.chaquo.python.android.PyApplication
import java.util.concurrent.Executors

class CryptoQuantApp : PyApplication() {
    private val initExecutor = Executors.newSingleThreadExecutor()

    override fun onCreate() {
        super.onCreate()
        // PyApplication has already called Python.start() at this point.
        // Move Python path initialization off the main thread to avoid ANR,
        // since getModule("crypto_quant_bridge") may trigger heavy imports.
        initExecutor.execute {
            try {
                val py = Python.getInstance()
                val bridge = py.getModule("crypto_quant_bridge")
                bridge.callAttr("init_paths")
            } catch (e: Exception) {
                android.util.Log.e("CryptoQuantApp", "Failed to init Python paths", e)
            }
        }
    }
}
