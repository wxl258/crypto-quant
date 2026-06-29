"""
数据备份恢复 API
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import zipfile
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
import tempfile

from version import __version__

router = APIRouter(prefix="/backup", tags=["backup"])

BACKUP_DIR = Path(__file__).parent.parent / "data" / "backups"


@router.post("/create")
async def create_backup():
    """创建完整备份（配置+数据库+交易记录+策略参数）"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = BACKUP_DIR / f"backup_{timestamp}.zip"

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 配置文件
        config_path = Path(__file__).parent.parent / "config.yaml"
        if config_path.exists():
            zf.write(config_path, "config.yaml")

        # 数据库
        db_path = Path(__file__).parent.parent / "data" / "market.db"
        if db_path.exists():
            zf.write(db_path, "market.db")

        # 策略状态
        state_path = Path(__file__).parent.parent / "strategy" / "strategy_state.json"
        if state_path.exists():
            zf.write(state_path, "strategy_state.json")

        # 自定义策略
        custom_dir = Path(__file__).parent.parent / "strategy" / "custom"
        if custom_dir.exists():
            for f in custom_dir.glob("*.py"):
                if f.name != "__init__.py":
                    zf.write(f, f"strategies/{f.name}")

        # 元数据
        meta = {
            "created_at": timestamp,
            "app_version": __version__,
        }
        zf.writestr("backup_info.json", json.dumps(meta, indent=2))

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    return {
        "success": True,
        "filename": zip_path.name,
        "size_mb": round(size_mb, 2),
        "path": str(zip_path),
        "message": f"备份完成 ({size_mb:.1f}MB)",
    }


@router.get("/list")
async def list_backups():
    """列出所有备份"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = []
    for f in sorted(BACKUP_DIR.glob("backup_*.zip"), reverse=True):
        backups.append({
            "filename": f.name,
            "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
            "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return {"backups": backups, "count": len(backups)}


@router.get("/download/{filename}")
async def download_backup(filename: str):
    """下载备份文件"""
    filepath = BACKUP_DIR / filename
    if not filepath.exists():
        raise HTTPException(404, "备份文件不存在")
    return FileResponse(filepath, filename=filename, media_type="application/zip")


@router.post("/restore/{filename}")
async def restore_backup(filename: str):
    """恢复备份"""
    filepath = BACKUP_DIR / filename
    if not filepath.exists():
        raise HTTPException(404, "备份文件不存在")

    try:
        with zipfile.ZipFile(filepath, 'r') as zf:
            base = Path(__file__).parent.parent

            # 恢复配置
            if "config.yaml" in zf.namelist():
                zf.extract("config.yaml", base)

            # 恢复数据库
            if "market.db" in zf.namelist():
                zf.extract("market.db", base / "data")

            # 恢复策略状态
            if "strategy_state.json" in zf.namelist():
                zf.extract("strategy_state.json", base / "strategy")

            # 恢复自定义策略
            for name in zf.namelist():
                if name.startswith("strategies/") and name != "strategies/":
                    target = base / "strategy" / "custom" / Path(name).name
                    zf.extract(name, base / "strategy" / "custom")

        return {"success": True, "message": "恢复完成，请重启APP使配置生效"}
    except Exception as e:
        raise HTTPException(500, f"恢复失败: {e}")


@router.delete("/delete/{filename}")
async def delete_backup(filename: str):
    """删除备份"""
    filepath = BACKUP_DIR / filename
    if not filepath.exists():
        raise HTTPException(404, "备份文件不存在")
    filepath.unlink()
    return {"success": True, "message": "备份已删除"}


@router.get("/storage")
async def storage_info():
    """获取存储使用情况"""
    db_path = Path(__file__).parent.parent / "data" / "market.db"
    db_size = db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0

    backup_size = sum(f.stat().st_size for f in BACKUP_DIR.glob("*.zip")) / (1024 * 1024) if BACKUP_DIR.exists() else 0

    return {
        "database_mb": round(db_size, 2),
        "backups_mb": round(backup_size, 2),
        "backup_count": len(list(BACKUP_DIR.glob("*.zip"))) if BACKUP_DIR.exists() else 0,
    }
