"""
CryptoQuant Android Bridge
Entry point for Chaquopy Python integration on Android.
Starts the FastAPI server and handles Android-specific configuration.
"""
import sys
import os
import logging

# Set up logging for Android
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def setup_android_paths():
    """Configure sys.path to include Python source directories for Android."""
    # On Android with Chaquopy, Python files are in the app's private directory
    python_dir = os.path.dirname(os.path.abspath(__file__))

    # Add crypto_quant package directory to path
    crypto_quant_dir = os.path.join(python_dir, "crypto_quant")
    if crypto_quant_dir not in sys.path:
        sys.path.insert(0, crypto_quant_dir)

    # Add parent python directory
    if python_dir not in sys.path:
        sys.path.insert(0, python_dir)

    logger.info(f"Python path configured. crypto_quant dir: {crypto_quant_dir}")

    # Override environment variables for Android
    os.environ.setdefault("CQ_MODE", "paper")  # Paper trading mode on Android
    os.environ.setdefault("CQ_WEB_HOST", "127.0.0.1")
    os.environ.setdefault("CQ_WEB_PORT", "8000")

    return crypto_quant_dir


def start_server(port=8000):
    """
    Start the FastAPI server. Called from Kotlin via Chaquopy.

    Args:
        port: Port to listen on (default 8000)
    """
    try:
        # Setup paths first
        crypto_quant_dir = setup_android_paths()

        # Import after path setup
        import uvicorn
        from main import app as fastapi_app

        logger.info(f"Starting CryptoQuant server on 127.0.0.1:{port}")
        logger.info(f"Mode: paper (simulated trading only on Android)")

        # Configure uvicorn for Android
        config = uvicorn.Config(
            fastapi_app,
            host="127.0.0.1",
            port=port,
            log_level="info",
            loop="asyncio",  # Use asyncio on Android
        )
        server = uvicorn.Server(config)

        # Run server (this blocks the thread, which is what we want)
        server.run()

    except Exception as e:
        logger.error(f"Failed to start server: {e}", exc_info=True)
        raise


# Allow direct execution for testing
if __name__ == "__main__":
    start_server()
