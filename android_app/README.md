# CryptoQuant Android App

加密货币量化交易系统 Android 应用，基于 Chaquopy 嵌入 Python 量化交易引擎。

## 技术栈

- **Android**: Kotlin, WebView, Gradle 8.5
- **Python**: Chaquopy 17.0, Python 3.10
- **后端**: FastAPI + Uvicorn (运行在 localhost:8000)
- **量化引擎**: pandas, numpy, scikit-learn, ccxt

## 构建要求

- Android Studio Hedgehog (2023.1) 或更新版本
- JDK 17
- Gradle 8.5
- Android SDK 34
- NDK (Chaquopy 自动处理)

## 构建步骤

```bash
# 在项目根目录
cd /workspace/crypto_quant_android/android_app

# 构建 debug APK
./gradlew assembleDebug

# 构建 release APK (需要签名)
./gradlew assembleRelease
```

## 项目结构

```
android_app/
├── build.gradle.kts          # 根构建配置
├── settings.gradle.kts       # 项目设置
├── gradle.properties         # Gradle 属性
├── app/
│   ├── build.gradle.kts      # App 构建配置 (Chaquopy 插件)
│   └── src/main/
│       ├── AndroidManifest.xml
│       ├── java/com/cryptoquant/app/
│       │   ├── CryptoQuantApp.kt   # Application 类
│       │   └── MainActivity.kt     # 主 Activity (WebView)
│       ├── python/
│       │   ├── crypto_quant_bridge.py  # Python 入口
│       │   └── crypto_quant/           # 量化交易引擎
│       └── res/                        # Android 资源
```

## 注意事项

1. 应用以 Paper Trading 模式运行（模拟交易）
2. Python 服务器运行在 `127.0.0.1:8000`
3. WebView 加载本地服务器页面
4. 需要 arm64-v8a 架构的 Android 设备（Android 7.0+）
