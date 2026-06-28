"""
数据存储 — 从SQLite数据库加载OHLCV数据
"""
import re
import sqlite3
import pandas as pd
from pathlib import Path
from typing import Optional


def _sanitize_table_name(symbol: str, interval: str) -> str:
    """Sanitize symbol and interval for safe table name construction."""
    safe_symbol = re.sub(r'[^a-zA-Z0-9_]', '', symbol)
    safe_interval = re.sub(r'[^a-zA-Z0-9_]', '', interval)
    return f"klines_{safe_symbol}_{safe_interval}"


class DataStore:
    """数据存储类，提供OHLCV数据加载功能"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    def load_ohlcv(self, symbol: str, interval: str, limit: int = 200) -> Optional[pd.DataFrame]:
        """从数据库加载OHLCV数据"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            table_name = _sanitize_table_name(symbol, interval)
            # Ensure limit is a safe positive integer
            safe_limit = max(1, min(int(limit), 10000))
            df = pd.read_sql_query(
                f"SELECT open_time, open, high, low, close, volume FROM \"{table_name}\" "
                f"ORDER BY open_time DESC LIMIT {safe_limit}",
                conn
            )
            if df.empty:
                return None
            # 按时间升序排列
            df = df.sort_values("open_time", ascending=True).reset_index(drop=True)
            return df
        except Exception:
            return None
        finally:
            if conn is not None:
                conn.close()
    
    def save_ohlcv(self, symbol: str, interval: str, df: pd.DataFrame):
        """保存OHLCV数据到数据库，自动去重"""
        if df is None or df.empty:
            return
        try:
            # 确保数据库目录存在
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            
            # 准备数据：确保有 open_time 列，移除重复项
            df = df.copy()
            if 'open_time' not in df.columns:
                if df.index.name is not None and df.index.name != 'open_time':
                    df = df.reset_index()
                else:
                    df['open_time'] = pd.to_datetime(df.index).astype('int64') // 10**6
            
            # 确保 open_time 是整数毫秒
            df['open_time'] = pd.to_numeric(df['open_time'], errors='coerce').astype('int64')
            df = df.drop_duplicates(subset=['open_time'])
            
            conn = sqlite3.connect(self.db_path)
            try:
                table_name = _sanitize_table_name(symbol, interval)
                df.to_sql(table_name, conn, if_exists='append', index=False)
                # 删除重复时间点，保留最早插入的
                conn.execute(f'''
                    DELETE FROM "{table_name}" 
                    WHERE rowid NOT IN (
                        SELECT MIN(rowid) FROM "{table_name}" GROUP BY open_time
                    )
                ''')
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"save_ohlcv failed: {e}")
