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
        logging.FileHandler(LOG_FILE, mode='w'),
        logging.StreamHandler(sys.stderr),
    ]
)
logger = logging.getLogger("bridge")


def _safe_import_check():
    """Pre-check: try importing all critical modules, report which fail."""
    modules = [
        "fastapi",
        "uvicorn",
        "pandas",
        "numpy",
        "ccxt",
        "yaml",
        "pydantic",
        "aiofiles",
        "requests",
        "websockets",
    ]
    results = {}
    for mod in modules:
        try:
            __import__(mod)
            results[mod] = "OK"
        except Exception as e:
            results[mod] = str(e)
            logger.error(f"Import {mod}: FAILED — {e}")
    return results


def start_server(port=8000):
    """
    Start the FastAPI server in a background thread.
    Returns immediately so the Kotlin caller can proceed.
    """
    # Write startup info
    logger.info("=" * 50)
    logger.info("CryptoQuant Android Bridge starting...")
    logger.info(f"Python: {sys.version}")
    logger.info(f"cwd: {os.getcwd()}")
    logger.info(f"__file__: {__file__}")
    logger.info(f"HOME: {os.environ.get('HOME', 'N/A')}")
    logger.info("=" * 50)

    # Pre-check imports
    logger.info("Running import pre-checks...")
    import_results = _safe_import_check()
    for mod, status in import_results.items():
        logger.info(f"  {mod}: {status}")

    # Setup paths
    python_dir = os.path.dirname(os.path.abspath(__file__))
    crypto_quant_dir = os.path.join(python_dir, "crypto_quant")
    sys.path.insert(0, crypto_quant_dir)
    sys.path.insert(0, python_dir)

    # Verify directory exists
    logger.info(f"crypto_quant dir exists: {os.path.isdir(crypto_quant_dir)}")
    if os.path.isdir(crypto_quant_dir):
        for f in sorted(os.listdir(crypto_quant_dir))[:20]:
            logger.info(f"  {f}")

    # Set env
    os.environ.setdefault("CQ_MODE", "paper")
    os.environ.setdefault("CQ_WEB_HOST", "127.0.0.1")
    os.environ.setdefault("CQ_WEB_PORT", str(port))

    # Start server in background thread
    def _run():
        try:
            logger.info("Importing crypto_quant main module...")
            # The crypto_quant dir is already in sys.path, so we can import directly
            import main as crypto_main
            fastapi_app = crypto_main.app
            logger.info("App imported successfully!")

            import uvicorn
            config = uvicorn.Config(
                fastapi_app,
                host="127.0.0.1",
                port=port,
                log_level="info",
                loop="asyncio",
            )
            server = uvicorn.Server(config)
            logger.info(f"Starting uvicorn on 127.0.0.1:{port}...")
            server.run()

        except Exception as e:
            logger.error(f"SERVER CRASH: {e}")
            logger.error(traceback.format_exc())

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    # Wait for server to be ready (max 30 seconds)
    logger.info("Waiting for server to become ready...")
    import urllib.request
    ready = False
    for i in range(60):
        try:
            resp = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=1
            )
            if resp.status == 200:
                ready = True
                logger.info("Server is READY!")
                break
        except Exception:
            time.sleep(0.5)

    if not ready:
        logger.error("Server did NOT become ready within 30 seconds!")
        # Log any crash info
        logger.error(f"Last log file contents will be in: {LOG_FILE}")

    # Return readiness status (but don't block Kotlin)
    return ready


# Allow direct execution for testing
if __name__ == "__main__":
    ok = start_server()
    print(f"\nServer ready: {ok}")
    if ok:
        print("Press Ctrl+C to stop...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Shutting down...")
