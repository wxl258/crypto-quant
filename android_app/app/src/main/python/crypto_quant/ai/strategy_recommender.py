"""
AI策略推荐引擎 — 基于市场状态自动推荐最优策略
"""
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import logging

from strategy.features import FeatureEngineer

logger = logging.getLogger(__name__)


@dataclass
class MarketState:
    """市场状态描述"""
    trend_strength: float     # 0-1，趋势强度（ADX归一化）
    trend_direction: str      # "up" / "down" / "neutral"
    volatility: float         # 波动率（ATR/价格）
    volatility_regime: str    # "low" / "normal" / "high"
    momentum: float           # ROC动量
    rsi: float                # RSI值
    volume_ratio: float       # 量比
    bollinger_position: float # 价格在布林带中的位置 0-1

class StrategyRecommender:
    """基于规则的策略推荐引擎（轻量级，不需要ML模型）"""
    
    def __init__(self):
        # 策略特征矩阵：每个策略在8种市场状态下的适用评分(0-10)
        self.strategy_scores = {
            "dual_ma": {
                "strong_trend": 9, "weak_trend": 2, "high_vol": 4, "low_vol": 6,
                "bullish": 8, "bearish": 8, "sideways": 1, "extreme_rsi": 3,
            },
            "macd": {
                "strong_trend": 9, "weak_trend": 3, "high_vol": 5, "low_vol": 5,
                "bullish": 8, "bearish": 8, "sideways": 2, "extreme_rsi": 4,
            },
            "supertrend": {
                "strong_trend": 10, "weak_trend": 2, "high_vol": 6, "low_vol": 4,
                "bullish": 8, "bearish": 8, "sideways": 1, "extreme_rsi": 3,
            },
            "turtle": {
                "strong_trend": 9, "weak_trend": 2, "high_vol": 7, "low_vol": 3,
                "bullish": 8, "bearish": 8, "sideways": 1, "extreme_rsi": 3,
            },
            "trend_follower": {
                "strong_trend": 10, "weak_trend": 1, "high_vol": 7, "low_vol": 3,
                "bullish": 8, "bearish": 8, "sideways": 1, "extreme_rsi": 2,
            },
            "rsi_mean_reversion": {
                "strong_trend": 3, "weak_trend": 8, "high_vol": 7, "low_vol": 5,
                "bullish": 5, "bearish": 5, "sideways": 9, "extreme_rsi": 10,
            },
            "mean_reversion_v2": {
                "strong_trend": 2, "weak_trend": 9, "high_vol": 8, "low_vol": 4,
                "bullish": 4, "bearish": 4, "sideways": 10, "extreme_rsi": 9,
            },
            "bollinger_bands": {
                "strong_trend": 4, "weak_trend": 8, "high_vol": 8, "low_vol": 4,
                "bullish": 5, "bearish": 5, "sideways": 9, "extreme_rsi": 8,
            },
            "grid": {
                "strong_trend": 1, "weak_trend": 9, "high_vol": 9, "low_vol": 2,
                "bullish": 3, "bearish": 3, "sideways": 10, "extreme_rsi": 5,
            },
            "funding_arb": {
                "strong_trend": 5, "weak_trend": 7, "high_vol": 5, "low_vol": 8,
                "bullish": 5, "bearish": 5, "sideways": 8, "extreme_rsi": 5,
            },
            "ensemble_balanced": {
                "strong_trend": 6, "weak_trend": 7, "high_vol": 6, "low_vol": 6,
                "bullish": 6, "bearish": 6, "sideways": 7, "extreme_rsi": 6,
            },
            "adaptive": {
                "strong_trend": 8, "weak_trend": 8, "high_vol": 7, "low_vol": 7,
                "bullish": 8, "bearish": 8, "sideways": 7, "extreme_rsi": 7,
            },
        }
        
        # 策略中文名
        self.strategy_names = {
            "dual_ma": "双均线策略",
            "macd": "MACD策略",
            "supertrend": "超级趋势",
            "turtle": "海龟交易",
            "trend_follower": "趋势跟踪",
            "rsi_mean_reversion": "RSI均值回归",
            "mean_reversion_v2": "均值回归V2",
            "bollinger_bands": "布林带策略",
            "grid": "网格交易",
            "funding_arb": "资金费率套利",
            "ensemble_balanced": "平衡组合",
            "adaptive": "自适应策略",
        }
        
        # 策略建议文案
        self.strategy_advice = {
            "dual_ma": "趋势市中均线交叉信号清晰可靠，是最经典的策略之一",
            "macd": "MACD在趋势行情中信号准确，零轴位置判断多空强弱",
            "supertrend": "强趋势中超级趋势极少假信号，适合做大波段",
            "turtle": "突破交易在趋势启动时入场，严格止损是核心",
            "trend_follower": "多重确认过滤假信号，追求高胜率趋势交易",
            "rsi_mean_reversion": "震荡市RSI超卖超买信号最准，是回测表现最好的策略",
            "mean_reversion_v2": "三重确认的均值回归，信号质量比V1更高",
            "bollinger_bands": "布林带在区间震荡中精准捕捉高低点",
            "grid": "震荡市网格自动低买高卖，但单边行情需设止损",
            "funding_arb": "低风险套利策略，收益稳定但需耐心等待费率机会",
            "ensemble_balanced": "三策略投票，平衡信号质量和数量，适合大多数行情",
            "adaptive": "自动切换趋势/震荡模式，适合不想手动选策略的用户",
        }
    
    def analyze_market(self, closes: np.ndarray, highs: np.ndarray, 
                       lows: np.ndarray, volumes: np.ndarray) -> MarketState:
        """分析当前市场状态，使用统一的 FeatureEngineer 计算指标。"""
        if len(closes) < 50:
            return MarketState(0.5, "neutral", 0.5, "normal", 0, 50, 1.0, 0.5)

        # 使用 FeatureEngineer 统一计算指标
        df_dict = {
            'close': closes, 'high': highs, 'low': lows, 'volume': volumes,
        }
        import pandas as pd
        df = pd.DataFrame(df_dict)
        features = FeatureEngineer.compute_features(df)

        # 趋势强度 (ADX归一化)
        adx_val = features['adx'].iloc[-1] if 'adx' in features.columns else 25
        trend_strength = min(float(adx_val) / 50, 1.0) if not np.isnan(adx_val) else 0.5

        # 趋势方向
        price_change = (closes[-1] - closes[-20]) / closes[-20] if closes[-20] != 0 else 0
        trend_direction = "up" if price_change > 0.02 else "down" if price_change < -0.02 else "neutral"

        # 波动率
        vol_val = features['atr_ratio'].iloc[-1] if 'atr_ratio' in features.columns else 0.02
        volatility = float(vol_val) if not np.isnan(vol_val) else 0.5
        vol_regime = "high" if volatility > 0.03 else "low" if volatility < 0.01 else "normal"

        # 动量
        momentum = (closes[-1] - closes[-10]) / closes[-10] if closes[-10] != 0 else 0

        # RSI
        rsi_val = features['rsi'].iloc[-1] if 'rsi' in features.columns else 50
        rsi = float(rsi_val) if not np.isnan(rsi_val) else 50

        # 量比
        vr_val = features['volume_ratio'].iloc[-1] if 'volume_ratio' in features.columns else 1.0
        volume_ratio = float(vr_val) if not np.isnan(vr_val) else 1.0

        # 布林带位置
        bb_val = features['bb_position'].iloc[-1] if 'bb_position' in features.columns else 0.5
        bb_position = float(np.clip(bb_val, 0, 1)) if not np.isnan(bb_val) else 0.5

        return MarketState(
            trend_strength=trend_strength,
            trend_direction=trend_direction,
            volatility=volatility,
            volatility_regime=vol_regime,
            momentum=momentum,
            rsi=rsi,
            volume_ratio=volume_ratio,
            bollinger_position=bb_position,
        )
    
    def recommend(self, market: MarketState, top_n: int = 5) -> List[Dict]:
        """根据市场状态推荐策略"""
        # 确定市场状态标签
        is_strong_trend = market.trend_strength > 0.6
        is_sideways = market.trend_strength < 0.3
        is_high_vol = market.volatility_regime == "high"
        is_extreme_rsi = market.rsi < 30 or market.rsi > 70
        
        scores = {}
        for strategy, matrix in self.strategy_scores.items():
            score = 0
            weight_sum = 0
            
            # 趋势维度（权重3）
            if is_strong_trend:
                score += matrix["strong_trend"] * 3
            elif is_sideways:
                score += matrix["weak_trend"] * 3
            else:
                score += (matrix["strong_trend"] + matrix["weak_trend"]) / 2 * 3
            weight_sum += 3
            
            # 波动率维度（权重2）
            if is_high_vol:
                score += matrix["high_vol"] * 2
            else:
                score += matrix["low_vol"] * 2
            weight_sum += 2
            
            # 方向维度（权重2）
            if market.trend_direction == "up":
                score += matrix["bullish"] * 2
            elif market.trend_direction == "down":
                score += matrix["bearish"] * 2
            else:
                score += matrix["sideways"] * 2
            weight_sum += 2
            
            # RSI极端维度（权重1）
            if is_extreme_rsi:
                score += matrix["extreme_rsi"] * 1
            else:
                score += 5 * 1
            weight_sum += 1
            
            scores[strategy] = round(score / weight_sum, 1)
        
        # 排序取top_n
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
        
        # 生成推荐结果
        recommendations = []
        for i, (name, score) in enumerate(ranked):
            rec = {
                "rank": i + 1,
                "strategy": name,
                "name_cn": self.strategy_names.get(name, name),
                "score": score,
                "max_score": 10,
                "advice": self.strategy_advice.get(name, ""),
                "star_rating": "\u2605" * int(score / 2) + "\u2606" * (5 - int(score / 2)),
            }
            recommendations.append(rec)
        
        return recommendations
    
    def get_market_summary(self, market: MarketState) -> Dict:
        """生成市场状态的人话总结"""
        summaries = []
        
        # 趋势
        if market.trend_strength > 0.7:
            summaries.append("\U0001f4c8 市场趋势明确，方向性很强")
        elif market.trend_strength > 0.4:
            summaries.append("\U0001f4ca 市场有一定趋势，但不够强劲")
        else:
            summaries.append("\U0001f504 市场处于震荡状态，缺乏明确方向")
        
        # 方向
        if market.trend_direction == "up":
            summaries.append("\U0001f402 近期偏多头，价格在上涨")
        elif market.trend_direction == "down":
            summaries.append("\U0001f43b 近期偏空头，价格在下跌")
        
        # 波动率
        if market.volatility_regime == "high":
            summaries.append("\U0001f30a 波动率较高，注意风险控制")
        elif market.volatility_regime == "low":
            summaries.append("\U0001f634 波动率较低，市场比较平静")
        
        # RSI
        if market.rsi > 70:
            summaries.append("\u26a0\ufe0f RSI超买，注意回调风险")
        elif market.rsi < 30:
            summaries.append("\U0001f4a1 RSI超卖，可能存在反弹机会")
        else:
            summaries.append("\u2705 RSI处于正常区间")
        
        # 成交量
        if market.volume_ratio > 1.5:
            summaries.append("\U0001f525 成交量显著放大，市场活跃")
        elif market.volume_ratio < 0.5:
            summaries.append("\U0001f4a4 成交量萎缩，市场冷清")
        
        return {
            "summaries": summaries,
            "trend_strength": round(market.trend_strength * 100),
            "trend_direction": market.trend_direction,
            "volatility": market.volatility_regime,
            "rsi": round(market.rsi, 1),
            "volume_ratio": round(market.volume_ratio, 2),
        }


# 全局单例
_recommender: Optional[StrategyRecommender] = None

def get_recommender() -> StrategyRecommender:
    global _recommender
    if _recommender is None:
        _recommender = StrategyRecommender()
    return _recommender
