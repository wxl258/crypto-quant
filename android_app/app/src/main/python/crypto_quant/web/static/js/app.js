/**
 * Main Application - Navigation & Core Logic
 */
'use strict';

// 防御性检查：确保依赖函数存在
if (typeof friendlyError !== 'function') {
    window.friendlyError = function(msg) { return '⚠️ ' + (msg || '未知错误'); };
}
if (typeof showToast !== 'function') {
    window.showToast = function(msg, type) { console.log('[' + (type || 'info') + ']', msg); };
}

const API = {
    async get(url) {
        const res = await fetch(url);
        if (!res.ok) throw new Error(friendlyError(`HTTP ${res.status}`));
        return res.json();
    },
    async post(url, data) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(friendlyError(err.detail || `HTTP ${res.status}`));
        }
        return res.json();
    },
    async postOptimize(url, data, onProgress) {
        // Streaming support for optimization progress
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(friendlyError(err.detail || `HTTP ${res.status}`));
        }
        return res.json();
    },
};

// ── Lazy module loading ──
const moduleCache = {};
function loadModule(name, path) {
    if (moduleCache[name]) return Promise.resolve();
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = path;
        script.onload = () => { moduleCache[name] = true; resolve(); };
        script.onerror = reject;
        document.head.appendChild(script);
    });
}

// ── API response cache (5s TTL) ──
const apiCache = {};
function cachedGet(url, ttl = 5000) {
    const now = Date.now();
    if (apiCache[url] && (now - apiCache[url].time) < ttl) {
        return Promise.resolve(apiCache[url].data);
    }
    return API.get(url).then(data => {
        apiCache[url] = { data, time: now };
        return data;
    });
}

// ── WebSocket pause/resume ──
let _wsPaused = false;

function pauseWebSockets() {
    if (_wsPaused) return;
    _wsPaused = true;
    if (_wsAccount) { _wsAccount.onmessage = null; _wsAccount.onclose = null; }
    if (_wsMarket) { _wsMarket.onmessage = null; _wsMarket.onclose = null; }
}

function resumeWebSockets() {
    if (!_wsPaused) return;
    _wsPaused = false;
    if (_wsAccount) {
        _wsAccount.onmessage = (e) => {
            try {
                const msg = JSON.parse(e.data);
                if (msg.type === 'account') updateDashboardFromWS(msg.data);
            } catch (err) { /* skip */ }
        };
        _wsAccount.onclose = () => scheduleWSReconnect();
    }
    if (_wsMarket) {
        _wsMarket.onmessage = (e) => {
            try {
                const msg = JSON.parse(e.data);
                if (msg.type === 'ticker') updateTickerFromWS(msg);
            } catch (err) { /* skip */ }
        };
        _wsMarket.onclose = () => scheduleWSReconnect();
    }
}

// Navigation
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
        e.preventDefault();
        const page = item.dataset.page;
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        item.classList.add('active');
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        const pageEl = document.getElementById(`page-${page}`);
        if (pageEl) pageEl.classList.add('active');

        // Pause/resume WebSocket based on page
        if (page === 'dashboard') {
            resumeWebSockets();
            loadModule('dashboard', '/static/js/dashboard.js').then(refreshDashboard);
        } else {
            pauseWebSockets();
            if (page === 'strategy') loadModule('strategy', '/static/js/strategy.js').then(initStrategyPage);
            if (page === 'strategy-store') loadModule('strategy_store', '/static/js/strategy_store.js').then(initStrategyStorePage);
            if (page === 'backtest') loadModule('backtest', '/static/js/backtest.js').then(initBacktestPage);
            if (page === 'trades') refreshTrades();
            if (page === 'risk') refreshRisk();
            if (page === 'lab') initLabPage();
            if (page === 'backup') refreshBackups();
            if (page === 'alerts') refreshAlerts();
            if (page === 'recommend') loadRecommend();
        }
    });
});

