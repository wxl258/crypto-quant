plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.cryptoquant.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.cryptoquant.app"
        minSdk = 24
        targetSdk = 34
        versionCode = 6
        versionName = "5.0.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildFeatures {
        viewBinding = true
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
            install("fastapi")
            install("uvicorn")
            install("pandas")
            install("numpy")
            install("ccxt")
            install("websockets")
            install("pyyaml")
            install("aiofiles")
            install("requests")
            install("fpdf2")
            install("pydantic")
            install("plyer")
            // scikit-learn 体积约 20-30MB，AI 模块未在核心路径使用，暂不打包
            // install("scikit-learn")
            // websocket-client 未使用
            // install("websocket-client")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.webkit:webkit:1.9.0")
}
