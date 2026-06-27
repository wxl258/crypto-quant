# CryptoQuant 量化交易 APP

> 一个 APK 安装即用的加密货币量化交易系统，支持币安 + 欧易双交易所

## 功能

- 📊 **24 种量化策略**：双均线、MACD、RSI均值回归、布林带、网格、海龟、超级趋势等
- 🧠 **AI 策略推荐**：根据市场状态自动推荐最优策略
- 🛡️ **智能风控**：止损止盈 + 日亏损熔断 + 连续亏损暂停
- 🔄 **策略热插拔**：支持从 URL 下载新策略，无需重装 APP
- 📅 **盈亏日历**：热力图展示每日盈亏
- 💾 **一键备份恢复**：配置、交易记录、策略参数全部可备份
- 🔔 **自定义告警**：价格突破/盈亏阈值推送通知
- 🏦 **双交易所**：币安 Binance + 欧易 OKX

## GitHub 自动打包

**每次推送代码到 main 分支，GitHub Actions 会自动打包生成 APK。**

### 下载 APK

1. 进入仓库的 **Actions** 标签页
2. 点击最新的 **打包 APK** 工作流
3. 在底部 **Artifacts** 区域下载 `crypto-quant-apk`

### 手动触发打包

1. 进入 **Actions** → **打包 APK**
2. 点击 **Run workflow** → **Run workflow**

## 本地打包

需要 Linux 环境（或 Windows WSL2）：

```bash
# 1. 安装依赖
sudo apt update && sudo apt install -y git zip unzip openjdk-17-jdk \
    python3-pip autoconf libtool cmake libffi-dev libssl-dev expect
pip3 install --user buildozer cython

# 2. 打包
buildozer android debug

# 3. APK 在 ./bin/ 目录下
```

## 安装使用

1. 下载 APK → 安装到手机
2. 打开 APP → 3 屏引导 → 填入币安/OKX 的 API Key
3. 点击「🚀 一键启动」→ 自动开始模拟盘交易
4. 熟悉后切换实盘模式

## 安全提醒

- API Key 仅存储在手机本地，不上传任何服务器
- 创建 API Key 时**不要开启提现权限**
- 建议先用模拟盘熟悉再切实盘
