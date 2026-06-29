package com.cryptoquant.app

import com.chaquo.python.android.PyApplication

class CryptoQuantApp : PyApplication() {
    override fun onCreate() {
        super.onCreate()
        // WorkManager 延迟到 Activity 启动后再调度，避免初始化冲突
    }
}