// Format helpers
function fmtUSD(n) {
    if (n === undefined || n === null) return '--';
    return Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(n) {
    if (n === undefined || n === null) return '--';
    const v = Number(n);
    const sign = v >= 0 ? '+' : '';
    return `${sign}${v.toFixed(2)}%`;
}

function fmtTime(ts) {
    if (!ts) return '--';
    return new Date(ts).toLocaleString('zh-CN');
}

// HTML escape to prevent XSS
function escHtml(str) {
    if (str === undefined || str === null) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

// Loading helper (kept for potential future use)
function showLoading(el, colSpan = 10) {
    el.textContent = '';
    const row = document.createElement('tr');
    row.className = 'empty-row';
    const td = document.createElement('td');
    td.colSpan = colSpan;
    const spinner = document.createElement('div');
    spinner.className = 'spinner';
    td.appendChild(spinner);
    td.appendChild(document.createTextNode(' 加载中...'));
    row.appendChild(td);
    el.appendChild(row);
}

// ── WebSocket connection ──
let _wsAccount = null;
let _wsMarket = null;
let _wsReconnectTimer = null;
let _wsPingInterval = null;
let _wsReconnectDelay = 3000;      // Current reconnect delay (exponential backoff)
const _WS_MIN_DELAY = 3000;       // 3 seconds minimum
const _WS_MAX_DELAY = 30000;      // 30 seconds cap

function connectWebSocket() {
    // 先检查后端 HTTP API 是否可用，避免无效的 WebSocket 连接循环
    fetch('/api/mode').then(r => {
        if (!r.ok) throw new Error('backend offline');
        return r.json();
    }).catch(() => {
        // 后端不可用，延迟重试
        scheduleWSReconnect();
        return;
    }).then(data => {
        if (!data) return; // backend offline, already handled
        _doConnectWebSocket();
    });
}

function _doConnectWebSocket() {
    // Clean up existing sockets before creating new ones
    if (_wsAccount) {
        _wsAccount.onclose = null;
        _wsAccount.close();
        _wsAccount = null;
    }
    if (_wsMarket) {
        _wsMarket.onclose = null;
        _wsMarket.close();
        _wsMarket = null;
    }
    // Clear existing ping interval
    if (_wsPingInterval) {
        clearInterval(_wsPingInterval);
        _wsPingInterval = null;
    }

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const base = `${protocol}//${location.host}`;

    // Account channel
    _wsAccount = new WebSocket(`${base}/api/ws/account`);
    _wsAccount.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'account') {
                updateDashboardFromWS(msg.data);
            }
        } catch (err) { /* malformed message, skip */ }
    };
    _wsAccount.onopen = () => {
        // Reset backoff on successful connection
        _wsReconnectDelay = _WS_MIN_DELAY;
    };
    _wsAccount.onclose = () => scheduleWSReconnect();

    // Market channel
    _wsMarket = new WebSocket(`${base}/api/ws/market`);
    _wsMarket.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'ticker') {
                updateTickerFromWS(msg);
            }
        } catch (err) { /* malformed message, skip */ }
    };
    _wsMarket.onclose = () => scheduleWSReconnect();

    // Keepalive ping every 25s (single interval, cleared on reconnect)
    _wsPingInterval = setInterval(() => {
        if (_wsAccount && _wsAccount.readyState === WebSocket.OPEN) _wsAccount.send('ping');
        if (_wsMarket && _wsMarket.readyState === WebSocket.OPEN) _wsMarket.send('ping');
    }, 25000);
}

function scheduleWSReconnect() {
    if (_wsReconnectTimer) return;
    _wsReconnectTimer = setTimeout(() => {
        _wsReconnectTimer = null;
        connectWebSocket();
    }, _wsReconnectDelay);
    // Exponential backoff: 3s → 6s → 12s → 24s → 30s (capped)
    _wsReconnectDelay = Math.min(_wsReconnectDelay * 2, _WS_MAX_DELAY);
}

function updateDashboardFromWS(data) {
    if (!data) return;
    const totalEquityEl = document.getElementById('total-equity');
    const availableBalanceEl = document.getElementById('available-balance');
    const positionCountEl = document.getElementById('position-count');
    const totalTradesEl = document.getElementById('total-trades');
    const pnlEl = document.getElementById('total-pnl');

    if (totalEquityEl) totalEquityEl.textContent = fmtUSD(data.total_equity);
    if (availableBalanceEl) availableBalanceEl.textContent = fmtUSD(data.capital);
    if (positionCountEl) positionCountEl.textContent = data.open_positions;
    if (totalTradesEl) totalTradesEl.textContent = data.total_trades;

    if (pnlEl) {
        const pnl = data.total_pnl;
        const pnlPct = data.total_pnl_pct;
        pnlEl.textContent = `${fmtUSD(pnl)} (${fmtPct(pnlPct)})`;
        pnlEl.className = `stat-change ${(pnl || 0) >= 0 ? 'positive' : 'negative'}`;
    }
}

function updateTickerFromWS(msg) {
    // Update K-line chart price line in real-time if chart is visible
}

// ── Toast notification system ──
function showToast(message, type = 'info') {
    // Create container if not exists
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    // Trigger animation
    requestAnimationFrame(() => toast.classList.add('show'));

    // Auto-dismiss after 3 seconds
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ── Mobile sidebar toggle ──
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const btn = document.getElementById('hamburger-btn');
    if (sidebar) {
        sidebar.classList.toggle('sidebar-open');
        btn.textContent = sidebar.classList.contains('sidebar-open') ? '✕' : '☰';
    }
}

// Close sidebar when clicking a nav item on mobile
document.addEventListener('click', (e) => {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    if (e.target.closest('.nav-item') && sidebar.classList.contains('sidebar-open')) {
        sidebar.classList.remove('sidebar-open');
        const btn = document.getElementById('hamburger-btn');
        if (btn) btn.textContent = '☰';
    }
});

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    connectWebSocket();
    // Dashboard is the default page — eager-load its module
    loadModule('dashboard', '/static/js/dashboard.js').then(() => {
        refreshDashboard();
        loadSignals();
    });
    loadCurrentMode();
    checkQuickStart();
});

