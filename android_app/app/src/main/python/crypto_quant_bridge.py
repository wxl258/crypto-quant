"""
CryptoQuant Android Bridge
Entry point for Chaquopy Python integration on Android.
Starts the FastAPI server and handles Android-specific configuration.
"""
import sys
import os
import logging
import traceback
import threading
import time

# Set up logging for Android — write to a file for debugging
LOG_DIR = os.environ.get("HOME", "/tmp")
LOG_FILE = os.path.join(LOG_DIR, "crypto_quant.log")

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='a'),
        logging.StreamHandler(sys.stderr),
    ]
)
logger = logging.getLogger("bridge")

# Server ready event — set when uvicorn has started successfully
_server_ready = threading.Event()
# Error propagation event — set when uvicorn crashes
_server_error = threading.Event()
_server_error_msg = None


def init_paths():
    """Setup Python paths — called on main thread before any imports."""
    python_dir = os.path.dirname(os.path.abspath(__file__))
    crypto_quant_dir = os.path.join(python_dir, "crypto_quant")
    sys.path.insert(0, crypto_quant_dir)
    sys.path.insert(0, python_dir)
    os.environ.setdefault("CQ_MODE", "paper")
    os.environ.setdefault("CQ_WEB_HOST", "127.0.0.1")
    return crypto_quant_dir


def start_server(port=8000):
    """
    Start the FastAPI server.
    All imports and app creation happen on the calling thread (must be main thread).
    Only the uvicorn server runs in a background daemon thread.
    """
    logger.info("=" * 50)
    logger.info("CryptoQuant Android Bridge starting...")
    logger.info(f"Python: {sys.version}")
    logger.info(f"HOME: {os.environ.get('HOME', 'N/A')}")
    logger.info("=" * 50)

    crypto_quant_dir = init_paths()
    os.environ.setdefault("CQ_WEB_PORT", str(port))

    logger.info(f"crypto_quant dir exists: {os.path.isdir(crypto_quant_dir)}")

    # Import and create app on the calling (main) thread
    try:
        logger.info("Importing crypto_quant main module on main thread...")
        import main as crypto_main
        fastapi_app = crypto_main.app
        logger.info("App imported successfully!")
    except Exception as e:
        logger.error(f"Failed to import main module: {e}")
        logger.error(traceback.format_exc())
        return False

    # Start uvicorn in a daemon thread
    def _run_server():
        global _server_error_msg
        try:
            import uvicorn
            config = uvicorn.Config(
                fastapi_app,
                host="127.0.0.1",
                port=port,
                log_level="info",
                loop="asyncio",
            )
            server = uvicorn.Server(config)
            # Signal Kotlin that the server is about to start
            _server_ready.set()
            logger.info(f"Starting uvicorn on 127.0.0.1:{port}...")
            server.run()
        except Exception as e:
            logger.error(f"SERVER CRASH: {e}")
            logger.error(traceback.format_exc())
            _server_error_msg = str(e)
            _server_error.set()

    t = threading.Thread(target=_run_server, daemon=True)
    t.start()
    logger.info("Uvicorn thread started, returning to Kotlin")
    return True
