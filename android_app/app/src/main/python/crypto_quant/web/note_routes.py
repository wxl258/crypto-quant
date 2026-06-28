"""
交易笔记 API
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import sqlite3
from datetime import datetime

router = APIRouter(prefix="/trade/note", tags=["trade-notes"])


class NoteRequest(BaseModel):
    trade_id: int
    note: str


def _get_db_path():
    from config import get_db_path
    return get_db_path()


def _ensure_notes_column():
    """确保 trade_history 表有 notes 列"""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE trade_history ADD COLUMN notes TEXT DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 列已存在
    finally:
        conn.close()


@router.post("/note")
async def save_note(req: NoteRequest):
    """保存交易笔记"""
    _ensure_notes_column()
    conn = sqlite3.connect(_get_db_path())
    cur = conn.cursor()
    try:
        cur.execute("UPDATE trade_history SET notes = ? WHERE id = ?",
                    (req.note, req.trade_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "交易记录不存在")
        conn.commit()
        return {"success": True, "message": "笔记已保存"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"保存失败: {e}")
    finally:
        conn.close()


@router.get("/note/{trade_id}")
async def get_note(trade_id: int):
    """获取交易笔记"""
    _ensure_notes_column()
    conn = sqlite3.connect(_get_db_path())
    cur = conn.cursor()
    try:
        cur.execute("SELECT notes FROM trade_history WHERE id = ?", (trade_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "交易记录不存在")
        return {"trade_id": trade_id, "note": row[0] or ""}
    finally:
        conn.close()
