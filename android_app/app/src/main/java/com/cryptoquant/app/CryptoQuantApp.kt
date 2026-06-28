package com.cryptoquant.app

import com.chaquo.python.android.PyApplication

class CryptoQuantApp : PyApplication() {
    override fun onCreate() {
        super.onCreate()
        // Python will be auto-started on first use via Chaquopy
    }
}
