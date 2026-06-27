// 错误码 → 友好提示映射
const ERROR_MESSAGES = {
    // 网络错误
    'Failed to fetch': '🌐 网络连接失败，请检查手机网络后重试',
    'NetworkError': '🌐 网络不给力，请稍后重试',
    'Network request failed': '🌐 网络请求超时，请检查网络连接',

    // 交易所错误
    'AuthenticationError': '🔑 API Key 验证失败，请检查 Key 是否正确',
    'Invalid API-key': '🔑 API Key 格式不正确，请重新填写',
    'Account has insufficient balance': '💰 账户余额不足，无法开仓',
    'Rate limit exceeded': '🐢 请求太频繁，请等待30秒后重试',
    'Exchange not available': '⏳ 交易所暂时不可用，请稍后重试',

    // 交易错误
    'Position does not exist': '📋 没有找到该持仓，可能已经平仓',
    'Order would immediately trigger': '⚠️ 订单价格异常，请调整后重试',
    'ReduceOnly order rejected': '⚠️ 减仓单被拒绝，请检查持仓',

    // 系统错误
    'database is locked': '⏳ 系统繁忙，请稍后重试',
    'no such table': '⚠️ 数据异常，请重启APP',
    'Cannot connect to': '🌐 无法连接交易所服务器',

    // 通用
    'HTTP 400': '⚠️ 请求参数有误，请检查输入',
    'HTTP 401': '🔑 认证失败，请重新登录',
    'HTTP 403': '🚫 没有权限执行此操作',
    'HTTP 404': '🔍 请求的资源不存在',
    'HTTP 500': '💥 服务器内部错误，请稍后重试',
    'HTTP 502': '🌐 网关错误，交易所可能正在维护',
    'HTTP 503': '⏳ 服务暂时不可用，请稍后重试',
};

/**
 * 将原始错误信息转换为用户友好提示
 */
function friendlyError(originalError) {
    const msg = String(originalError || '未知错误');

    // 精确匹配
    for (const [key, friendly] of Object.entries(ERROR_MESSAGES)) {
        if (msg.includes(key)) {
            return friendly;
        }
    }

    // HTTP状态码匹配
    const httpMatch = msg.match(/HTTP (\d{3})/);
    if (httpMatch && ERROR_MESSAGES[`HTTP ${httpMatch[1]}`]) {
        return ERROR_MESSAGES[`HTTP ${httpMatch[1]}`];
    }

    // 默认
    return `⚠️ ${msg}`;
}