// Fallback: still refresh every 60s as backup
setInterval(() => {
    const dash = document.getElementById('page-dashboard');
    if (dash && dash.classList.contains('active')) {
        refreshDashboard();
        loadSignals();
    }
}, 60000);

// ── Mode switching ──
async function loadCurrentMode() {
    try {
        const data = await API.get('/api/mode');
        updateModeUI(data.mode);
    } catch (e) {
        console.error('Failed to load mode:', e);
    }
}

function updateModeUI(mode) {
    const badge = document.getElementById('sidebar-mode-badge');
    const display = document.getElementById('mode-display');
    const toggle = document.getElementById('mode-switch');
    const isLive = mode === 'live';

    if (badge) {
        badge.textContent = isLive ? '实盘' : '模拟盘';
        badge.style.background = isLive ? 'rgba(76,175,132,0.15)' : 'rgba(255,167,38,0.15)';
        badge.style.color = isLive ? 'var(--green)' : 'var(--orange)';
    }
    if (display) {
        display.textContent = isLive ? '实盘' : '模拟盘';
    }
    if (toggle) {
        toggle.checked = isLive;
    }
}

async function toggleMode() {
    const toggle = document.getElementById('mode-switch');
    const newMode = toggle.checked ? 'live' : 'paper';
    try {
        const result = await API.post('/api/mode', { mode: newMode });
        updateModeUI(result.mode);
        showToast(`已切换至${result.mode === 'live' ? '实盘' : '模拟盘'}模式`, 'info');
    } catch (e) {
        // Revert toggle on failure
        toggle.checked = !toggle.checked;
        showToast('模式切换失败: ' + friendlyError(e.message), 'error');
    }
}

// ── 一键开箱 ──
async function checkQuickStart() {
    try {
        const status = await API.get('/api/live/status');
        const area = document.getElementById('quick-start-area');
        if (area && (!status.bots || Object.keys(status.bots).length === 0)) {
            area.style.display = 'block';
        }
    } catch(e) {
        // Silently ignore - quick start area stays hidden
    }
}

async function quickStart() {
    const btn = document.getElementById('quick-start-btn');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = '⏳ 启动中...';
    try {
        const result = await API.post('/api/quick-start', {});
        showToast(result.message, 'success');
        const area = document.getElementById('quick-start-area');
        if (area) area.style.display = 'none';
        setTimeout(() => refreshDashboard(), 2000);
    } catch(e) {
        showToast('启动失败: ' + friendlyError(e.message), 'error');
        btn.disabled = false;
        btn.textContent = '🚀 一键启动（模拟盘）';
    }
}

// ── Strategy Lab ──
function initLabPage() {
    // No heavy init needed - just ensure the panel is reset
    const panel = document.getElementById('lab-results-panel');
    if (panel) panel.style.display = 'none';
}

async function runLabTest() {
    const strategy = document.getElementById('lab-strategy').value;
    const symbol = document.getElementById('lab-symbol').value;
    const interval = '1h';
    const capital = 10000;
    const days = 90;

    const btn = document.querySelector('#page-lab .btn-primary');
    btn.textContent = '⏳ 测试中...';
    btn.disabled = true;

    try {
        const result = await API.post('/api/backtest', {
            strategy, symbol, interval, initial_capital: capital, days, params: {}
        });
        const m = result.metrics;

        // Show results
        const panel = document.getElementById('lab-results-panel');
        panel.style.display = 'block';

        const retEl = document.getElementById('lab-return');
        retEl.textContent = fmtPct(m.total_return);
        retEl.style.color = m.total_return >= 0 ? 'var(--green)' : 'var(--red)';

        const wrEl = document.getElementById('lab-winrate');
        wrEl.textContent = fmtPct(m.win_rate);
        wrEl.style.color = m.win_rate >= 50 ? 'var(--green)' : 'var(--orange)';

        const ddEl = document.getElementById('lab-dd');
        ddEl.textContent = fmtPct(m.max_drawdown);
        ddEl.style.color = Math.abs(m.max_drawdown) <= 20 ? 'var(--green)' : 'var(--red)';

        // One-sentence summary
        const verdict = m.total_return >= 10 ? '表现不错' : m.total_return >= 0 ? '表现一般' : '表现不佳';
        document.getElementById('lab-summary').innerHTML =
            `这个策略在过去${days}天<strong style="color:${m.total_return >= 0 ? 'var(--green)' : 'var(--red)'}">${verdict}</strong>，` +
            `收益<strong>${fmtPct(m.total_return)}</strong>，胜率<strong>${fmtPct(m.win_rate)}</strong>`;

        panel.scrollIntoView({ behavior: 'smooth' });
    } catch (e) {
        showToast('测试失败: ' + friendlyError(e.message), 'error');
    } finally {
        btn.textContent = '🚀 开始测试';
        btn.disabled = false;
    }
}

