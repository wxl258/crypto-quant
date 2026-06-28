# Add project specific ProGuard rules here.
# By default, the flags in this file are appended to flags specified
# in the SDK tools.

# Keep Chaquopy Python classes
-keep class com.chaquo.python.** { *; }
-keep class com.chaquo.python.android.** { *; }

# Keep WebView JavaScript interface
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}

# Keep our application classes
-keep class com.cryptoquant.app.** { *; }

# Keep Python bridge module (called reflectively by Chaquopy)
-keep class crypto_quant_bridge.** { *; }
-keep class crypto_quant.** { *; }
