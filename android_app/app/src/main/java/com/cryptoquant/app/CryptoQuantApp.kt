package com.cryptoquant.app

import com.chaquo.python.Python
import com.chaquo.python.android.PyApplication

class CryptoQuantApp : PyApplication() {
    override fun onCreate() {
        super.onCreate()
        // PyApplication has already called Python.start() at this point.
        // Now initialize Python paths so that crypto_quant modules are importable.
        try {
            val py = Python.getInstance()
            val bridge = py.getModule("crypto_quant_bridge")
            bridge.callAttr("init_paths")
        } catch (e: Exception) {
            android.util.Log.e("CryptoQuantApp", "Failed to init Python paths", e)
        }
    }
}
