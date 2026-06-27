"""
Alert Manager - Sends notifications via Telegram
"""
import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AlertManager:
    """Manages and sends alert notifications via Telegram Bot API."""

    def __init__(self, bot_token: str = "", chat_id: str = "", enabled: bool = False):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled

    def _is_configured(self) -> bool:
        return bool(self.enabled and self.bot_token and self.chat_id)

    def send_telegram(self, message: str, bot_token: Optional[str] = None,
                      chat_id: Optional[str] = None) -> bool:
        """Send a message via Telegram Bot API.

        Args:
            message: Text message to send
            bot_token: Override the instance bot token
            chat_id: Override the instance chat id

        Returns:
            True if sent successfully, False otherwise
        """
        token = bot_token or self.bot_token
        cid = chat_id or self.chat_id

        if not token or not cid:
            logger.warning("Telegram not configured — bot_token or chat_id missing")
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": cid,
            "text": message,
            "parse_mode": "HTML",
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    logger.info("Telegram alert sent successfully")
                    return True
                logger.error(f"Telegram API error: {data.get('description', 'unknown')}")
            else:
                logger.error(f"Telegram HTTP error: {resp.status_code}")
            return False
        except requests.RequestException as e:
            logger.error(f"Telegram request failed: {e}")
            return False

    def send_trade_alert(self, symbol: str, side: str, price: float, pnl: float = 0) -> bool:
        """Format and send a trade notification.

        Args:
            symbol: Trading pair symbol
            side: Trade direction (LONG / SHORT)
            price: Entry/exit price
            pnl: Profit/loss amount (0 for open alerts)
        """
        if not self._is_configured():
            return False

        emoji = "📈" if side == "LONG" else "📉"
        pnl_sign = "+" if pnl >= 0 else ""

        if side == 'CLOSE':
            message = (
                f"{emoji} <b>Trade Closed</b>\n"
                f"<b>Symbol:</b> {symbol}\n"
                f"<b>Side:</b> {side}\n"
                f"<b>Exit Price:</b> {price:.2f}\n"
                f"<b>PnL:</b> {pnl_sign}{pnl:.2f} USDT"
            )
        else:
            message = (
                f"{emoji} <b>Trade Opened</b>\n"
                f"<b>Symbol:</b> {symbol}\n"
                f"<b>Side:</b> {side}\n"
                f"<b>Entry Price:</b> {price:.2f}"
            )

        return self.send_telegram(message)

    def send_risk_alert(self, reason: str, details: str = "") -> bool:
        """Format and send a risk warning notification.

        Args:
            reason: Short reason for the risk alert
            details: Additional details about the risk event
        """
        if not self._is_configured():
            return False

        message = (
            f"⚠️ <b>Risk Alert</b>\n"
            f"<b>Reason:</b> {reason}"
        )
        if details:
            message += f"\n<b>Details:</b> {details}"

        return self.send_telegram(message)

    def configure(self, bot_token: str, chat_id: str, enabled: bool):
        """Update alert configuration at runtime."""
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