// ── Double-click dashboard title to refresh ──
document.addEventListener('DOMContentLoaded', () => {
    const h2 = document.querySelector('#page-dashboard .page-header h2');
    if (h2) {
        h2.style.cursor = 'pointer';
        h2.title = '双击刷新数据';
        h2.addEventListener('dblclick', () => {
            refreshDashboard();
            loadSignals();
            showToast('数据已刷新', 'info');
        });
    }
});

// ── Swipe left on position rows to show close button (mobile) ──
let _swipeStartX = 0;
let _swipeTarget = null;
document.addEventListener('touchstart', (e) => {
    const row = e.target.closest('#positions-table tbody tr');
    if (row && !row.classList.contains('empty-row')) {
        _swipeStartX = e.touches[0].clientX;
        _swipeTarget = row;
    }
}, { passive: true });
document.addEventListener('touchend', (e) => {
    if (!_swipeTarget) return;
    const deltaX = (e.changedTouches[0]?.clientX || _swipeStartX) - _swipeStartX;
    if (deltaX < -60) {
        // Swipe left - show close button or confirm
        const symbolCell = _swipeTarget.querySelector('td:first-child');
        if (symbolCell) {
            const symbol = symbolCell.textContent.trim();
            if (symbol && confirm(`确认平仓 ${symbol}？`)) {
                closePosition(symbol);
            }
        }
    }
    _swipeTarget = null;
});

// ── Signal loading and display ──
async function loadSignals() {
    try {
        const data = await API.get('/api/live/status');
        const panel = document.getElementById('signals-panel');
        const grid = document.getElementById('signals-grid');
        if (!panel || !grid) return;

        // Update time
        document.getElementById('signal-update-time').textContent =
            '更新于 ' + new Date().toLocaleTimeString('zh-CN');

        const bots = data.bots || {};
        const botList = Object.values(bots);

        if (botList.length === 0) {
            panel.style.display = 'block';
            grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--text-muted);padding:20px;">暂无交易信号，策略监控中...</div>';
            return;
        }

        panel.style.display = 'block';
        grid.innerHTML = botList.map(bot => {
            const strategyKey = bot.strategy || 'unknown';
            const guide = (typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[strategyKey]) || null;
            const strategyName = guide ? guide.name : getStrategyLabel(strategyKey);
            const icon = guide ? guide.icon : '📈';
            const direction = bot.side || 'NEUTRAL';
            const dirLabel = direction === 'LONG' ? '买入🟢' : direction === 'SHORT' ? '卖出🔴' : '观望🟡';
            const dirClass = direction === 'LONG' ? 'buy' : direction === 'SHORT' ? 'sell' : 'neutral';
            const confidence = bot.confidence || 0;
            const stars = '⭐'.repeat(Math.min(5, Math.round(confidence * 5)));
            const price = bot.current_price ? fmtUSD(bot.current_price) : '--';
            return `
                <div class="signal-card" onclick="showSignalDetail('${strategyKey}', '${direction}', ${JSON.stringify(guide).replace(/"/g, '&quot;')})" style="cursor:pointer;">
                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
                        <span style="font-size:20px;">${icon}</span>
                        <div>
                            <div style="font-weight:600;font-size:14px;">${escHtml(strategyName)}</div>
                            <div style="font-size:11px;color:var(--text-muted);">${escHtml(bot.symbol || '--')}</div>
                        </div>
                    </div>
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <span class="signal-badge ${dirClass}">${dirLabel}</span>
                        <span style="font-size:12px;color:var(--text-muted);">${stars}</span>
                    </div>
                    <div style="margin-top:8px;font-size:13px;font-weight:600;">$${price}</div>
                </div>`;
        }).join('');
    } catch (e) {
        console.error('Failed to load signals:', e);
    }
}

