[app]

# 应用名称
title = 量化交易系统
package.name = crypto_quant
package.domain = com.cryptoquant.app

# 源码目录
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,html,css,js,yaml,db,json

# 版本
version = 1.0.0

# Python 依赖
requirements = python3==3.11.7,kivy==2.3.0,fastapi,uvicorn,pandas,numpy,ccxt,websockets,pyyaml,aiofiles,requests,scikit-learn,fpdf2,pydantic,plyer

# 屏幕方向
orientation = portrait
fullscreen = 1

# 权限
android.permissions = INTERNET,WAKE_LOCK,FOREGROUND_SERVICE,ACCESS_NETWORK_STATE

# 架构（只打 arm64-v8a 就够了，覆盖 95%+ 现代手机）
android.arch = arm64-v8a

# API 级别
android.minapi = 26
android.api = 34
android.ndk = 25b
android.sdk = 34

# 防止息屏
android.wakelock = True

# 前台服务（防止被杀）
android.foreground_service = True

# 图标（如果有的话）
# icon.filename = assets/icon.png

# 启动画面
# presplash.filename = assets/splash.png

# 日志级别
log_level = 1

# 排除测试目录
source.exclude_dirs = tests,__pycache__,.pytest_cache,.git

# 排除不需要的架构库
android.exclude_archs = x86,x86_64,mips,mips64

# OOM 调整（防止被内存不足杀进程）
android.oom_score_adj = True

# 允许应用安装在外部存储
android.allow_backup = True

[buildozer]
log_level = 1
warn_on_root = 1
