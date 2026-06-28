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
    Returns True if the server thread was started successfully (not if it's healthy yet).
    Kotlin will poll the /health endpoint separately.
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
    all_ok = True
    for mod, status in import_results.items():
        logger.info(f"  {mod}: {status}")
        if status != "OK":
            all_ok = False

    if not all_ok:
        logger.error("One or more imports failed, server may not start correctly")

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

    # Start server in background thread and return immediately
    server_started = False

    def _run():
        nonlocal server_started
        try:
            logger.info("Importing crypto_quant main module...")
            import main as crypto_main
            fastapi_app = crypto_main.app
            logger.info("App imported successfully!")
            server_started = True

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

    # Give the server thread a brief moment to start imports (max 5 seconds)
    # This avoids the Kotlin side getting stuck for 30 seconds
    logger.info("Waiting for server thread to begin...")
    for i in range(20):
        if server_started:
            logger.info("Server thread has started imports, returning to Kotlin")
            return True
        time.sleep(0.25)

    # If we got here, the import might have failed
    logger.warning("Server thread did not finish imports within 5 seconds")
    # Still return True — let Kotlin poll /health to determine actual readiness
    return True


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
