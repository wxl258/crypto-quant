"""
CryptoQuant 桌面版启动入口
直接启动 FastAPI 服务器，在浏览器中打开 Web 界面。

用法:
    python main.py                # 默认端口 8000
    python main.py --port 9000    # 自定义端口
    python main.py --no-browser   # 不自动打开浏览器
    python main.py --workers 4    # 使用 4 个 worker
"""
import sys
import os
import argparse
import multiprocessing

# 将 crypto_quant 加入 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'crypto_quant'))

import logging
import uvicorn

from crypto_quant.config import get_mode, get_web_config
from crypto_quant.version import __version__

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="CryptoQuant 量化交易系统")
    parser.add_argument("--port", type=int, default=None, help="服务器端口（默认从配置读取）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--workers", type=int, default=None, help="Worker 数量（默认使用 CPU 核心数）")
    args = parser.parse_args()

    web = get_web_config()
    host = web.get('host', '0.0.0.0')
    port = args.port or web.get('port', 8000)
    is_dev = get_mode() == "paper"
    workers = args.workers or int(os.environ.get('WORKERS', multiprocessing.cpu_count()))

    logger.info(f"CryptoQuant v{__version__}")
    logger.info(f"模式: {get_mode()}")
    logger.info(f"服务器: http://{host}:{port}")
    logger.info(f"Workers: {workers}")

    if not args.no_browser:
        import webbrowser
        import threading
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    uvicorn.run(
        "crypto_quant.main:app",
        host=host,
        port=port,
        workers=workers if not is_dev else 1,
        reload=is_dev,
    )


if __name__ == "__main__":
    main()
