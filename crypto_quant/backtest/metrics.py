"""
Backtest Performance Metrics Calculator.

Provides the calculate_metrics function for computing comprehensive
backtest performance statistics from equity curves and trade logs.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def calculate_metrics(
    equity_curve: pd.Series,
    trades: pd.DataFrame,
    initial_capital: float = 10000,
    risk_free_rate: float = 0.02,
) -> dict[str, Any]:
    """Calculate comprehensive backtest performance metrics.

    Computes total return, annualized return, Sharpe ratio, max drawdown
    (with duration), win rate, profit factor, Calmar ratio, and per-trade
    statistics.

    Args:
        equity_curve: Series of equity values indexed by timestamp.
        trades: DataFrame with columns: entry_time, exit_time, side, pnl, pnl_pct.
        initial_capital: Starting capital in quote currency.
        risk_free_rate: Annual risk-free rate for Sharpe ratio calculation.

    Returns:
        Dictionary of metrics with the following keys:
        total_return, annual_return, final_equity, sharpe_ratio,
        max_drawdown, max_drawdown_duration, win_rate, profit_factor,
        calmar_ratio, total_trades, winning_trades, losing_trades,
        avg_win, avg_loss, best_trade, worst_trade.
    """
    metrics: dict[str, Any] = {}

    if equity_curve.empty:
        return {
            'total_return': 0, 'annual_return': 0, 'sharpe_ratio': 0,
            'max_drawdown': 0, 'max_drawdown_duration': 0,
            'win_rate': 0, 'profit_factor': 0, 'calmar_ratio': 0,
            'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
            'avg_win': 0, 'avg_loss': 0, 'best_trade': 0, 'worst_trade': 0,
        }

    final_equity = equity_curve.iloc[-1]
    total_return = (final_equity - initial_capital) / initial_capital
    metrics['total_return'] = round(total_return * 100, 2)
    metrics['final_equity'] = round(final_equity, 2)

    # Annualized return (guard against negative base for fractional power)
    trading_days = (equity_curve.index[-1] - equity_curve.index[0]).days
    if trading_days > 0:
        years = trading_days / 365.25
        if total_return > -1 and years > 0:
            annual_return = (1 + total_return) ** (1 / years) - 1
        else:
            annual_return = -1 if total_return <= -1 else 0
    else:
        annual_return = 0
    metrics['annual_return'] = round(annual_return * 100, 2)

    # Sharpe Ratio — annualization factor depends on candle interval
    returns = equity_curve.pct_change().dropna()
    if len(returns) > 1 and returns.std() > 0:
        # Estimate periods per year from median time delta between candles
        time_deltas = returns.index.to_series().diff().dropna()
        if len(time_deltas) > 0:
            median_seconds = time_deltas.median().total_seconds()
            periods_per_year = 365.25 * 24 * 3600 / max(median_seconds, 1)
        else:
            periods_per_year = 365  # fallback: daily
        sharpe = (
            (returns.mean() * periods_per_year - risk_free_rate)
            / (returns.std() * np.sqrt(periods_per_year))
        )
    else:
        sharpe = 0
    metrics['sharpe_ratio'] = round(sharpe, 2)

    # Max Drawdown
    rolling_max = equity_curve.expanding().max()
    drawdown = (equity_curve - rolling_max) / rolling_max
    max_dd = drawdown.min()
    metrics['max_drawdown'] = round(max_dd * 100, 2)

    # Max Drawdown Duration
    dd_start: int | None = None
    max_dd_days: int = 0
    in_dd: bool = False
    for i, dd in enumerate(drawdown):
        if dd < 0 and not in_dd:
            dd_start = i
            in_dd = True
        elif dd >= 0 and in_dd:
            days = (drawdown.index[i] - drawdown.index[dd_start]).days
            max_dd_days = max(max_dd_days, days)
            in_dd = False
    # If still in drawdown at end, compute final duration
    if in_dd and dd_start is not None:
        days = (drawdown.index[-1] - drawdown.index[dd_start]).days
        max_dd_days = max(max_dd_days, days)
    metrics['max_drawdown_duration'] = max_dd_days

    # Trade Statistics
    if trades.empty:
        metrics.update({
            'win_rate': 0, 'profit_factor': 0,
            'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
            'avg_win': 0, 'avg_loss': 0, 'best_trade': 0, 'worst_trade': 0,
        })
    else:
        total_trades = len(trades)
        winning = trades[trades['pnl'] > 0]
        losing = trades[trades['pnl'] < 0]

        metrics['total_trades'] = total_trades
        metrics['winning_trades'] = len(winning)
        metrics['losing_trades'] = len(losing)
        metrics['win_rate'] = (
            round(len(winning) / total_trades * 100, 2) if total_trades > 0 else 0
        )

        total_profit = winning['pnl'].sum() if not winning.empty else 0
        total_loss = abs(losing['pnl'].sum()) if not losing.empty else 0
        metrics['profit_factor'] = (
            round(total_profit / total_loss, 2) if total_loss > 0 else None
        )

        metrics['avg_win'] = round(winning['pnl'].mean(), 2) if not winning.empty else 0
        metrics['avg_loss'] = round(losing['pnl'].mean(), 2) if not losing.empty else 0
        metrics['best_trade'] = round(trades['pnl'].max(), 2)
        metrics['worst_trade'] = round(trades['pnl'].min(), 2)

    # Calmar Ratio
    if max_dd != 0:
        metrics['calmar_ratio'] = round(annual_return / abs(max_dd), 2)
    else:
        metrics['calmar_ratio'] = 0

    # Normalize all values to Python native types for JSON serialization
    for key in metrics:
        val = metrics[key]
        if val is None:
            continue
        if isinstance(val, (np.integer,)):
            metrics[key] = int(val)
        elif isinstance(val, (np.floating,)):
            metrics[key] = float(val)
        elif isinstance(val, (np.bool_,)):
            metrics[key] = bool(val)

    return metrics
