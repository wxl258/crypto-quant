plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// ============================================================
// 版本号：从根目录 VERSION 文件读取（单点来源）
// ============================================================
fun readVersion(): String {
    val versionFile = rootProject.file("../VERSION")
    return if (versionFile.exists()) {
        versionFile.readText().trim()
    } else {
        "0.0.0"
    }
}

fun readVersionCode(): Int {
    val version = readVersion()
    val parts = version.split(".")
    return if (parts.size >= 3) {
        parts[0].toInt() * 10000 + parts[1].toInt() * 100 + parts[2].toInt()
    } else {
        1
    }
}

// ============================================================
// 从根目录 crypto_quant/ 拷贝 Python 源码到 Android 目录
// 消除代码双副本问题
// ============================================================
val syncPythonSource by tasks.registering(Sync::class) {
    description = "从根目录 crypto_quant/ 同步 Python 源码到 Chaquopy 目录"
    from(rootProject.file("../crypto_quant")) {
        exclude("data/market.db")        // 数据库文件不打包
        exclude("**/__pycache__/**")
        exclude("**/*.pyc")
    }
    into(file("src/main/python/crypto_quant"))
}

// 确保 Java 编译前先同步 Python 源码
tasks.named("preBuild") {
    dependsOn(syncPythonSource)
}

// 声明 syncPythonSource 与 mergeDebugPythonSources 的依赖
tasks.matching { it.name == "mergeDebugPythonSources" }.configureEach {
    dependsOn(syncPythonSource)
}
tasks.matching { it.name == "mergeReleasePythonSources" }.configureEach {
    dependsOn(syncPythonSource)
}

// 清理时也清理同步的 Python 目录
tasks.named("clean") {
    doLast {
        file("src/main/python/crypto_quant").deleteRecursively()
    }
}

android {
    namespace = "com.cryptoquant.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.cryptoquant.app"
        minSdk = 24
        targetSdk = 34
        versionCode = readVersionCode()
        versionName = readVersion()

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        ndk {
            // 主流 64 位 + 32 位兼容
            abiFilters += listOf("arm64-v8a", "armeabi-v7a")
        }
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }

    kotlinOptions {
        jvmTarget = "1.8"
    }
}

chaquopy {
    defaultConfig {
        version = "3.10"
        // 提取所有包以确保数据文件（yaml/html/css/js）可用
        extractPackages("crypto_quant")
        pip {
            // 从 requirements.txt 读取依赖（单点来源）
            val reqFile = rootProject.file("../crypto_quant/requirements.txt")
            if (reqFile.exists()) {
                reqFile.readLines()
                    .filter { it.isNotBlank() && !it.trimStart().startsWith("#") }
                    .forEach { line ->
                        val pkg = line.trim().split(">=", "==", "~=", ">", "<", "!=").first().trim()
                        install(pkg)
                    }
            }
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.webkit:webkit:1.9.0")

    // WorkManager — 定时后台任务（数据采集）
    implementation("androidx.work:work-runtime-ktx:2.9.0")
}
