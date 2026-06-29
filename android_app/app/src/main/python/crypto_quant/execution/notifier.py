"""
手机推送通知模块 — 策略信号、成交、告警推送到 Android 通知栏
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 尝试导入 Android 通知库
try:
    from plyer import notification
    HAS_NOTIFICATION = True
except ImportError:
    try:
        from android import AndroidNotification
        HAS_NOTIFICATION = True
    except ImportError:
        HAS_NOTIFICATION = False
        logger.warning("通知功能不可用（非Android环境或缺少plyer库）")


class TradeNotifier:
    """交易通知管理器"""
    
    def __init__(self, enabled: bool = True):
        self.enabled = enabled and HAS_NOTIFICATION
        self.last_signal = {}
    
    def signal_alert(self, symbol: str, strategy_name: str, 
                     signal_type: str, price: float, confidence: float = 0.0):
        """策略信号通知"""
        if not self.enabled:
            return
        
        # 防止同一信号重复推送（60秒内）
        import time
        key = f"{symbol}_{strategy_name}_{signal_type}"
        now = time.time()
        if key in self.last_signal and now - self.last_signal[key] < 60:
            return
        self.last_signal[key] = now
        
        signal_emoji = "🟢" if signal_type == "BUY" else "🔴" if signal_type == "SELL" else "🟡"
        signal_cn = {"BUY": "买入", "SELL": "卖出", "CLOSE": "平仓"}.get(signal_type, signal_type)
        
        title = f"{signal_emoji} {symbol} {signal_cn}信号"
        message = f"策略：{strategy_name}\n价格：${price:,.2f}"
        if confidence > 0:
            stars = "★" * int(confidence * 5) + "☆" * (5 - int(confidence * 5))
            message += f"\n置信度：{stars} ({confidence:.0%})"
        
        self._notify(title, message)
    
    def trade_alert(self, symbol: str, side: str, price: float, 
                    quantity: float, pnl: Optional[float] = None):
        """成交通知"""
        if not self.enabled:
            return
        
        side_cn = "做多" if side.upper() == "LONG" else "做空"
        title = f"✅ {symbol} 已{side_cn}"
        message = f"价格：${price:,.2f}\n数量：{quantity:.4f}"
        if pnl is not None:
            pnl_sign = "+" if pnl >= 0 else ""
            message += f"\n盈亏：{pnl_sign}${pnl:,.2f}"
        
        self._notify(title, message)
    
    def risk_alert(self, title: str, message: str):
        """风控告警"""
        if not self.enabled:
            return
        self._notify(f"⚠️ {title}", message)
    
    def daily_summary(self, pnl: float, trades: int, win_rate: float):
        """每日总结（建议每天晚上推送一次）"""
        if not self.enabled:
            return
        
        pnl_sign = "+" if pnl >= 0 else ""
        emoji = "🎉" if pnl > 0 else "😐" if pnl == 0 else "📉"
        
        title = f"{emoji} 今日交易总结"
        message = (f"盈亏：{pnl_sign}${pnl:,.2f}\n"
                   f"交易次数：{trades}\n"
                   f"胜率：{win_rate:.0%}")
        
        self._notify(title, message)
    
    def _notify(self, title: str, message: str):
        """发送系统通知"""
        try:
            if HAS_NOTIFICATION:
                try:
                    notification.notify(
                        title=title,
                        message=message,
                        app_name="量化交易系统",
                        timeout=5,
                    )
                except Exception as e:
                    logger.debug(f"System notification failed (non-critical): {e}")
                    pass
            
            logger.info(f"通知: {title} - {message}")
        except Exception as e:
            logger.error(f"发送通知失败: {e}")


# 全局通知实例
_notifier: Optional[TradeNotifier] = None

def get_notifier() -> TradeNotifier:
    global _notifier
    if _notifier is None:
        _notifier = TradeNotifier(enabled=True)
    return _notifier
