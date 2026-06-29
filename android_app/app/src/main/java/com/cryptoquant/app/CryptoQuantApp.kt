package com.cryptoquant.app

import com.chaquo.python.android.PyApplication
import com.cryptoquant.app.worker.DataCollectionWorker

class CryptoQuantApp : PyApplication() {
    override fun onCreate() {
        super.onCreate()

        // 调度定时数据采集任务（每 15 分钟）
        DataCollectionWorker.schedule(this)
    }
}
