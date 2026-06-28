"""
Configuration loader — single source of truth for all settings.
Reads from config.yaml with environment variable overrides.

Environment variables follow the pattern CQ_<SECTION>_<KEY> (uppercase, dot→underscore).
Examples:
  CQ_MODE=live                    → overrides config.yaml mode
  CQ_RISK_MAX_POSITION_PCT=0.25   → overrides risk.max_position_pct
  CQ_BINANCE_API_KEY=xxx          → overrides binance.api_key
"""
import os
import yaml
from pathlib import Path
from typing import Any, Dict, List

_CONFIG = None


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    """Override config values from environment variables (CQ_ prefix)."""
    for key, value in os.environ.items():
        if not key.startswith("CQ_"):
            continue
        parts = key[3:].lower().split("_", 1)
        if len(parts) != 2:
            continue
        section, field = parts
        if section in config and isinstance(config[section], dict):
            env_val = value
            # Try type coercion: bool, int, float, else keep as str
            if env_val.lower() in ("true", "false"):
                env_val = env_val.lower() == "true"
            elif env_val.isdigit():
                env_val = int(env_val)
            else:
                try:
                    env_val = float(env_val)
                except ValueError:
                    pass
            config[section][field] = env_val
    return config


def _load_config() -> Dict[str, Any]:
    config_path = Path(__file__).parent / "config.yaml"

    # 尝试加载 YAML 配置文件
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except (FileNotFoundError, IOError, yaml.YAMLError) as e:
        # Android 环境或文件缺失时的 fallback：使用内嵌默认配置
        import logging
        logging.getLogger(__name__).warning(
            f"无法加载 config.yaml ({e})，使用内置默认配置"
        )
        config = {
            "mode": "paper",
            "binance": {"api_key": "", "api_secret": "", "testnet": True},
            "okx": {"api_key": "", "api_secret": "", "password": "", "testnet": True},
            "exchange": {"id": "binance", "testnet": True},
            "trading": {
                "default_symbol": "BTCUSDT",
                "default_leverage": 3,
                "default_quantity": 0.01,
                "offline_pause": True,
                "timezone": "Asia/Shanghai",
                "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "BNBUSDT"],
            },
            "risk": {
                "max_position_pct": 0.3,
                "max_total_position_pct": 0.8,
                "max_daily_loss_pct": 0.05,
                "max_consecutive_losses": 3,
                "stop_loss_pct": 0.05,
                "take_profit_pct": 0.10,
                "position_sizing": "fixed",
            },
            "data": {
                "db_path": "data/market.db",
                "kline_intervals": ["1m", "5m", "15m", "1h", "4h", "1d"],
            },
            "backtest": {
                "initial_capital": 10000,
                "commission": 0.0005,
                "slippage": 0.0002,
                "slippage_model": "volume",
                "funding_rate": 0.0001,
                "position_pct": 0.3,
                "default_leverage": 3,
                "dynamic_leverage": True,
                "dynamic_trailing_stop": True,
            },
            "alerts": {
                "telegram_bot_token": "",
                "telegram_chat_id": "",
                "enabled": False,
            },
            "web": {"host": "0.0.0.0", "port": 8000},
        }

    return _apply_env_overrides(config)


def get_config() -> Dict[str, Any]:
    """Return the full configuration dictionary (lazy-loaded, cached)."""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = _load_config()
    return _CONFIG


# ── Convenience accessors ──

def get_mode() -> str:
    return get_config().get("mode", "paper")


def get_exchange_id() -> str:
    return get_exchange_config().get("id", "binance").lower()


def get_okx_config() -> Dict[str, Any]:
    return get_config().get("okx", {})


def get_binance_config() -> Dict[str, Any]:
    return get_config().get("binance", {})


def get_exchange_config() -> Dict[str, Any]:
    return get_config().get("exchange", {})


def get_trading_config() -> Dict[str, Any]:
    return get_config().get("trading", {})


def get_risk_config() -> Dict[str, Any]:
    return get_config().get("risk", {})


def get_data_config() -> Dict[str, Any]:
    return get_config().get("data", {})


def get_backtest_config() -> Dict[str, Any]:
    return get_config().get("backtest", {})


def get_web_config() -> Dict[str, Any]:
    return get_config().get("web", {})


def get_alerts_config() -> Dict[str, Any]:
    return get_config().get("alerts", {})


def get_timezone() -> str:
    """Return the configured timezone, defaulting to Asia/Shanghai."""
    return get_trading_config().get("timezone", "Asia/Shanghai")


def get_db_path() -> str:
    raw = get_data_config().get("db_path", "data/market.db")
    if not os.path.isabs(raw):
        # Android: use app private storage directory
        try:
            from android.storage import app_storage_path
            base = app_storage_path()
            return os.path.join(base, raw)
        except (ImportError, Exception):
            # Use HOME directory (Chaquopy standard on Android)
            home = os.environ.get("HOME", str(Path(__file__).parent))
            result = os.path.join(home, raw)
            # Ensure directory exists
            os.makedirs(os.path.dirname(result), exist_ok=True)
            return result
    return raw


def get_trading_symbols() -> List[str]:
    return get_trading_config().get("symbols", ["BTCUSDT", "ETHUSDT"])


def get_kline_intervals() -> List[str]:
    return get_data_config().get("kline_intervals", ["1m", "5m", "15m", "1h", "4h", "1d"])
