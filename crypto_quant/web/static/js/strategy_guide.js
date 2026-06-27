// 策略中文说明书
const STRATEGY_GUIDE = {
    "dual_ma": {
        name: "双均线策略",
        icon: "📈",
        difficulty: "⭐ 入门",
        type: "trend",
        description: "利用两条不同周期的移动平均线交叉产生交易信号。短期均线上穿长期均线时做多，下穿时做空。",
        suitable: "趋势明显的单边行情",
        unsuitable: "横盘震荡行情",
        params: {
            "fast_period": { name: "快线周期", default: 10, desc: "短期均线周期，越小越灵敏" },
            "slow_period": { name: "慢线周期", default: 30, desc: "长期均线周期，越大越稳定" },
        },
        tips: "适合新手入门，信号清晰易懂。震荡市可能频繁假信号。"
    },
    "rsi_mean_reversion": {
        name: "RSI均值回归策略",
        icon: "🔄",
        difficulty: "⭐⭐ 进阶",
        type: "mean_reversion",
        description: "利用RSI指标判断超买超卖，在极端值时反向交易，赌价格会回归均值。回测表现最好的策略之一。",
        suitable: "震荡行情、区间波动",
        unsuitable: "单边暴涨暴跌行情",
        params: {
            "rsi_period": { name: "RSI周期", default: 14, desc: "RSI计算周期，标准值为14" },
            "oversold": { name: "超卖阈值", default: 30, desc: "RSI低于此值视为超卖，触发买入" },
            "overbought": { name: "超买阈值", default: 70, desc: "RSI高于此值视为超买，触发卖出" },
        },
        tips: "超卖区做多、超买区做空。大趋势中可能逆势被套，建议配合趋势过滤。"
    },
    "macd": {
        name: "MACD策略",
        icon: "📊",
        difficulty: "⭐⭐ 进阶",
        type: "trend",
        description: "经典的趋势跟踪指标。通过快慢EMA的差值（MACD线）和信号线的关系判断买卖点。",
        suitable: "趋势行情",
        unsuitable: "窄幅震荡",
        params: {
            "fast_period": { name: "快线周期", default: 12, desc: "快速EMA周期" },
            "slow_period": { name: "慢线周期", default: 26, desc: "慢速EMA周期" },
            "signal_period": { name: "信号线周期", default: 9, desc: "信号线EMA周期" },
        },
        tips: "金叉买入、死叉卖出。零轴上方多头强势，下方空头强势。"
    },
    "bollinger_bands": {
        name: "布林带策略",
        icon: "🎯",
        difficulty: "⭐⭐ 进阶",
        type: "mean_reversion",
        description: "基于统计学原理，价格在布林带上下轨之间波动的概率约95%。触及下轨做多，触及上轨做空。",
        suitable: "区间震荡行情",
        unsuitable: "单边突破行情",
        params: {
            "period": { name: "周期", default: 20, desc: "布林带中轨（SMA）周期" },
            "std_dev": { name: "标准差倍数", default: 2.0, desc: "带宽倍数，越大带越宽" },
        },
        tips: "价格触及上下轨是入场信号，但突破布林带时不要逆势。配合RSI过滤效果更好。"
    },
    "supertrend": {
        name: "超级趋势策略",
        icon: "🧭",
        difficulty: "⭐⭐ 进阶",
        type: "trend",
        description: "基于ATR的动态趋势跟踪指标。价格在上轨上方做多，在下轨下方做空，翻转时反向。",
        suitable: "趋势行情（牛市或熊市）",
        unsuitable: "横盘震荡",
        params: {
            "atr_period": { name: "ATR周期", default: 10, desc: "平均真实波幅周期" },
            "multiplier": { name: "乘数", default: 3.0, desc: "ATR乘数，越大信号越少" },
        },
        tips: "趋势市中表现优秀，震荡市中频繁翻转。适合做波段。"
    },
    "turtle": {
        name: "海龟交易策略",
        icon: "🐢",
        difficulty: "⭐⭐ 进阶",
        type: "trend",
        description: "著名的趋势跟踪策略。突破N日高点做多，跌破N日低点做空。以慢取胜，不预测只跟随。",
        suitable: "长期趋势行情",
        unsuitable: "短期震荡",
        params: {
            "entry_period": { name: "入场周期", default: 20, desc: "突破周期，经典值为20" },
            "exit_period": { name: "出场周期", default: 10, desc: "出场周期，经典值为10" },
        },
        tips: "海龟交易的精髓在于严格止损和仓位管理，不是简单的突破交易。"
    },
    "grid": {
        name: "网格交易策略",
        icon: "📐",
        difficulty: "⭐⭐⭐ 中高级",
        type: "grid",
        description: "在设定价格区间内均匀布置买卖单，价格波动时自动低买高卖。震荡市神器。",
        suitable: "区间震荡行情",
        unsuitable: "单边暴涨暴跌（可能被套）",
        params: {
            "grid_count": { name: "网格层数", default: 10, desc: "网格层数，越多越密" },
            "grid_range_pct": { name: "网格范围%", default: 10, desc: "价格区间的百分比宽度" },
        },
        tips: "单边行情可能被套牢。建议在震荡区间使用，并设好止损。"
    },
    "trend_follower": {
        name: "趋势跟踪策略",
        icon: "🏃",
        difficulty: "⭐⭐⭐ 中高级",
        type: "trend",
        description: "综合ROC动量、ADX趋势强度和均线方向的趋势跟踪系统。多重确认后才入场。",
        suitable: "强势单边行情",
        unsuitable: "无方向震荡",
        params: {},
        tips: "多重过滤减少假信号，但也可能错过一些机会。追求高胜率而非高频次。"
    },
    "mean_reversion_v2": {
        name: "均值回归V2",
        icon: "🔁",
        difficulty: "⭐⭐⭐ 中高级",
        type: "mean_reversion",
        description: "升级版均值回归，综合RSI、布林带位置和成交量三重确认，信号质量更高。",
        suitable: "震荡行情",
        unsuitable: "趋势行情",
        params: {},
        tips: "比V1版信号更可靠，但信号频率降低。适合耐心等待机会的交易者。"
    },
    "funding_arb": {
        name: "资金费率套利",
        icon: "💰",
        difficulty: "⭐⭐ 进阶",
        type: "arbitrage",
        description: "利用永续合约的资金费率机制套利。当资金费率极端时反向开仓，赚取费率收益。",
        suitable: "资金费率极端时（>0.1%或<-0.05%）",
        unsuitable: "费率正常时",
        params: {},
        tips: "低风险策略，但收益也相对有限。需要持仓等待费率结算（每8小时）。"
    },
    "adaptive": {
        name: "自适应策略",
        icon: "🧠",
        difficulty: "⭐⭐⭐ 中高级",
        type: "trend",
        description: "根据市场ADX趋势强度自动切换策略。趋势强用趋势跟踪，趋势弱用均值回归。",
        suitable: "所有行情（自动适应）",
        unsuitable: "无明显优缺点",
        params: {},
        tips: "理论上全行情适用，但策略切换有滞后性。是高级策略的入门选择。"
    },
    "smart_meta": {
        name: "智能元策略",
        icon: "🤖",
        difficulty: "⭐⭐⭐⭐ 高级",
        type: "ensemble",
        description: "策略的策略。根据市场状态（趋势/震荡/高波动）自动选择最优子策略执行。",
        suitable: "所有行情",
        unsuitable: "需要较多计算资源",
        params: {},
        tips: "相当于一个自动策略经理。手机运行可能略慢。"
    },
    "ensemble_conservative": {
        name: "保守组合",
        icon: "🛡️",
        difficulty: "⭐ 入门",
        type: "ensemble",
        description: "RSI + 布林带 双策略投票。两个策略都同意时才交易，信号少但可靠性高。",
        suitable: "追求稳定",
        unsuitable: "想频繁交易",
        params: {},
        tips: "适合不想频繁操作、追求胜率的用户。"
    },
    "ensemble_balanced": {
        name: "平衡组合",
        icon: "⚖️",
        difficulty: "⭐⭐ 进阶",
        type: "ensemble",
        description: "RSI + 布林带 + MACD 三策略投票。多数同意才交易，平衡信号数量和质量。",
        suitable: "大多数行情",
        unsuitable: "极端行情",
        params: {},
        tips: "最推荐的组合策略，信号质量和数量平衡得最好。"
    },
    "ensemble_aggressive": {
        name: "激进组合",
        icon: "🔥",
        difficulty: "⭐⭐ 进阶",
        type: "ensemble",
        description: "RSI + 布林带 + MACD + 双均线 四策略投票。信号多但假信号也多。",
        suitable: "活跃行情",
        unsuitable: "保守型用户",
        params: {},
        tips: "信号频率高，但需要配合严格风控。适合喜欢频繁交易的用户。"
    },
    "ensemble_trend": {
        name: "趋势组合",
        icon: "📈",
        difficulty: "⭐⭐ 进阶",
        type: "ensemble",
        description: "超级趋势 + 海龟 + MACD 三趋势策略组合。专攻趋势行情。",
        suitable: "趋势行情",
        unsuitable: "震荡行情",
        params: {},
        tips: "牛市和熊市表现好，震荡市可能持续亏损。需要判断市场大方向。"
    },
};

