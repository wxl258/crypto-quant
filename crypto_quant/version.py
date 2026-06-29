"""
项目版本号 — 从根目录 VERSION 文件读取，作为唯一版本来源。
"""
from pathlib import Path

_VERSION_FILE = Path(__file__).parent.parent / "VERSION"


def get_version() -> str:
    """从 VERSION 文件读取当前版本号。"""
    if _VERSION_FILE.exists():
        return _VERSION_FILE.read_text().strip()
    return "0.0.0"


__version__ = get_version()
