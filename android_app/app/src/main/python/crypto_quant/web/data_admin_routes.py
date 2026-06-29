"""
数据管理 API — 清理、统计
"""
from fastapi import APIRouter, HTTPException
from pathlib import Path
import sqlite3
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data", tags=["data-admin"])


def _get_db_path():
    from config import get_db_path
    return get_db_path()


@router.get("/stats")
async def data_stats():
    """数据统计概览"""
    db_path = _get_db_path()
    if not Path(db_path).exists():
        return {"exists": False}

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    stats = {"exists": True}

    # K线数据统计
    try:
        cur.execute("SELECT COUNT(*) FROM ohlcv")
        stats["total_klines"] = cur.fetchone()[0]

        cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM ohlcv")
        row = cur.fetchone()
        if row[0]:
            stats["earliest_data"] = datetime.fromtimestamp(row[0] / 1000).isoformat()
            stats["latest_data"] = datetime.fromtimestamp(row[1] / 1000).isoformat()

        cur.execute("SELECT COUNT(DISTINCT symbol) FROM ohlcv")
        stats["symbols_count"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT interval) FROM ohlcv")
        stats["intervals_count"] = cur.fetchone()[0]

        cur.execute(
            "SELECT symbol, interval, COUNT(*) as cnt FROM ohlcv GROUP BY symbol, interval ORDER BY cnt DESC LIMIT 10")
        stats["top_pairs"] = [{"symbol": r[0], "interval": r[1], "count": r[2]} for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"OHLCV stats query failed: {e}")
        stats["ohlcv_error"] = "表不存在或查询失败"

    # 交易记录统计
    try:
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
    except Exception as e:
        logger.warning(f"Trade stats query failed: {e}")
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
    cur = conn.cursor()

    cutoff = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

    try:
        cur.execute("SELECT COUNT(*) FROM ohlcv WHERE timestamp < ?", (cutoff,))
        deleted_count = cur.fetchone()[0]

        cur.execute("DELETE FROM ohlcv WHERE timestamp < ?", (cutoff,))
        conn.commit()

        # 压缩数据库
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
