"""
CryptoQuant Android Bridge — Java/Kotlin 与 Python 之间的桥梁。

通过 Chaquopy 从 Android 端调用：
    crypto_quant_bridge.start_server(port)
    crypto_quant_bridge.stop_server()
"""
import sys
import os
import threading
import logging

# 确保 crypto_quant 包在 Python 路径中
_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("crypto_quant_bridge")

_server_thread: threading.Thread | None = None
_server = None


def start_server(port: int = 8000):
    """
    在后台线程启动 FastAPI 服务器。
    由 Android ForegroundService 调用。
    """
    global _server_thread

    import uvicorn
    from crypto_quant.main import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=int(port),
        log_level="warning",
    )
    _server = uvicorn.Server(config)

    _server_thread = threading.Thread(
        target=_server.run,
        name="quant-server",
        daemon=True,
    )
    _server_thread.start()
    logger.info(f"Quant server starting on port {port}")


def stop_server():
    """停止 FastAPI 服务器。"""
    global _server
    if _server is not None:
        _server.should_exit = True
        logger.info("Quant server stopping")
