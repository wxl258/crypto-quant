"""数据质量检查管道"""
import logging
logger = logging.getLogger(__name__)

def check_ohlcv(df):
    """检查OHLCV数据质量，返回清洗后的DataFrame和问题报告"""
    issues = []
    if df is None or len(df) == 0:
        return df, [{'type': 'empty', 'msg': '数据为空'}]
    
    df = df.copy()
    
    # 1. OHLC逻辑验证
    bad_ohlc = df[(df['high'] < df[['open','close']].max(axis=1)) | 
                  (df['low'] > df[['open','close']].min(axis=1))]
    if len(bad_ohlc) > 0:
        issues.append({'type': 'ohlc_invalid', 'count': len(bad_ohlc), 'msg': f'{len(bad_ohlc)}条OHLC逻辑异常'})
        df.loc[bad_ohlc.index, 'high'] = df.loc[bad_ohlc.index, ['open','close','high']].max(axis=1)
        df.loc[bad_ohlc.index, 'low'] = df.loc[bad_ohlc.index, ['open','close','low']].min(axis=1)
    
    # 2. 价格跳跃检测 (>20%)
    if len(df) > 1:
        pct_chg = df['close'].pct_change().abs()
        jumps = pct_chg[pct_chg > 0.2]
        if len(jumps) > 0:
            issues.append({'type': 'price_jump', 'count': len(jumps), 'msg': f'{len(jumps)}条价格跳变>20%'})
    
    # 3. 成交量异常 (0或>100倍均值)
    if len(df) > 10:
        avg_vol = df['volume'].replace(0, None).mean()
        if avg_vol and avg_vol > 0:
            zero_vol = df[df['volume'] == 0]
            huge_vol = df[df['volume'] > avg_vol * 100]
            if len(zero_vol) > 0:
                issues.append({'type': 'zero_volume', 'count': len(zero_vol)})
            if len(huge_vol) > 0:
                issues.append({'type': 'huge_volume', 'count': len(huge_vol)})
    
    # 4. 去重
    if hasattr(df, 'index') and df.index.duplicated().any():
        dup_count = df.index.duplicated().sum()
        df = df[~df.index.duplicated(keep='first')]
        issues.append({'type': 'duplicates', 'count': dup_count})
    
    return df, issues
