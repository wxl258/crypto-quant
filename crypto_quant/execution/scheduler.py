"""
Background Trading Scheduler — Manages live paper trading and periodic reporting.

Runs as an asyncio background task alongside the FastAPI server.
Provides a central registry for named traders and reporters, supports
starting/stopping individual trading bots, and periodically generates
performance reports.

A module-level singleton ``scheduler`` is available for direct use.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import (
    get_exchange_id,
    get_binance_config,
    get_okx_config,
    get_db_path,
    get_risk_config,
    get_trading_config,
)
from data.store import DataStore
from execution.live_trader import LivePaperTrader
from execution.reporter import TradingReporter
from execution.simulator import PaperTradingSimulator
from risk.manager import RiskLimits
from strategy import StrategyRegistry

logger = logging.getLogger(__name__)


class TradingScheduler:
    """Manages background paper trading bots and periodic reporting.

    Each trader is identified by a unique name string. Traders and their
    corresponding reporters are created and torn down together.

    Usage::

        scheduler = TradingScheduler()
        await scheduler.start()
        await scheduler.start_trader("mybot", "MACross", "BTCUSDT")
        ...
        await scheduler.stop_trader("mybot")
        await scheduler.stop()
    """

    def __init__(self) -> None:
        """Initialise the scheduler with empty trader/reporter registries."""
        self._traders: dict[str, LivePaperTrader] = {}
        self._reporters: dict[str, TradingReporter] = {}
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """Whether the scheduler's report-checking loop is active."""
        return self._running

    def get_trader(self, name: str) -> LivePaperTrader | None:
        """Look up a trader by name.

        Args:
            name: Trader identifier.

        Returns:
            The :class:`LivePaperTrader` instance, or ``None`` if not found.
        """
        return self._traders.get(name)

    def get_reporter(self, name: str) -> TradingReporter | None:
        """Look up a reporter by name.

        Args:
            name: Trader identifier.

        Returns:
            The :class:`TradingReporter` instance, or ``None`` if not found.
        """
        return self._reporters.get(name)

    def list_traders(self) -> list[dict[str, Any]]:
        """Return a summary of all registered traders.

        Returns:
            A list of dicts with ``name`` and ``status`` keys.
        """
        return [
            {'name': name, 'status': t.status}
            for name, t in self._traders.items()
        ]

    async def start_trader(
        self,
        name: str,
        strategy_name: str,
        symbol: str,
        leverage: int = 3,
        interval_seconds: int = 60,
    ) -> None:
        """Start a new paper trading bot.

        If a trader with the same *name* already exists, it is stopped first.

        Args:
            name: Unique identifier for this trader.
            strategy_name: Name of a strategy registered in
                :class:`StrategyRegistry`.
            symbol: Trading pair symbol (e.g. ``'BTCUSDT'``).
            leverage: Leverage multiplier (default 3).
            interval_seconds: Tick interval in seconds (default 60).

        Raises:
            ValueError: If *strategy_name* is not found in the registry.
        """
        if name in self._traders:
            await self._traders[name].stop()

        # Create strategy using the global registry
        strategy_cls = StrategyRegistry.get(strategy_name)
        if strategy_cls is None:
            available = StrategyRegistry.list_strategies()
            raise ValueError(
                f"Unknown strategy '{strategy_name}'. Available: {available}"
            )
        strategy = strategy_cls()
        strategy.params['leverage'] = leverage

        # Create dependencies
        store = DataStore(get_db_path())

        risk_cfg = get_risk_config()
        limits = RiskLimits(
            max_position_pct=float(risk_cfg.get('max_position_pct', 0.3)),
            max_total_position_pct=float(
                risk_cfg.get('max_total_position_pct', 0.8)
            ),
            max_daily_loss_pct=float(risk_cfg.get('max_daily_loss_pct', 0.05)),
            max_consecutive_losses=int(
                risk_cfg.get('max_consecutive_losses', 3)
            ),
            stop_loss_pct=float(risk_cfg.get('stop_loss_pct', 0.05)),
            take_profit_pct=float(risk_cfg.get('take_profit_pct', 0.10)),
            position_sizing=str(risk_cfg.get('position_sizing', 'fixed')),
        )
        trading_cfg = get_trading_config()
        simulator = PaperTradingSimulator(
            initial_capital=trading_cfg.get('default_quantity', 10000),
            risk_limits=limits,
        )

        trader = LivePaperTrader(
            strategy, symbol, simulator, store, interval_seconds
        )
        reporter = TradingReporter(simulator, report_interval_hours=5)

        self._traders[name] = trader
        self._reporters[name] = reporter

        await trader.start()
        logger.info(f"Trader '{name}' started: {strategy_name} on {symbol}")

    async def stop_trader(self, name: str) -> None:
        """Stop a paper trading bot and remove it from the registry.

        Args:
            name: Trader identifier.

        No-op if no trader with that name exists.
        """
        if name in self._traders:
            await self._traders[name].stop()
            del self._traders[name]
        if name in self._reporters:
            del self._reporters[name]
        logger.info(f"Trader '{name}' stopped")

    async def start(self) -> None:
        """Start the scheduler's periodic report-checking loop.

        No-op if already running.
        """
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._check_reports())
        logger.info("Trading scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler and all managed traders.

        All traders are stopped and removed before the scheduler's
        own background task is cancelled.
        """
        self._running = False
        for name in list(self._traders.keys()):
            await self.stop_trader(name)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # Expected during task cancellation, no action needed
            self._task = None
        logger.info("Trading scheduler stopped")

    async def _check_reports(self) -> None:
        """Periodically check if reports need to be generated.

        Runs every 60 seconds while the scheduler is active. For each
        reporter whose ``should_report()`` returns ``True``, a report is
        generated, logged, and stored in ``_latest_reports`` for API access.
        """
        while self._running:
            for name, reporter in self._reporters.items():
                if reporter.should_report():
                    report = reporter.generate_report()
                    text = reporter.format_text_report(report)
                    logger.info(f"\n{text}")
                    # Store latest report for API access
                    self._latest_reports: dict[str, Any] = getattr(
                        self, '_latest_reports', {}
                    )
                    self._latest_reports[name] = report
            await asyncio.sleep(60)  # Check every minute

    def get_all_reports(self) -> dict[str, Any]:
        """Get the most recent report from each trader's reporter.

        Returns:
            A dict mapping trader names to their latest report dicts.
            Only includes traders that have generated at least one report.
        """
        result: dict[str, Any] = {}
        for name, reporter in self._reporters.items():
            report = reporter.get_latest_report()
            if report:
                result[name] = report
        return result


# Global singleton — can be imported and used directly
scheduler = TradingScheduler()
