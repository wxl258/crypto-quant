package com.cryptoquant.app

import android.app.Application
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * Application class that initializes Python via Chaquopy.
 * Python.start() is called here exactly once per process, on the main thread.
 * All Activities can then use Python.getInstance() without re-initializing.
 */
class CryptoQuantApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // Initialize Python on the main thread — required by Chaquopy
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
    }
}
