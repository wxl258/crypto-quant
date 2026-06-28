"""
Trading Reporter — Generates periodic performance reports.

Reports every N hours, summarizing:
- Account equity & PnL
- Open positions
- Recent trades
- Risk status
"""
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from execution.simulator import PaperTradingSimulator

logger = logging.getLogger(__name__)


class TradingReporter:
    """Generates periodic trading performance reports."""

    def __init__(self, simulator: PaperTradingSimulator, report_interval_hours: int = 5):
        self.simulator = simulator
        self.report_interval = report_interval_hours
        self._last_report_time: Optional[datetime] = None
        self._report_history: List[dict] = []
        self._max_history = 100

    def should_report(self) -> bool:
        """Check if it's time for a new report."""
        if self._last_report_time is None:
            return True
        elapsed = (datetime.now() - self._last_report_time).total_seconds() / 3600
        return elapsed >= self.report_interval

    def generate_report(self) -> dict:
        """Generate a comprehensive trading report."""
        account = self.simulator.get_account_summary()
        risk = self.simulator.risk_manager.get_risk_summary()

        # Recent trades (last 20)
        closed_trades = [t for t in self.simulator.order_history if t.get('side') == 'CLOSE']
        recent = closed_trades[-20:]

        # Compute period PnL
        period_pnl = sum(t.get('pnl', 0) for t in recent[-10:])

        # Win/loss stats
        wins = [t for t in closed_trades if t.get('pnl', 0) > 0]
        losses = [t for t in closed_trades if t.get('pnl', 0) < 0]
        total_trades = len(closed_trades)
        win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
        avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0

        report = {
            'timestamp': datetime.now().isoformat(),
            'type': 'periodic_report',
            'interval_hours': self.report_interval,
            'account': {
                'initial_capital': account['initial_capital'],
                'total_equity': account['total_equity'],
                'total_pnl': account['total_pnl'],
                'total_pnl_pct': account['total_pnl_pct'],
                'capital': account['capital'],
            },
            'performance': {
                'total_trades': total_trades,
                'win_rate': round(win_rate, 1),
                'avg_win': round(avg_win, 2),
                'avg_loss': round(avg_loss, 2),
                'profit_factor': round(
                    sum(t['pnl'] for t in wins) / abs(sum(t['pnl'] for t in losses)), 2
                ) if losses else None,
                'period_pnl': round(period_pnl, 2),
            },
            'risk': {
                'trading_paused': risk['trading_paused'],
                'consecutive_losses': risk['consecutive_losses'],
                'total_exposure_pct': risk['total_exposure_pct'],
                'daily_pnl': risk['daily_pnl'],
            },
            'positions': [
                {
                    'symbol': p['symbol'],
                    'side': p['side'],
                    'entry_price': p['entry_price'],
                    'current_price': p['current_price'],
                    'unrealized_pnl': p['unrealized_pnl'],
                }
                for p in account.get('positions', [])
            ],
            'recent_trades': [
                {
                    'time': str(t.get('timestamp', '')),
                    'symbol': t.get('symbol', ''),
                    'pnl': t.get('pnl', 0),
                    'reason': t.get('reason', ''),
                }
                for t in recent[-5:]
            ],
        }

        # Store in history
        self._report_history.append(report)
        if len(self._report_history) > self._max_history:
            self._report_history = self._report_history[-self._max_history:]

        self._last_report_time = datetime.now()
        logger.info(f"Report generated: equity=${account['total_equity']:,.2f}, "
                     f"PnL={account['total_pnl_pct']:+.2f}%, trades={total_trades}")

        return report

    def get_report_history(self, limit: int = 20) -> List[dict]:
        """Get recent report history."""
        return self._report_history[-limit:]

    def get_latest_report(self) -> Optional[dict]:
        """Get the most recent report."""
        return self._report_history[-1] if self._report_history else None

    def format_text_report(self, report: dict = None) -> str:
        """Format a report as human-readable text."""
        if report is None:
            report = self.get_latest_report()
            if report is None:
                return "暂无报告"

        a = report['account']
        p = report['performance']
        r = report['risk']

        lines = [
            "=" * 50,
            f"📊 交易报告 — {report['timestamp'][:19]}",
            "=" * 50,
            "",
            "【账户概览】",
            f"  总权益:    ${a['total_equity']:>12,.2f}",
            f"  总盈亏:    ${a['total_pnl']:>+12,.2f}  ({a['total_pnl_pct']:+.2f}%)",
            f"  可用余额:  ${a['capital']:>12,.2f}",
            "",
            "【交易表现】",
            f"  总交易:    {p['total_trades']:>8}",
            f"  胜率:      {p['win_rate']:>7.1f}%",
            f"  均盈:      ${p['avg_win']:>10,.2f}",
            f"  均亏:      ${p['avg_loss']:>10,.2f}",
            f"  盈亏比:    {p['profit_factor'] or 'N/A':>10}",
            f"  本期盈亏:  ${p['period_pnl']:>+10,.2f}",
            "",
            "【风险状态】",
            f"  交易暂停:  {'是 ⚠️' if r['trading_paused'] else '否 ✅'}",
            f"  连续亏损:  {r['consecutive_losses']}",
            f"  总敞口:    {r['total_exposure_pct']:.1f}%",
            f"  日盈亏:    ${r['daily_pnl']:>+10,.2f}",
            "",
            "【当前持仓】",
        ]

        positions = report.get('positions', [])
        if positions:
            for pos in positions:
                lines.append(
                    f"  {pos['side']:>5s} {pos['symbol']:<10s} "
                    f"入场${pos['entry_price']:,.2f} "
                    f"现价${pos['current_price']:,.2f} "
                    f"浮盈${pos['unrealized_pnl']:+,.2f}"
                )
        else:
            lines.append("  (空仓)")

        lines.append("")
        lines.append("【最近交易】")
        recent = report.get('recent_trades', [])
        if recent:
            for t in recent:
                pnl_str = f"${t['pnl']:+,.2f}"
                lines.append(f"  {t['time'][:19]} | {t['symbol']} | {pnl_str:>10} | {t['reason']}")
        else:
            lines.append("  (无)")

        lines.append("")
        lines.append("=" * 50)
        return "\n".join(lines)
