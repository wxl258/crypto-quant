"""
数据存储 — 从SQLite数据库加载OHLCV数据
"""
import sqlite3
import pandas as pd
from pathlib import Path
from typing import Optional


class DataStore:
    """数据存储类，提供OHLCV数据加载功能"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    def load_ohlcv(self, symbol: str, interval: str, limit: int = 200) -> Optional[pd.DataFrame]:
        """从数据库加载OHLCV数据"""
        try:
            conn = sqlite3.connect(self.db_path)
            table_name = f"klines_{symbol}_{interval}"
            df = pd.read_sql_query(
                f"SELECT open_time, open, high, low, close, volume FROM \"{table_name}\" "
                f"ORDER BY open_time DESC LIMIT {limit}",
                conn
            )
            conn.close()
            if df.empty:
                return None
            # 按时间升序排列
            df = df.sort_values("open_time", ascending=True).reset_index(drop=True)
            return df
        except Exception:
            return None
    
    def save_ohlcv(self, symbol: str, interval: str, df: pd.DataFrame):
        """保存OHLCV数据到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            table_name = f"klines_{symbol}_{interval}"
            df.to_sql(table_name, conn, if_exists="append", index=False)
            conn.close()
        except Exception:
            pass
