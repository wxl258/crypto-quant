package com.cryptoquant.app

import com.chaquo.python.android.PyApplication

/**
 * Application class using Chaquopy's PyApplication.
 * PyApplication automatically calls Python.start() in onCreate()
 * on the correct thread with the correct context.
 */
class CryptoQuantApp : PyApplication()
