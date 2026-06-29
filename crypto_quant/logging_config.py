"""
结构化日志配置模块

提供：
- JSON 格式的结构化日志（通过环境变量 LOG_FORMAT=json 切换）
- RotatingFileHandler 日志轮转（默认 10MB × 5 个文件）
- 敏感信息脱敏（api_key, api_secret, password 等）
- trace_id 支持（通过 contextvars 传递）
"""

import logging
import json
import os
import re
from logging.handlers import RotatingFileHandler
from datetime import datetime
from typing import Any

# 用于跨协程传递 trace_id
import contextvars

_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


def set_trace_id(trace_id: str) -> None:
    """设置当前上下文的 trace_id。"""
    _trace_id_var.set(trace_id)


def get_trace_id() -> str:
    """获取当前上下文的 trace_id。"""
    return _trace_id_var.get()


_SENSITIVE_PATTERNS = [
    (re.compile(r'(api_key[=:\s"\']+)([^\s"\'&,}]+)', re.IGNORECASE), r'\1***'),
    (re.compile(r'(api_secret[=:\s"\']+)([^\s"\'&,}]+)', re.IGNORECASE), r'\1***'),
    (re.compile(r'(password[=:\s"\']+)([^\s"\'&,}]+)', re.IGNORECASE), r'\1***'),
    (re.compile(r'(secret[=:\s"\']+)([^\s"\'&,}]+)', re.IGNORECASE), r'\1***'),
    (re.compile(r'(X-MBX-APIKEY:\s*)([^\s]+)', re.IGNORECASE), r'\1***'),
]


class SensitiveFilter(logging.Filter):
    """脱敏过滤器 — 替换日志中的敏感字段。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            msg = record.msg
            for pattern, replacement in _SENSITIVE_PATTERNS:
                msg = pattern.sub(replacement, msg)
            record.msg = msg
        return True


class JsonFormatter(logging.Formatter):
    """JSON 格式的结构化日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": get_trace_id(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """带 trace_id 的文本日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        record.trace_id = get_trace_id()  # type: ignore[attr-defined]
        return super().format(record)


def setup_logging(
    level: int = logging.INFO,
    json_format: bool = False,
    log_file: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """配置结构化日志。

    Args:
        level: 日志级别，默认 INFO。
        json_format: 是否使用 JSON 格式（默认从 LOG_FORMAT 环境变量读取）。
        log_file: 日志文件路径（默认从 LOG_FILE 环境变量读取）。
        max_bytes: 单个日志文件最大大小，默认 10MB。
        backup_count: 保留的备份文件数量，默认 5。
    """
    # 从环境变量读取配置
    if not json_format:
        json_format = os.environ.get("LOG_FORMAT", "").lower() == "json"
    if not log_file:
        log_file = os.environ.get("LOG_FILE", "")

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 清除已有的 handler
    root_logger.handlers.clear()

    # 敏感信息过滤器
    sensitive_filter = SensitiveFilter()

    if json_format:
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = TextFormatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s [trace_id=%(trace_id)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sensitive_filter)
    root_logger.addHandler(console_handler)

    # 文件 handler（如果指定了日志文件）
    if log_file:
        try:
            log_dir = os.path.dirname(log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(sensitive_filter)
            root_logger.addHandler(file_handler)
        except Exception as e:
            logging.getLogger(__name__).warning(f"无法创建日志文件 handler: {e}")
