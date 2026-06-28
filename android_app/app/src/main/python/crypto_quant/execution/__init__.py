# Lazy imports to avoid triggering ccxt dependency on module load
# ccxt may not be available in Chaquopy environment
__all__ = ["BinanceFuturesClient", "PaperTradingSimulator"]
