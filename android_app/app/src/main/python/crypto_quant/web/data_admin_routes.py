"""
数据管理 API — 清理、统计
"""
from fastapi import APIRouter, HTTPException
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta

router = APIRouter(prefix="/data", tags=["data-admin"])


def _get_db_path():
    from config import get_db_path
    return get_db_path()


def _get_kline_tables(conn):
    """获取所有 klines_ 开头的表名"""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'klines_%'")
    return [r[0] for r in cur.fetchall()]


@router.get("/stats")
async def data_stats():
    """数据统计概览"""
    db_path = _get_db_path()
    if not Path(db_path).exists():
        return {"exists": False}

    conn = sqlite3.connect(db_path)
    stats = {"exists": True}

    # K线数据统计
    try:
        kline_tables = _get_kline_tables(conn)
        total_klines = 0
        min_time = None
        max_time = None
        pairs = {}

        for table in kline_tables:
            parts = table.split('_')
            if len(parts) >= 3:
                symbol = '_'.join(parts[1:-1])
                interval = parts[-1]
            else:
                symbol, interval = table, ''

            cur = conn.cursor()
            try:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                count = cur.fetchone()[0]
                total_klines += count
                pairs[f"{symbol}_{interval}"] = count

                cur.execute(f'SELECT MIN(open_time), MAX(open_time) FROM "{table}"')
                row = cur.fetchone()
                if row[0]:
                    t_min = row[0] if isinstance(row[0], (int, float)) else int(row[0])
                    t_max = row[1] if isinstance(row[1], (int, float)) else int(row[1])
                    if min_time is None or t_min < min_time:
                        min_time = t_min
                    if max_time is None or t_max > max_time:
                        max_time = t_max
            except Exception:
                continue

        stats["total_klines"] = total_klines
        stats["symbols_count"] = len(pairs)
        if min_time and max_time:
            stats["earliest_data"] = datetime.fromtimestamp(min_time / 1000).isoformat()
            stats["latest_data"] = datetime.fromtimestamp(max_time / 1000).isoformat()
        stats["top_pairs"] = [
            {"symbol": k.rsplit('_', 1)[0], "interval": k.rsplit('_', 1)[1], "count": v}
            for k, v in sorted(pairs.items(), key=lambda x: -x[1])[:10]
        ]
    except Exception as e:
        stats["ohlcv_error"] = f"查询失败: {e}"

    # 交易记录统计
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trade_history")
        stats["total_trades"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM trade_history WHERE status='OPEN'")
        stats["open_positions"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM trade_history WHERE status='CLOSED'")
        stats["closed_trades"] = cur.fetchone()[0]

        cur.execute("SELECT COALESCE(SUM(pnl), 0) FROM trade_history WHERE status='CLOSED'")
        stats["total_pnl"] = round(cur.fetchone()[0], 2)

        cur.execute("SELECT COUNT(*) FROM trade_history WHERE status='CLOSED' AND pnl > 0")
        wins = cur.fetchone()[0]
        if stats["closed_trades"] > 0:
            stats["win_rate"] = round(wins / stats["closed_trades"] * 100, 1)
    except Exception:
        stats["trade_error"] = "表不存在或查询失败"

    # 数据库大小
    stats["db_size_mb"] = round(Path(db_path).stat().st_size / (1024 * 1024), 2)

    conn.close()
    return stats


@router.post("/cleanup")
async def cleanup_data(days: int = 90):
    """清理N天前的K线数据（保留交易记录）"""
    if days < 7:
        raise HTTPException(400, "最少保留7天数据")

    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    cutoff = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

    try:
        deleted_count = 0
        kline_tables = _get_kline_tables(conn)
        for table in kline_tables:
            try:
                cur = conn.cursor()
                cur.execute(f'SELECT COUNT(*) FROM "{table}" WHERE open_time < ?', (cutoff,))
                deleted_count += cur.fetchone()[0]
                cur.execute(f'DELETE FROM "{table}" WHERE open_time < ?', (cutoff,))
            except Exception:
                continue
        conn.commit()

        # 压缩数据库
        cur = conn.cursor()
        cur.execute("VACUUM")

        new_size = round(Path(db_path).stat().st_size / (1024 * 1024), 2)

        return {
            "success": True,
            "deleted_klines": deleted_count,
            "kept_days": days,
            "new_db_size_mb": new_size,
            "message": f"已清理 {deleted_count} 条 {days} 天前的K线数据",
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"清理失败: {e}")
    finally:
        conn.close()
