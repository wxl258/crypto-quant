# Changelog

## [12.0.0] — 2026-06-28

### 🔧 工程化重构
- **统一源码目录**：`crypto_quant/` 成为唯一 Python 源码目录，Android 端通过 Gradle Sync Task 构建时拷贝，消除 22 个差异文件的双副本问题
- **统一版本号管理**：创建 `VERSION` 文件作为单点来源，Python (`version.py`)、Gradle (`build.gradle.kts`)、CI 均从此读取
- **移除 Buildozer/Kivy**：删除 `buildozer.spec`，桌面端改为直接启动 FastAPI 的轻量脚本
- **统一依赖管理**：单一 `crypto_quant/requirements.txt`，移除冗余的 `android_app/requirements.txt`，Gradle 从文件读取依赖

### 🤖 Android 增强
- **新增 ForegroundService**：量化引擎在后台持续运行，不再因 Activity 销毁而中断
- **新增通知渠道**：Android 8+ 通知渠道，Android 13+ 运行时权限请求
- **新增 Python Bridge**：`crypto_quant_bridge.py` 作为 Java ↔ Python 的标准化接口
- **完善 AndroidManifest**：添加 `FOREGROUND_SERVICE_DATA_SYNC`、`POST_NOTIFICATIONS`、`ACCESS_WIFI_STATE` 权限

### 🧪 CI/CD 增强
- 新增 `lint` job：Ruff 代码检查 + pytest 测试
- 新增 PR 触发
- 触发条件扩展为 `crypto_quant/**` + `VERSION` 变更
- 版本号从 `VERSION` 文件动态读取
- Chaquopy 缓存键包含 `requirements.txt`

### 📝 代码质量
- 核心模块添加类型注解：`config.py`、`data/store.py`、`strategy/base.py`、`execution/`、`risk/manager.py`、`backtest/`
- 完善 `.gitignore`：覆盖 Python 虚拟环境、IDE 文件、数据库文件、构建产物等

### 📖 文档
- 重写 `README.md`：添加项目架构图、开发指南、配置说明、API 文档链接
- 新增 `CHANGELOG.md`

### 🗑️ 移除
- `buildozer.spec` — 已由 Chaquopy 方案替代
- `android_app/requirements.txt` — 已合并到根 requirements.txt
- Kivy 依赖 — 不再需要

---

## [11.0.0] — 2026-06

- 专家级界面优化：玻璃拟态侧边栏、悬浮底部导航、渐变策略卡片、专业K线配色

## [10.1.0] — 2026-06

- 修复策略基类参数、优化接口、K线数据列名、移动端底部导航和留白

## [10.0.0] — 2026-06

- 修复策略API返回完整数据 + 全面重新设计UI界面（现代深色主题）

## [9.1.0] — 2026-06

- 防御性修复：StaticFiles 不存在时跳过、root 路由 fallback HTML、数据库路径简化

## [9.0.0] — 2026-06

- 修复所有策略 `__init__` 签名：支持 `**kwargs` 兼容关键字参数调用
