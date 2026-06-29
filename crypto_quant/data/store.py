"""
数据存储模块 — SQLite 数据库的 OHLCV 和交易记录读写。

提供 DataStore 类，封装了以下功能：
- OHLCV K线数据的加载与保存
- 交易记录的增删改查
- 持仓状态的持久化与恢复
"""
import json
import sqlite3
import logging
from datetime import datetime
from typing import Any

import pandas as pd
from pathlib import Path

logger = logging.getLogger(__name__)


class DataStore:
    """数据存储类，提供 OHLCV 和交易数据的加载与保存功能。

    基于 SQLite 数据库，表命名规则：
    - K线表：``klines_{symbol}_{interval}``
    - 交易表：``trades``（全局单表）

    Attributes:
        db_path: SQLite 数据库文件的路径。
    """

    def __init__(self, db_path: str | Path) -> None:
        """初始化 DataStore 实例。

        Args:
            db_path: SQLite 数据库文件的路径。
        """
        self.db_path: str = str(db_path)
        self._conn: sqlite3.Connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_trade_tables()

    def _init_trade_tables(self) -> None:
        """创建交易记录相关表（如果不存在）。"""
        try:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    quantity REAL NOT NULL,
                    leverage INTEGER DEFAULT 1,
                    pnl REAL DEFAULT 0,
                    fee REAL DEFAULT 0,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    reason TEXT DEFAULT '',
                    status TEXT DEFAULT 'open',
                    note TEXT DEFAULT ''
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS evolution_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generation INTEGER DEFAULT 0,
                    best_fitness REAL DEFAULT 0,
                    best_params TEXT DEFAULT '{}',
                    timestamp TEXT NOT NULL
                )
            """)
            self._conn.commit()
        except Exception as e:
            logger.warning(f"初始化交易表失败: {e}")

    def load_ohlcv(
        self, symbol: str, interval: str, limit: int = 200
    ) -> pd.DataFrame | None:
        """从数据库加载指定交易对和时间粒度的 OHLCV 数据。

        查询对应表并按时间倒序取最近 ``limit`` 条记录，返回时按时间升序排列。

        Args:
            symbol: 交易对名称（如 ``"BTCUSDT"``）。
            interval: K 线时间粒度（如 ``"1h"``, ``"4h"``, ``"1d"``）。
            limit: 最大返回条数，默认为 200。

        Returns:
            包含以下列的 DataFrame：``open_time``, ``open``, ``high``,
            ``low``, ``close``, ``volume``，按 ``open_time`` 升序排列。
            若查询结果为空或发生异常则返回 ``None``。
        """
        try:
            table_name = f"klines_{symbol}_{interval}"
            df = pd.read_sql_query(
                f"SELECT open_time, open, high, low, close, volume FROM \"{table_name}\" "
                f"ORDER BY open_time DESC LIMIT {limit}",
                self._conn,
            )
            if df.empty:
                return None
            # 按时间升序排列
            df = df.sort_values("open_time", ascending=True).reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning(f"Failed to load OHLCV for {symbol}/{interval}: {e}")
            return None

    def save_ohlcv(
        self, symbol: str, interval: str, df: pd.DataFrame
    ) -> None:
        """将 OHLCV 数据保存到数据库。

        以追加模式写入，不会覆盖已有数据。若表不存在则自动创建。
        保存后自动创建 open_time 降序索引以加速查询。

        Args:
            symbol: 交易对名称（如 ``"BTCUSDT"``）。
            interval: K 线时间粒度（如 ``"1h"``, ``"4h"``, ``"1d"``）。
            df: 待保存的 OHLCV DataFrame，需包含 ``open_time``, ``open``,
                ``high``, ``low``, ``close``, ``volume`` 列。

        Note:
            发生异常时静默忽略，不会抛出错误。
        """
        try:
            table_name = f"klines_{symbol}_{interval}"
            df.to_sql(table_name, self._conn, if_exists="append", index=False)
            self._conn.execute(
                f'CREATE INDEX IF NOT EXISTS idx_{table_name}_time '
                f'ON "{table_name}"(open_time DESC)'
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"Failed to save OHLCV for {symbol}/{interval}: {e}")
            pass

    # ── 交易记录持久化 ──

    def save_trade(self, trade: dict[str, Any]) -> int | None:
        """保存新交易记录到数据库。

        Args:
            trade: 交易字典，需包含 symbol, side, entry_price, quantity,
                leverage, entry_time 字段。可选 pnl, fee, reason, status。

        Returns:
            新记录的 ID，失败返回 None。
        """
        try:
            cur = self._conn.execute(
                """INSERT INTO trades (symbol, side, entry_price, quantity, leverage,
                   entry_time, reason, status, fee)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade.get('symbol', ''),
                    trade.get('side', ''),
                    trade.get('entry_price', 0),
                    trade.get('quantity', 0),
                    trade.get('leverage', 1),
                    trade.get('entry_time', datetime.now().isoformat()),
                    trade.get('reason', ''),
                    trade.get('status', 'open'),
                    trade.get('fee', 0),
                ),
            )
            trade_id = cur.lastrowid
            self._conn.commit()
            return trade_id
        except Exception as e:
            logger.error(f"保存交易记录失败: {e}")
            return None

    def close_trade_in_db(
        self,
        trade_id: int,
        exit_price: float,
        pnl: float,
        close_time: str | None = None,
        reason: str = "",
    ) -> bool:
        """将交易记录标记为已平仓。

        Args:
            trade_id: 交易记录 ID。
            exit_price: 平仓价格。
            pnl: 盈亏金额。
            close_time: 平仓时间（ISO格式），默认当前时间。
            reason: 平仓原因。

        Returns:
            是否成功更新。
        """
        try:
            self._conn.execute(
                """UPDATE trades SET exit_price=?, pnl=?, exit_time=?, reason=?, status='closed'
                   WHERE id=?""",
                (
                    exit_price,
                    pnl,
                    close_time or datetime.now().isoformat(),
                    reason,
                    trade_id,
                ),
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"更新交易记录失败 (id={trade_id}): {e}")
            return False

    def load_open_positions(self) -> list[dict[str, Any]]:
        """加载所有未平仓的交易记录。

        Returns:
            持仓列表，每个元素为 dict。
        """
        try:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                "SELECT * FROM trades WHERE status='open' ORDER BY entry_time DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"加载持仓失败: {e}")
            return []

    def load_trade_history(self, limit: int = 200) -> list[dict[str, Any]]:
        """加载最近的历史交易记录。

        Args:
            limit: 最大返回条数。

        Returns:
            交易列表，按入场时间降序。
        """
        try:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                "SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"加载交易历史失败: {e}")
            return []

    def get_trade_stats(self, symbol: str | None = None) -> dict[str, Any]:
        """获取交易统计信息。

        Args:
            symbol: 可选，按交易对过滤。

        Returns:
            包含 total_trades, total_pnl, win_count, loss_count 的字典。
        """
        try:
            where = f"WHERE symbol='{symbol}' AND status='closed'" if symbol else "WHERE status='closed'"
            row = self._conn.execute(
                f"SELECT COUNT(*) as total, COALESCE(SUM(pnl), 0) as total_pnl, "
                f"SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins, "
                f"SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses "
                f"FROM trades {where}"
            ).fetchone()
            if row:
                return {
                    'total_trades': row[0],
                    'total_pnl': round(row[1], 2),
                    'win_count': row[2],
                    'loss_count': row[3],
                    'win_rate': round(row[2] / row[0] * 100, 1) if row[0] > 0 else 0,
                }
            return {'total_trades': 0, 'total_pnl': 0, 'win_count': 0, 'loss_count': 0, 'win_rate': 0}
        except Exception as e:
            logger.warning(f"获取交易统计失败: {e}")
            return {'total_trades': 0, 'total_pnl': 0, 'win_count': 0, 'loss_count': 0, 'win_rate': 0}

    # ── 进化日志持久化 ──

    def save_evolution_log(self, entry: dict[str, Any]) -> int | None:
        """保存一条进化日志到 evolution_log 表。

        Args:
            entry: 包含 generation, best_fitness, best_params, timestamp 的字典。

        Returns:
            新记录的 ID，失败返回 None。
        """
        try:
            cur = self._conn.execute(
                """INSERT INTO evolution_log (generation, best_fitness, best_params, timestamp)
                   VALUES (?, ?, ?, ?)""",
                (
                    entry.get('generation', 0),
                    entry.get('best_fitness', 0),
                    json.dumps(entry.get('best_params', {})),
                    entry.get('timestamp', datetime.now().isoformat()),
                ),
            )
            log_id = cur.lastrowid
            self._conn.commit()
            return log_id
        except Exception as e:
            logger.warning(f"保存进化日志失败: {e}")
            return None

    def load_evolution_log(self, limit: int = 200) -> list[dict[str, Any]]:
        """加载最近的进化日志记录。

        Args:
            limit: 最大返回条数。

        Returns:
            进化日志列表，按时间降序排列。
        """
        try:
            self._conn.row_factory = sqlite3.Row
            rows = self._conn.execute(
                "SELECT * FROM evolution_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                try:
                    d['best_params'] = json.loads(d.get('best_params', '{}'))
                except (json.JSONDecodeError, TypeError):
                    d['best_params'] = {}
                results.append(d)
            return results
        except Exception as e:
            logger.warning(f"加载进化日志失败: {e}")
            return []

    def close(self) -> None:
        """优雅关闭数据库连接。"""
        try:
            self._conn.close()
            logger.debug("数据库连接已关闭")
        except Exception as e:
            logger.warning(f"关闭数据库连接失败: {e}")

    def __del__(self) -> None:
        """析构时确保连接关闭。"""
        try:
            self.close()
        except Exception:
            pass