// ── Signal detail popup (BottomSheet style) ──
function showSignalDetail(strategyKey, direction, guideData) {
    const explainer = (typeof SIGNAL_EXPLAINER !== 'undefined' && SIGNAL_EXPLAINER[strategyKey]) || null;
    const guide = guideData || ((typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[strategyKey]) || null);

    let explanation = '';
    if (explainer && explainer[direction]) {
        explanation = explainer[direction];
    } else {
        explanation = direction === 'LONG' ? '策略发出买入信号，建议关注入场时机。' :
                     direction === 'SHORT' ? '策略发出卖出信号，建议关注出场时机。' :
                     '策略当前无明确方向信号，建议观望等待。';
    }

    const strategyName = guide ? guide.name : getStrategyLabel(strategyKey);
    const icon = guide ? guide.icon : '📈';
    const dirLabel = direction === 'LONG' ? '买入' : direction === 'SHORT' ? '卖出' : '观望';
    const dirColor = direction === 'LONG' ? 'var(--green)' : direction === 'SHORT' ? 'var(--red)' : 'var(--orange)';

    const overlay = document.createElement('div');
    overlay.className = 'signal-detail-overlay';
    overlay.innerHTML = `
        <div class="signal-detail-sheet">
            <div class="signal-detail-handle"></div>
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">
                <span style="font-size:32px;">${icon}</span>
                <div>
                    <div style="font-size:18px;font-weight:700;">${escHtml(strategyName)}</div>
                    <div style="font-size:14px;color:${dirColor};font-weight:600;">${dirLabel}信号</div>
                </div>
            </div>
            <div style="background:rgba(0,0,0,0.2);border-radius:8px;padding:16px;margin-bottom:16px;">
                <div style="font-size:14px;line-height:1.8;color:var(--text-primary);">${explanation}</div>
            </div>
            ${guide ? `
            <div style="font-size:12px;color:var(--text-muted);line-height:1.6;">
                <div>📊 适合行情: ${escHtml(guide.suitable || '--')}</div>
                <div>⚠️ 不适合: ${escHtml(guide.unsuitable || '--')}</div>
                ${guide.tips ? '<div>💡 ' + escHtml(guide.tips) + '</div>' : ''}
            </div>` : ''}
            <button class="btn btn-primary btn-block" style="margin-top:16px;" onclick="this.closest('.signal-detail-overlay').remove()">知道了</button>
        </div>`;
    document.body.appendChild(overlay);

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.remove();
    });

    // Animate in
    requestAnimationFrame(() => {
        const sheet = overlay.querySelector('.signal-detail-sheet');
        if (sheet) sheet.style.transform = 'translateY(0)';
    });
}
function updateSafetyShield() {
    const shield = document.getElementById('safety-shield');
    const title = document.getElementById('safety-title');
    const detail = document.getElementById('safety-detail');
    const badges = document.getElementById('safety-badges');
    if (!shield || !title || !detail || !badges) return;

    const sl = parseFloat(document.getElementById('risk-sl').value) || 0;
    const tp = parseFloat(document.getElementById('risk-tp').value) || 0;
    const dailyLoss = parseFloat(document.getElementById('risk-daily-loss').value) || 0;
    const maxPos = parseFloat(document.getElementById('risk-max-pos').value) || 0;

    const stopLossOk = sl > 0;
    const takeProfitOk = tp > 0;
    const dailyLimitOk = dailyLoss > 0;
    const positionLimitOk = maxPos > 0;

    const allProtected = stopLossOk && takeProfitOk && dailyLimitOk && positionLimitOk;

    if (allProtected) {
        shield.style.background = 'linear-gradient(135deg, #1b5e20, #2e7d32)';
        title.textContent = '止损保护已开启';
        title.style.color = '#a5d6a7';
        detail.textContent = `单笔止损 ${sl}% | 日亏损上限 ${dailyLoss}% | 连续亏损熔断 3次`;
        detail.style.color = '#81c784';
    } else {
        shield.style.background = 'linear-gradient(135deg, #b71c1c, #c62828)';
        title.textContent = '⚠️ 止损保护未完全开启';
        title.style.color = '#ffcdd2';
        const parts = [];
        if (!stopLossOk) parts.push('止损已关闭');
        if (!takeProfitOk) parts.push('止盈已关闭');
        if (!dailyLimitOk) parts.push('日亏损上限已关闭');
        if (!positionLimitOk) parts.push('仓位限制已关闭');
        detail.textContent = parts.join(' | ');
        detail.style.color = '#ef9a9a';
    }

    badges.innerHTML = 
        `<span style="background: rgba(255,255,255,0.15); padding: 4px 12px; border-radius: 12px; font-size: 12px; color: ${stopLossOk ? '#c8e6c9' : '#ef9a9a'};">${stopLossOk ? '✅' : '❌'} 止损单</span>` +
        `<span style="background: rgba(255,255,255,0.15); padding: 4px 12px; border-radius: 12px; font-size: 12px; color: ${takeProfitOk ? '#c8e6c9' : '#ef9a9a'};">${takeProfitOk ? '✅' : '❌'} 止盈单</span>` +
        `<span style="background: rgba(255,255,255,0.15); padding: 4px 12px; border-radius: 12px; font-size: 12px; color: ${dailyLimitOk ? '#c8e6c9' : '#ef9a9a'};">${dailyLimitOk ? '✅' : '❌'} 熔断机制</span>`;
}

