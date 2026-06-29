package com.cryptoquant.app.worker

import android.content.Context
import android.util.Log
import androidx.work.*
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.util.concurrent.TimeUnit

/**
 * WorkManager 定时任务 — 定期采集市场数据。
 *
 * 即使 APP 不在前台也能通过 WorkManager 执行数据采集，
 * 确保离线状态下仍有最新市场数据可供回测使用。
 */
class DataCollectionWorker(
    context: Context,
    workerParams: WorkerParameters
) : CoroutineWorker(context, workerParams) {

    override suspend fun doWork(): Result {
        return try {
            Log.d(TAG, "开始定时数据采集...")

            if (!Python.isStarted()) {
                Log.w(TAG, "Python 未启动，跳过数据采集")
                return Result.success()
            }

            val py = Python.getInstance()
            val module = py.getModule("crypto_quant.data.collector")
            module.callAttr("collect_recent_data")

            Log.i(TAG, "数据采集完成")
            Result.success()
        } catch (e: Exception) {
            Log.e(TAG, "数据采集失败", e)
            Result.retry()
        }
    }

    companion object {
        const val TAG = "DataCollectionWorker"
        private const val WORK_NAME = "cryptoquant_data_collection"

        /**
         * 调度定期数据采集任务（每 15 分钟一次，仅在有网络时执行）。
         */
        fun schedule(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()

            val request = PeriodicWorkRequestBuilder<DataCollectionWorker>(
                15, TimeUnit.MINUTES
            )
                .setConstraints(constraints)
                .setBackoffCriteria(
                    BackoffPolicy.EXPONENTIAL,
                    30, TimeUnit.SECONDS
                )
                .build()

            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request
            )

            Log.i(TAG, "数据采集任务已调度（每 15 分钟）")
        }

        /**
         * 取消数据采集任务。
         */
        fun cancel(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(WORK_NAME)
            Log.i(TAG, "数据采集任务已取消")
        }
    }
}