// 难度颜色映射
const DIFFICULTY_COLORS = {
    "⭐ 入门": "#4caf50",
    "⭐⭐ 进阶": "#2196f3",
    "⭐⭐⭐ 中高级": "#ff9800",
    "⭐⭐⭐⭐ 高级": "#f44336",
};

// 策略信号人话解读
const SIGNAL_EXPLAINER = {
    "rsi_mean_reversion": {
        "LONG": "RSI进入超卖区（低于30），说明近期跌太多了，价格可能反弹。此时买入，赌价格回归正常水平。",
        "SHORT": "RSI进入超买区（高于70），说明近期涨太多了，价格可能回调。此时卖出，落袋为安。",
    },
    "dual_ma": {
        "LONG": "短期均线上穿长期均线（金叉），说明短期走势强于长期，上涨趋势可能开始。",
        "SHORT": "短期均线下穿长期均线（死叉），说明短期走势弱于长期，下跌趋势可能开始。",
    },
    "macd": {
        "LONG": "MACD线上穿信号线（金叉），且柱状图由绿转红，说明多头力量增强。",
        "SHORT": "MACD线下穿信号线（死叉），且柱状图由红转绿，说明空头力量增强。",
    },
    "bollinger_bands": {
        "LONG": "价格触及布林带下轨，统计上只有5%的概率会在这里。价格大概率会反弹回中轨。",
        "SHORT": "价格触及布林带上轨，统计上只有5%的概率会在这里。价格大概率会回落到中轨。",
    },
    "supertrend": {
        "LONG": "超级趋势指标翻转为上升，上轨从阻力变为支撑，趋势可能转为多头。",
        "SHORT": "超级趋势指标翻转为下降，下轨从支撑变为阻力，趋势可能转为空头。",
    },
    "turtle": {
        "LONG": "价格突破N日最高点（唐奇安通道上沿），海龟法则认为这是趋势启动的信号。",
        "SHORT": "价格跌破N日最低点（唐奇安通道下沿），海龟法则认为下跌趋势已形成。",
    },
    "grid": {
        "LONG": "价格跌到网格买入层，系统自动在低价位挂单买入。网格越低越买。",
        "SHORT": "价格涨到网格卖出层，系统自动在高价位挂单卖出。网格越高越卖。",
    },
    "trend_follower": {
        "LONG": "ROC动量+ADX趋势强度+均线方向三重确认上涨趋势，入场信号可靠性较高。",
        "SHORT": "ROC动量+ADX趋势强度+均线方向三重确认下跌趋势，做空信号可靠性较高。",
    },
    "mean_reversion_v2": {
        "LONG": "RSI超卖+布林带下轨+成交量放大，三重确认超跌反弹机会。",
        "SHORT": "RSI超买+布林带上轨+成交量放大，三重确认超涨回调机会。",
    },
    "funding_arb": {
        "LONG": "资金费率极端为负（空头付钱给多头），做多可以赚取费率收益。",
        "SHORT": "资金费率极端为正（多头付钱给空头），做空可以赚取费率收益。",
    },
};