// ── Backup & Restore ──
async function refreshBackups() {
    try {
        const [storage, list] = await Promise.all([
            API.get('/api/backup/storage'),
            API.get('/api/backup/list'),
        ]);
        document.getElementById('bk-db-size').textContent = (storage.database_mb || 0).toFixed(1) + ' MB';
        document.getElementById('bk-backup-size').textContent = (storage.backups_mb || 0).toFixed(1) + ' MB';
        document.getElementById('bk-count').textContent = storage.backup_count || 0;

        const tbody = document.getElementById('backups-tbody');
        const backups = list.backups || [];
        if (backups.length === 0) {
            tbody.innerHTML = '<tr class="empty-row"><td colspan="4">暂无备份</td></tr>';
            return;
        }
        tbody.innerHTML = backups.map(b => `
            <tr>
                <td><strong>${escHtml(b.filename)}</strong></td>
                <td>${b.size_mb} MB</td>
                <td>${fmtTime(b.created_at)}</td>
                <td>
                    <button class="btn-sm-card primary" onclick="downloadBackup('${escHtml(b.filename)}')">下载</button>
                    <button class="btn-sm-card" style="border-color:var(--orange);color:var(--orange)" onclick="restoreBackup('${escHtml(b.filename)}')">恢复</button>
                    <button class="btn-sm-card danger" onclick="deleteBackup('${escHtml(b.filename)}')">删除</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        showToast('加载备份失败: ' + friendlyError(e.message), 'error');
    }
}

async function createBackup() {
    try {
        const btn = document.querySelector('#page-backup .btn-primary');
        btn.disabled = true;
        btn.textContent = '⏳ 创建中...';
        const result = await API.post('/api/backup/create', {});
        showToast(result.message, 'success');
        await refreshBackups();
    } catch (e) {
        showToast('创建备份失败: ' + friendlyError(e.message), 'error');
    } finally {
        const btn = document.querySelector('#page-backup .btn-primary');
        btn.disabled = false;
        btn.textContent = '+ 创建备份';
    }
}

function downloadBackup(filename) {
    window.open('/api/backup/download/' + encodeURIComponent(filename), '_blank');
}

async function restoreBackup(filename) {
    if (!confirm('恢复备份将覆盖当前数据，确定继续？')) return;
    try {
        const result = await API.post('/api/backup/restore/' + encodeURIComponent(filename), {});
        showToast(result.message, 'success');
        await refreshBackups();
    } catch (e) {
        showToast('恢复失败: ' + friendlyError(e.message), 'error');
    }
}

async function deleteBackup(filename) {
    if (!confirm('确定删除备份 ' + filename + '？')) return;
    try {
        const result = await API.delete('/api/backup/delete/' + encodeURIComponent(filename));
        showToast(result.message, 'success');
        await refreshBackups();
    } catch (e) {
        showToast('删除失败: ' + friendlyError(e.message), 'error');
    }
}

// Add DELETE method support to API
API.delete = async function(url) {
    const res = await fetch(url, { method: 'DELETE' });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(friendlyError(err.detail || `HTTP ${res.status}`));
    }
    return res.json();
};

// ── Custom Alerts ──
function onAlertTypeChange() {
    const type = document.getElementById('alert-type').value;
    const symbolGroup = document.getElementById('alert-symbol-group');
    const valueLabel = document.getElementById('alert-value-label');
    if (type === 'pnl') {
        if (symbolGroup) symbolGroup.style.display = 'none';
        if (valueLabel) valueLabel.textContent = '亏损阈值 (USDT)';
        document.getElementById('alert-value').value = 100;
        document.getElementById('alert-condition').value = 'below';
    } else {
        if (symbolGroup) symbolGroup.style.display = 'block';
        if (valueLabel) valueLabel.textContent = '阈值';
        document.getElementById('alert-value').value = 60000;
    }
}

async function refreshAlerts() {
    try {
        const data = await API.get('/api/alerts/custom/list');
        const alerts = data.alerts || [];
        const tbody = document.getElementById('alerts-tbody');
        if (alerts.length === 0) {
            tbody.innerHTML = '<tr class="empty-row"><td colspan="7">暂无自定义告警</td></tr>';
            return;
        }
        tbody.innerHTML = alerts.map(a => {
            const typeLabel = a.type === 'price' ? '价格告警' : a.type === 'pnl' ? '盈亏告警' : a.type;
            const condLabel = a.condition === 'above' ? '突破上限' : a.condition === 'below' ? '跌破下限' : a.condition;
            const statusCls = a.triggered ? 'negative' : 'positive';
            const statusLabel = a.triggered ? '已触发' : (a.enabled ? '监控中' : '已禁用');
            return `
                <tr>
                    <td>${escHtml(typeLabel)}</td>
                    <td>${escHtml(a.symbol || '--')}</td>
                    <td>${escHtml(condLabel)}</td>
                    <td>${escHtml(String(a.value))}</td>
                    <td>${escHtml(a.message || '--')}</td>
                    <td><span class="${statusCls}">${statusLabel}</span></td>
                    <td>
                        <button class="btn-sm-card" onclick="toggleAlert('${escHtml(a.id)}', ${!a.enabled})">${a.enabled ? '禁用' : '启用'}</button>
                        <button class="btn-sm-card danger" onclick="deleteAlert('${escHtml(a.id)}')">删除</button>
                    </td>
                </tr>`;
        }).join('');
    } catch (e) {
        showToast('加载告警失败: ' + friendlyError(e.message), 'error');
    }
}

async function createAlert() {
    const type = document.getElementById('alert-type').value;
    const symbol = type === 'pnl' ? '' : document.getElementById('alert-symbol').value;
    const condition = document.getElementById('alert-condition').value;
    const value = parseFloat(document.getElementById('alert-value').value);
    const message = document.getElementById('alert-message').value;

    if (isNaN(value) || value <= 0) {
        showToast('请输入有效的阈值', 'warning');
        return;
    }

    try {
        await API.post('/api/alerts/custom/create', {
            type, symbol, condition, value, message, enabled: true
        });
        showToast('告警已添加', 'success');
        document.getElementById('alert-message').value = '';
        await refreshAlerts();
    } catch (e) {
        showToast('添加告警失败: ' + friendlyError(e.message), 'error');
    }
}

async function toggleAlert(alertId, enabled) {
    try {
        await API.post('/api/alerts/custom/toggle', { alert_id: alertId, enabled });
        showToast(enabled ? '告警已启用' : '告警已禁用', 'info');
        await refreshAlerts();
    } catch (e) {
        showToast('操作失败: ' + friendlyError(e.message), 'error');
    }
}

async function deleteAlert(alertId) {
    if (!confirm('确定删除该告警？')) return;
    try {
        await API.delete('/api/alerts/custom/' + encodeURIComponent(alertId));
        showToast('告警已删除', 'success');
        await refreshAlerts();
    } catch (e) {
        showToast('删除失败: ' + friendlyError(e.message), 'error');
    }
}

// ── Trade Notes ──
async function showNoteEditor(tradeId, currentNote) {
    const overlay = document.createElement('div');
    overlay.className = 'signal-detail-overlay';
    overlay.innerHTML = `
        <div class="signal-detail-sheet">
            <div class="signal-detail-handle"></div>
            <h3 style="margin-bottom:12px;">📝 交易笔记</h3>
            <textarea id="note-textarea" style="width:100%;min-height:120px;background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);color:var(--text-primary);padding:12px;font-size:14px;resize:vertical;" placeholder="记录这笔交易的想法...">${escHtml(currentNote || '')}</textarea>
            <div style="display:flex;gap:8px;margin-top:12px;">
                <button class="btn btn-primary" style="flex:1;" onclick="saveNote(${tradeId}, document.getElementById('note-textarea').value, this.closest('.signal-detail-overlay'))">保存</button>
                <button class="btn" style="flex:1;background:var(--bg-hover);color:var(--text-primary);" onclick="this.closest('.signal-detail-overlay').remove()">取消</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.remove();
    });
    requestAnimationFrame(() => {
        const sheet = overlay.querySelector('.signal-detail-sheet');
        if (sheet) sheet.style.transform = 'translateY(0)';
    });
}

