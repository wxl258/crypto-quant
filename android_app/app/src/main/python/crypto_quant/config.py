"""
Configuration loader — single source of truth for all settings.

Reads from config.yaml with environment variable overrides. Supports lazy-loading
with caching to avoid repeated I/O. Provides convenience accessors for each
configuration section (exchange, trading, risk, data, backtest, alerts, web).

Environment variables follow the pattern ``CQ_<SECTION>_<KEY>`` (uppercase, dot → underscore).
Examples:
  CQ_MODE=live                    → overrides config.yaml mode
  CQ_RISK_MAX_POSITION_PCT=0.25   → overrides risk.max_position_pct
  CQ_BINANCE_API_KEY=xxx          → overrides binance.api_key
"""
import os
from pathlib import Path
from typing import Any

# yaml 延迟导入 — Android/Chaquopy 上 pyyaml C 扩展可能不可用
_yaml = None

def _get_yaml():
    global _yaml
    if _yaml is None:
        try:
            import yaml as _yaml_mod
            _yaml = _yaml_mod
        except ImportError:
            _yaml = False
    return _yaml if _yaml is not False else None

# ---------------------------------------------------------------------------
# Module-level cache for the loaded (and env-overridden) configuration dict.
# Initialised to None; populated on first call to get_config().
# ---------------------------------------------------------------------------
_CONFIG: dict[str, Any] | None = None


def _apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """Override config values from environment variables (``CQ_`` prefix).

    Keys are parsed as ``CQ_<section>_<field>`` (case-insensitive for the
    section/field portion).  Values are type-coerced when possible:
    ``"true"``/``"false"`` → bool, digits → int, numeric → float,
    otherwise kept as str.

    Args:
        config: The configuration dictionary loaded from YAML or defaults.

    Returns:
        The same dictionary, mutated in-place with any matching environment
        variable overrides applied.
    """
    for key, value in os.environ.items():
        if not key.startswith("CQ_"):
            continue
        parts = key[3:].lower().split("_", 1)
        if len(parts) != 2:
            continue
        section, field = parts
        if section in config and isinstance(config[section], dict):
            env_val: bool | int | float | str = value
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


def _load_config() -> dict[str, Any]:
    """Load the full configuration from ``config.yaml`` and apply env overrides.

    If the YAML file cannot be read (missing, unparseable, etc.) a built-in
    set of default configuration values is used instead so the application can
    still start in degraded / paper-trading mode.

    Returns:
        The fully resolved configuration dictionary.
    """
    config_path: Path = Path(__file__).parent / "config.yaml"

    # 尝试加载 YAML 配置文件
    try:
        yaml_mod = _get_yaml()
        if yaml_mod is None:
            raise ImportError("yaml not available")
        with open(config_path, "r", encoding="utf-8") as f:
            config: dict[str, Any] = yaml_mod.safe_load(f)
    except Exception as e:
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


def get_config() -> dict[str, Any]:
    """Return the full configuration dictionary (lazy-loaded, cached).

    The configuration is loaded from disk only once; subsequent calls return
    the cached dictionary.

    Returns:
        The complete configuration dictionary with all sections.
    """
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = _load_config()
    return _CONFIG


# ── Convenience accessors ──

def get_mode() -> str:
    """Return the current operation mode (e.g. ``"paper"``, ``"live"``)."""
    return get_config().get("mode", "paper")


def get_exchange_id() -> str:
    """Return the configured exchange identifier, lowercased."""
    return get_exchange_config().get("id", "binance").lower()


def get_okx_config() -> dict[str, Any]:
    """Return the OKX exchange configuration section."""
    return get_config().get("okx", {})


def get_binance_config() -> dict[str, Any]:
    """Return the Binance exchange configuration section."""
    return get_config().get("binance", {})


def get_exchange_config() -> dict[str, Any]:
    """Return the exchange configuration section."""
    return get_config().get("exchange", {})


def get_trading_config() -> dict[str, Any]:
    """Return the trading configuration section."""
    return get_config().get("trading", {})


def get_risk_config() -> dict[str, Any]:
    """Return the risk management configuration section."""
    return get_config().get("risk", {})


def get_data_config() -> dict[str, Any]:
    """Return the data storage configuration section."""
    return get_config().get("data", {})


def get_backtest_config() -> dict[str, Any]:
    """Return the backtest configuration section."""
    return get_config().get("backtest", {})


def get_web_config() -> dict[str, Any]:
    """Return the web server configuration section."""
    return get_config().get("web", {})


def get_alerts_config() -> dict[str, Any]:
    """Return the alerts/notifications configuration section."""
    return get_config().get("alerts", {})


def get_timezone() -> str:
    """Return the configured timezone, defaulting to ``"Asia/Shanghai"``."""
    return get_trading_config().get("timezone", "Asia/Shanghai")


def get_db_path() -> str:
    """Return the absolute path to the market database.

    Resolves relative paths to an absolute location, with fallback logic for
    Android environments (Chaquopy) where the standard filesystem layout may
    not be available.

    Returns:
        Absolute filesystem path to the SQLite database file.
    """
    raw: str = get_data_config().get("db_path", "data/market.db")
    if not os.path.isabs(raw):
        # Android: use app private storage directory
        try:
            from android.storage import app_storage_path
            base: str = app_storage_path()
            result: str = os.path.join(base, raw)
            os.makedirs(os.path.dirname(result), exist_ok=True)
            return result
        except (ImportError, Exception):
            # Use HOME directory (Chaquopy standard on Android)
            home: str = os.environ.get("HOME", str(Path(__file__).parent))
            # 简化路径，直接用 market.db
            result = os.path.join(home, "market.db")
            return result
    return raw


def get_trading_symbols() -> list[str]:
    """Return the list of trading symbols (e.g. ``["BTCUSDT", "ETHUSDT"]``)."""
    return get_trading_config().get("symbols", ["BTCUSDT", "ETHUSDT"])


def get_kline_intervals() -> list[str]:
    """Return the configured kline/candlestick intervals."""
    return get_data_config().get("kline_intervals", ["1m", "5m", "15m", "1h", "4h", "1d"])
