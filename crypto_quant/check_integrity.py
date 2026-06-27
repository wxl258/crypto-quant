#!/usr/bin/env python3
"""项目完整性自检"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

errors = []

# 1. 检查关键模块导入
modules_to_check = [
    ("config", "配置模块"),
    ("data.store", "数据存储"),
    ("data.collector", "数据采集"),
    ("strategy.base", "策略基类"),
    ("strategy.manager", "策略管理器"),
    ("backtest.engine", "回测引擎"),
    ("backtest.metrics", "回测指标"),
    ("execution.client", "交易所客户端"),
    ("execution.simulator", "交易模拟器"),
    ("execution.live_trader", "实盘模拟"),
    ("execution.notifier", "推送通知"),
    ("execution.alert_engine", "告警引擎"),
    ("risk.manager", "风险管理"),
]

for mod_name, desc in modules_to_check:
    try:
        __import__(mod_name)
        print(f"\u2705 {desc} ({mod_name})")
    except Exception as e:
        errors.append(f"\u274c {desc} ({mod_name}): {e}")
        print(f"\u274c {desc} ({mod_name}): {e}")

# 2. 检查策略注册
try:
    from strategy.base import StrategyRegistry
    strategies = StrategyRegistry.list_strategies()
    print(f"\u2705 已注册策略: {len(strategies)} 个 - {[s['name'] for s in strategies]}")
except Exception as e:
    errors.append(f"\u274c 策略注册: {e}")

# 3. 检查配置
try:
    from config import get_config
    cfg = get_config()
    print(f"\u2705 配置文件加载成功 (mode={cfg.get('mode')})")
except Exception as e:
    errors.append(f"\u274c 配置加载: {e}")

print(f"\n{'='*50}")
if errors:
    print(f"\u26a0\ufe0f 发现 {len(errors)} 个问题:")
    for e in errors:
        print(f"  {e}")
else:
    print("\u2705 所有检查通过！项目可以正常启动")