async function saveNote(tradeId, noteText, overlay) {
    try {
        await API.post('/api/trade/note', { trade_id: tradeId, note: noteText });
        showToast('笔记已保存', 'success');
        if (overlay) overlay.remove();
        // Refresh trades table if on trades page
        if (document.getElementById('page-trades').classList.contains('active')) {
            refreshTrades();
        }
    } catch (e) {
        showToast('保存失败: ' + friendlyError(e.message), 'error');
    }
}

// ── AI Strategy Recommend ──
async function loadRecommend() {
    const loading = document.getElementById('recommend-loading');
    const content = document.getElementById('recommend-content');
    const symbol = document.getElementById('recommend-symbol')?.value || 'BTCUSDT';

    if (loading) loading.style.display = 'block';
    if (content) content.style.display = 'none';

    try {
        const data = await API.get('/api/recommend/analyze?symbol=' + encodeURIComponent(symbol));

        // Show content
        if (loading) loading.style.display = 'none';
        if (content) content.style.display = 'block';

        // Error message
        const errEl = document.getElementById('rec-error-msg');
        if (data.error) {
            if (errEl) { errEl.style.display = 'block'; errEl.textContent = data.error; }
        } else {
            if (errEl) errEl.style.display = 'none';
        }

        // Current price
        const priceEl = document.getElementById('rec-current-price');
        if (priceEl && data.current_price) {
            priceEl.textContent = fmtUSD(data.current_price);
        }

        // Market state cards
        const ms = data.market_state || {};
        const trendBar = document.getElementById('rec-trend-bar');
        if (trendBar) {
            trendBar.style.width = (ms.trend_strength || 0) + '%';
        }
        const trendStrength = document.getElementById('rec-trend-strength');
        if (trendStrength) trendStrength.textContent = (ms.trend_strength || 0) + '%';

        const trendDir = document.getElementById('rec-trend-dir');
        if (trendDir) {
            const dirMap = { up: '🐂 多头', down: '🐻 空头', neutral: '↔️ 中性' };
            trendDir.textContent = dirMap[ms.trend_direction] || '--';
        }

        const volEl = document.getElementById('rec-volatility');
        if (volEl) {
            const volMap = { high: '🌊 高波动', normal: '📊 正常', low: '😴 低波动' };
            volEl.textContent = volMap[ms.volatility_regime] || '--';
        }

        const rsiVal = document.getElementById('rec-rsi-value');
        if (rsiVal) rsiVal.textContent = (ms.rsi || 0).toFixed(1);

        // Draw RSI gauge
        drawRSIGauge(ms.rsi || 50);

        // Market summary
        const summaryEl = document.getElementById('rec-summary-text');
        if (summaryEl && data.market_summary && data.market_summary.summaries) {
            summaryEl.innerHTML = data.market_summary.summaries.map(s =>
                '<div style="padding:6px 0;font-size:14px;line-height:1.6;">' + escHtml(s) + '</div>'
            ).join('');
        }

        // Strategy recommendations
        const strategiesEl = document.getElementById('rec-strategies');
        if (strategiesEl && data.recommendations) {
            strategiesEl.innerHTML = data.recommendations.map((r, i) => {
                const scoreColor = r.score >= 8 ? 'var(--green)' : r.score >= 6 ? 'var(--blue)' : 'var(--orange)';
                const rankIcon = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : (i + 1);
                return `
                <div style="display:flex;align-items:center;gap:12px;padding:14px 12px;border-bottom:1px solid var(--border);transition:background 0.2s;"
                     onmouseover="this.style.background='rgba(255,255,255,0.03)'" onmouseout="this.style.background=''">
                    <div style="font-size:24px;min-width:36px;text-align:center;">${rankIcon}</div>
                    <div style="flex:1;min-width:0;">
                        <div style="font-weight:600;font-size:15px;margin-bottom:4px;">${escHtml(r.name_cn)}</div>
                        <div style="font-size:12px;color:var(--text-muted);line-height:1.5;">${escHtml(r.advice)}</div>
                        <div style="font-size:13px;color:${scoreColor};margin-top:4px;">
                            评分: <strong>${r.score}</strong>/10 ${r.star_rating}
                        </div>
                    </div>
                    <div style="display:flex;flex-direction:column;gap:6px;flex-shrink:0;">
                        <div style="width:60px;height:6px;background:rgba(255,255,255,0.1);border-radius:3px;overflow:hidden;">
                            <div style="height:100%;background:${scoreColor};border-radius:3px;width:${r.score * 10}%;transition:width 0.8s ease;"></div>
                        </div>
                        <button class="btn-sm-card primary" onclick="useRecommendedStrategy('${r.strategy}', '${escHtml(r.name_cn)}')" style="font-size:12px;white-space:nowrap;">
                            🚀 使用此策略
                        </button>
                    </div>
                </div>`;
            }).join('');
        }
    } catch (e) {
        if (loading) loading.style.display = 'none';
        if (content) content.style.display = 'block';
        showToast('加载推荐失败: ' + friendlyError(e.message), 'error');
    }
}

