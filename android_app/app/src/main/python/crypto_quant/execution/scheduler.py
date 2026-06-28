"""
Background Trading Scheduler — Manages live paper trading and periodic reporting.

Runs as an asyncio background task alongside the FastAPI server.
"""
import asyncio
import logging
from typing import Dict, Optional

from data.store import DataStore
from execution.simulator import PaperTradingSimulator
from execution.live_trader import LivePaperTrader
from execution.reporter import TradingReporter
from risk.manager import RiskLimits
from strategy import StrategyRegistry
from config import get_db_path, get_risk_config, get_backtest_config

logger = logging.getLogger(__name__)


class TradingScheduler:
    """Manages background paper trading and reporting."""

    def __init__(self):
        self._traders: Dict[str, LivePaperTrader] = {}
        self._reporters: Dict[str, TradingReporter] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def get_trader(self, name: str) -> Optional[LivePaperTrader]:
        return self._traders.get(name)

    def get_reporter(self, name: str) -> Optional[TradingReporter]:
        return self._reporters.get(name)

    def list_traders(self) -> list:
        return [
            {'name': name, 'status': t.status}
            for name, t in self._traders.items()
        ]

    async def start_trader(self, name: str, strategy_name: str, symbol: str,
                           leverage: int = 3, interval_seconds: int = 60):
        """Start a new paper trading bot."""
        if name in self._traders:
            await self._traders[name].stop()

        # Create strategy using the global registry
        strategy_cls = StrategyRegistry.get(strategy_name)
        if strategy_cls is None:
            available = StrategyRegistry.list_strategies()
            raise ValueError(f"Unknown strategy '{strategy_name}'. Available: {available}")
        strategy = strategy_cls()
        strategy._params['leverage'] = leverage

        # Create dependencies
        store = DataStore(get_db_path())

        risk_cfg = get_risk_config()
        limits = RiskLimits(
            max_position_pct=float(risk_cfg.get('max_position_pct', 0.3)),
            max_total_position_pct=float(risk_cfg.get('max_total_position_pct', 0.8)),
            max_daily_loss_pct=float(risk_cfg.get('max_daily_loss_pct', 0.05)),
            max_consecutive_losses=int(risk_cfg.get('max_consecutive_losses', 3)),
            stop_loss_pct=float(risk_cfg.get('stop_loss_pct', 0.05)),
            take_profit_pct=float(risk_cfg.get('take_profit_pct', 0.10)),
            position_sizing=str(risk_cfg.get('position_sizing', 'fixed')),
        )
        simulator = PaperTradingSimulator(
            initial_capital=get_backtest_config().get('initial_capital', 10000),
            risk_limits=limits,
        )

        trader = LivePaperTrader(strategy, symbol, simulator, store, interval_seconds)
        reporter = TradingReporter(simulator, report_interval_hours=5)

        self._traders[name] = trader
        self._reporters[name] = reporter

        await trader.start()
        logger.info(f"Trader '{name}' started: {strategy_name} on {symbol}")

    async def stop_trader(self, name: str):
        """Stop a paper trading bot."""
        if name in self._traders:
            await self._traders[name].stop()
            del self._traders[name]
        if name in self._reporters:
            del self._reporters[name]
        logger.info(f"Trader '{name}' stopped")

    async def start(self):
        """Start the scheduler's report-checking loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._check_reports())
        logger.info("Trading scheduler started")

    async def stop(self):
        """Stop the scheduler and all traders."""
        self._running = False
        for name in list(self._traders.keys()):
            await self.stop_trader(name)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Trading scheduler stopped")

    async def _check_reports(self):
        """Periodically check if reports need to be generated."""
        while self._running:
            for name, reporter in self._reporters.items():
                if reporter.should_report():
                    report = reporter.generate_report()
                    text = reporter.format_text_report(report)
                    logger.info(f"\n{text}")
                    # Store latest report for API access
                    self._latest_reports = getattr(self, '_latest_reports', {})
                    self._latest_reports[name] = report
            await asyncio.sleep(60)  # Check every minute

    def get_all_reports(self) -> dict:
        """Get latest reports from all traders."""
        result = {}
        for name, reporter in self._reporters.items():
            report = reporter.get_latest_report()
            if report:
                result[name] = report
        return result


# Global singleton
scheduler = TradingScheduler()
