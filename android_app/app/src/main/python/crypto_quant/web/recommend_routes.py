"""
AI策略推荐 API
"""
from fastapi import APIRouter, Query
import numpy as np

router = APIRouter(prefix="/recommend", tags=["recommend"])

@router.get("/analyze")
async def analyze_market(symbol: str = Query(default="BTCUSDT")):
    """分析当前市场状态并推荐策略"""
    from ai.strategy_recommender import get_recommender
    from data.store import DataStore
    from config import get_db_path
    
    recommender = get_recommender()
    
    # 从数据库加载最近数据
    store = DataStore(get_db_path())
    df = store.load_ohlcv(symbol, "1h", limit=200)
    
    if df is None or df.empty:
        # 无数据时返回默认推荐
        return {
            "symbol": symbol,
            "error": "暂无足够的历史数据，请先采集数据",
            "recommendations": [
                {"rank": 1, "strategy": "ensemble_balanced", "name_cn": "平衡组合",
                 "score": 7.0, "advice": "无数据时的默认推荐，适合大多数行情", "star_rating": "\u2605\u2605\u2605\u2606\u2606"},
            ],
            "market_summary": {"summaries": ["\u26a0\ufe0f 数据不足，无法分析市场状态"]},
        }
    
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    volumes = df['volume'].values if 'volume' in df.columns else np.ones_like(closes)
    
    # 分析市场
    market = recommender.analyze_market(closes, highs, lows, volumes)
    
    # 推荐策略
    recommendations = recommender.recommend(market, top_n=5)
    
    # 市场总结
    market_summary = recommender.get_market_summary(market)
    
    return {
        "symbol": symbol,
        "current_price": float(closes[-1]) if len(closes) > 0 else 0,
        "market_state": {
            "trend_strength": round(market.trend_strength * 100),
            "trend_direction": market.trend_direction,
            "volatility_regime": market.volatility_regime,
            "rsi": round(market.rsi, 1),
            "momentum": round(market.momentum * 100, 2),
            "volume_ratio": round(market.volume_ratio, 2),
        },
        "market_summary": market_summary,
        "recommendations": recommendations,
    }

@router.get("/quick")
async def quick_recommend(symbol: str = Query(default="BTCUSDT")):
    """快速推荐：只返回Top3策略名"""
    from ai.strategy_recommender import get_recommender
    from data.store import DataStore
    from config import get_db_path
    import numpy as np
    
    recommender = get_recommender()
    store = DataStore(get_db_path())
    df = store.load_ohlcv(symbol, "1h", limit=200)
    
    if df is None or df.empty:
        return {"top3": ["ensemble_balanced", "adaptive", "dual_ma"]}
    
    market = recommender.analyze_market(
        df['close'].values, df['high'].values, 
        df['low'].values, df['volume'].values if 'volume' in df.columns else np.ones(len(df))
    )
    recs = recommender.recommend(market, top_n=3)
    return {"top3": [r["strategy"] for r in recs], "market": recommender.get_market_summary(market)}
