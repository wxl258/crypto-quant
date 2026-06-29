# CryptoQuant 量化交易 APP

> 一个 APK 安装即用的加密货币量化交易系统，支持币安 + 欧易双交易所。

[![CI](https://github.com/wxl258/crypto-quant/actions/workflows/build-apk.yml/badge.svg)](https://github.com/wxl258/crypto-quant/actions/workflows/build-apk.yml)
![Version](https://img.shields.io/badge/version-12.0.0-blue)

---

## 📁 项目架构

```
crypto-quant/
├── crypto_quant/               ← 唯一 Python 源码目录
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # 配置管理
│   ├── version.py              # 版本号（从 VERSION 读取）
│   ├── crypto_quant_bridge.py  # Android-Chaquopy 桥接
│   ├── ai/                     # AI 智能体、强化学习、策略推荐
│   ├── backtest/               # 回测引擎 & 指标计算
│   ├── data/                   # 数据采集 & SQLite 存储
│   ├── execution/              # 实盘/模拟交易、调度器、报告
│   ├── risk/                   # 风控管理
│   ├── strategy/               # 24 种策略 + 热插拔管理器
│   │   └── custom/             # 用户自定义策略目录
│   ├── web/                    # FastAPI 路由 + 前端资源
│   │   └── static/             # HTML/CSS/JS (玻璃拟态 UI)
│   └── tests/                  # 单元测试
├── android_app/                ← Android 项目 (Chaquopy)
│   └── app/
│       ├── build.gradle.kts    # 构建配置（自动从 crypto_quant/ 同步源码）
│       └── src/main/
│           ├── java/.../app/   # Kotlin 源码
│           │   ├── MainActivity.kt
│           │   ├── CryptoQuantApp.kt
│           │   └── service/
│           │       └── QuantForegroundService.kt  # 后台保活
│           ├── AndroidManifest.xml
│           └── res/            # Android 资源
├── main.py                     # 桌面版启动脚本
├── VERSION                     # 版本号（单点来源）
├── .github/workflows/          # CI/CD
└── .gitignore
```

**架构设计要点：**
- **`crypto_quant/` 是唯一 Python 源码目录**，Android 端通过 Gradle Sync Task 在构建时拷贝，消除代码双副本
- **`VERSION` 是唯一版本号来源**，Python、Gradle、CI 均从此读取
- **Chaquopy** 负责 Android 端 Python 运行时，不再使用 Buildozer/Kivy

---

## 🚀 功能

| 模块 | 功能 |
|------|------|
| 📊 **24 种量化策略** | 双均线、MACD、RSI均值回归、布林带、网格、海龟、超级趋势、趋势跟踪、自适应、集成学习等 |
| 🧠 **AI 策略推荐** | 根据市场状态自动推荐最优策略，支持强化学习 |
| 🛡️ **智能风控** | 止损止盈 + 日亏损熔断 + 连续亏损暂停 + 波动率自适应仓位 |
| 🔄 **策略热插拔** | 支持从 URL 下载新策略，无需重装 APP |
| 📅 **盈亏日历** | 热力图展示每日盈亏 |
| 💾 **一键备份恢复** | 配置、交易记录、策略参数全部可备份 |
| 🔔 **自定义告警** | 价格突破/盈亏阈值推送通知 |
| 🏦 **双交易所** | 币安 Binance + 欧易 OKX |
| 🔬 **策略进化引擎** | 参数自动优化、遗传算法 |

---

## 📥 获取 APK

### GitHub Actions 自动打包

每次推送代码到 `main` 分支，GitHub Actions 会自动构建 APK。

1. 进入仓库的 **Actions** 标签页
2. 点击最新的 **CI — 代码检查 & APK 构建** 工作流
3. 在底部 **Artifacts** 区域下载 APK

### 手动触发

1. **Actions** → **CI — 代码检查 & APK 构建**
2. 点击 **Run workflow** → **Run workflow**

---

## 🔧 开发指南

### 环境要求

- Python 3.10+（Android 端 Chaquopy 使用 3.10）
- Java 17+（Android 构建）
- Android SDK（通过 Android Studio 或命令行）

### 桌面端运行

```bash
# 1. 安装依赖
pip install -r crypto_quant/requirements.txt

# 2. 启动
python main.py

# 3. 浏览器访问 http://127.0.0.1:8000
# API 文档: http://127.0.0.1:8000/docs
```

### Android 端构建

```bash
cd android_app
./gradlew assembleDebug
# APK 输出: android_app/app/build/outputs/apk/debug/
```

> 构建时 Gradle 会自动从 `crypto_quant/` 同步 Python 源码到 Chaquopy 目录，无需手动拷贝。

### 运行测试

```bash
cd crypto_quant
python -m pytest tests/ -v
```

### 代码检查

```bash
pip install ruff
ruff check crypto_quant/
```

---

## ⚙️ 配置说明

配置文件位于 `crypto_quant/config.yaml`：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `mode` | 运行模式：`paper`（模拟）/ `live`（实盘） | `paper` |
| `exchange` | 交易所：`binance` / `okx` | `binance` |
| `symbols` | 交易对列表 | `["BTC/USDT", "ETH/USDT"]` |
| `web.host` | 服务器地址 | `0.0.0.0` |
| `web.port` | 服务器端口 | `8000` |
| `risk.max_position_pct` | 单笔最大仓位比例 | `0.1` |
| `risk.daily_loss_limit` | 日亏损熔断比例 | `0.05` |

---

## 🔒 安全提醒

- API Key 仅存储在手机本地，不上传任何服务器
- 创建 API Key 时**不要开启提现权限**
- 建议先用模拟盘熟悉再切换实盘
- 定期备份配置和交易记录

---

## 📝 版本历史

详见 [CHANGELOG.md](./CHANGELOG.md)

## 📄 许可证

MIT License