function drawRSIGauge(rsi) {
    const canvas = document.getElementById('rec-rsi-gauge');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    const cx = w / 2;
    const cy = h / 2;
    const radius = 30;
    const lineWidth = 8;

    ctx.clearRect(0, 0, w, h);

    // Background arc
    ctx.beginPath();
    ctx.arc(cx, cy, radius, Math.PI, 0);
    ctx.strokeStyle = 'rgba(255,255,255,0.1)';
    ctx.lineWidth = lineWidth;
    ctx.stroke();

    // Value arc (0-100 RSI maps to PI to 0)
    const angle = Math.PI + (Math.PI * Math.min(100, Math.max(0, rsi)) / 100);

    // Color gradient
    let color;
    if (rsi > 70) color = '#ef5350';
    else if (rsi < 30) color = '#66bb6a';
    else color = '#42a5f5';

    ctx.beginPath();
    ctx.arc(cx, cy, radius, Math.PI, angle);
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.stroke();

    // Rounded cap
    const capX = cx + radius * Math.cos(angle);
    const capY = cy + radius * Math.sin(angle);
    ctx.beginPath();
    ctx.arc(capX, capY, lineWidth / 2, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
}

function useRecommendedStrategy(strategyKey, strategyName) {
    // Switch to strategy page and pre-select this strategy
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const strategyNav = document.querySelector('[data-page="strategy"]');
    if (strategyNav) strategyNav.classList.add('active');
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const strategyPage = document.getElementById('page-strategy');
    if (strategyPage) strategyPage.classList.add('active');

    // Trigger strategy creation modal with pre-selected strategy
    setTimeout(() => {
        showStrategyModal();
        const sel = document.getElementById('modal-strategy');
        if (sel) sel.value = strategyKey;
    }, 200);

    showToast('已选择策略: ' + strategyName, 'info');
}
