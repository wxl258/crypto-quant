/**
 * Main Application - Navigation & Core Logic
 */
'use strict';

// ═══════════════════════════════════════════
//  Configuration
// ═══════════════════════════════════════════
const CONFIG = {
    WS_RECONNECT_MIN: 3000,
    WS_RECONNECT_MAX: 30000,
    WS_PING_INTERVAL: 25000,
    CACHE_TTL: 5000,
    DASHBOARD_REFRESH_MS: 60000,
    TOAST_DURATION: 3000,
    DEFAULT_CAPITAL: 10000,
    DEFAULT_DAYS: 90,
};

// ═══════════════════════════════════════════
//  CQ Namespace
// ═══════════════════════════════════════════
const CQ = {
    _wsPaused: false,
    _wsAccount: null,
    _wsMarket: null,
    _wsReconnectTimer: null,
    _wsPingInterval: null,
    _wsReconnectDelay: CONFIG.WS_RECONNECT_MIN,
    _wsStatusEl: null,
    _notifications: [],
    _currentPage: 'dashboard',
};

// ═══════════════════════════════════════════
//  Utility Functions
// ═══════════════════════════════════════════

function debounce(fn, delay) {
    let timer = null;
    return function (...args) {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

function throttle(fn, delay) {
    let last = 0;
    let timer = null;
    return function (...args) {
        const now = Date.now();
        const remaining = delay - (now - last);
        if (remaining <= 0) {
            if (timer) { clearTimeout(timer); timer = null; }
            last = now;
            fn.apply(this, args);
        } else if (!timer) {
            timer = setTimeout(() => {
                last = Date.now();
                timer = null;
                fn.apply(this, args);
            }, remaining);
        }
    };
}

function setLoading(el, loading) {
    if (!el) return;
    if (typeof el === 'string') el = document.getElementById(el) || document.querySelector(el);
    if (!el) return;
    if (el.tagName === 'BUTTON') {
        el.disabled = loading;
        if (loading) {
            el._prevText = el.textContent;
            el.textContent = '⏳ 加载中...';
        } else if (el._prevText !== undefined) {
            el.textContent = el._prevText;
            delete el._prevText;
        }
    }
}

// ── API response cache ──
const apiCache = {};
function cachedGet(url, ttl = CONFIG.CACHE_TTL) {
    const now = Date.now();
    if (apiCache[url] && (now - apiCache[url].time) < ttl) {
        return Promise.resolve(apiCache[url].data);
    }
    return API.get(url).then(data => {
        apiCache[url] = { data, time: now };
        return data;
    });
}

// ═══════════════════════════════════════════
//  API
// ═══════════════════════════════════════════

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

// Add DELETE method support to API
API.delete = async function(url) {
    const res = await fetch(url, { method: 'DELETE' });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(friendlyError(err.detail || `HTTP ${res.status}`));
    }
    return res.json();
};

// ═══════════════════════════════════════════
//  Lazy Module Loading
// ═══════════════════════════════════════════

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

// ═══════════════════════════════════════════
//  WebSocket Management
// ═══════════════════════════════════════════

function getWSURL(path) {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${location.host}${path}`;
}

function updateWSStatus(status, text) {
    const el = CQ._wsStatusEl;
    if (!el) return;
    const colors = {
        connected: 'var(--green)',
        connecting: 'var(--orange)',
        disconnected: 'var(--red)',
    };
    const labels = {
        connected: '在线',
        connecting: '连接中',
        disconnected: '离线',
    };
    el.innerHTML = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${colors[status] || colors.disconnected};margin-right:6px;"></span>${text || labels[status] || status}`;
    el.title = `WebSocket 状态: ${text || labels[status] || status}`;
}

function initWSStatusEl() {
    // Create connection status indicator in sidebar footer
    const footer = document.querySelector('.sidebar-footer');
    if (footer && !document.getElementById('ws-status')) {
        const statusEl = document.createElement('div');
        statusEl.id = 'ws-status';
        statusEl.style.cssText = 'padding:8px 12px;font-size:12px;color:var(--text-muted);border-top:1px solid var(--border);';
        footer.appendChild(statusEl);
        CQ._wsStatusEl = statusEl;
        updateWSStatus('disconnected');
    }
}

function connectWebSocket() {
    // Clean up existing sockets before creating new ones
    if (CQ._wsAccount) {
        CQ._wsAccount.onclose = null;
        CQ._wsAccount.close();
        CQ._wsAccount = null;
    }
    if (CQ._wsMarket) {
        CQ._wsMarket.onclose = null;
        CQ._wsMarket.close();
        CQ._wsMarket = null;
    }
    // Clear existing ping interval
    if (CQ._wsPingInterval) {
        clearInterval(CQ._wsPingInterval);
        CQ._wsPingInterval = null;
    }

    updateWSStatus('connecting');

    // Account channel
    CQ._wsAccount = new WebSocket(getWSURL('/api/ws/account'));
    CQ._wsAccount.onopen = () => {
        // Reset backoff on successful connection
        CQ._wsReconnectDelay = CONFIG.WS_RECONNECT_MIN;
        updateWSStatus('connected');
    };
    CQ._wsAccount.onmessage = throttle((e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'account') {
                updateDashboardFromWS(msg.data);
                // Collect notifications from account updates
                collectNotificationsFromWS(msg.data);
            }
            if (msg.type === 'notification') {
                addNotification(msg.data);
            }
        } catch (err) { /* malformed message, skip */ }
    }, 200);
    CQ._wsAccount.onclose = () => {
        updateWSStatus('disconnected');
        scheduleWSReconnect();
    };

    // Market channel
    CQ._wsMarket = new WebSocket(getWSURL('/api/ws/market'));
    CQ._wsMarket.onmessage = throttle((e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'ticker') {
                updateTickerFromWS(msg);
            }
        } catch (err) { /* malformed message, skip */ }
    }, 200);
    CQ._wsMarket.onclose = () => scheduleWSReconnect();

    // Keepalive ping
    CQ._wsPingInterval = setInterval(() => {
        if (CQ._wsAccount && CQ._wsAccount.readyState === WebSocket.OPEN) CQ._wsAccount.send('ping');
        if (CQ._wsMarket && CQ._wsMarket.readyState === WebSocket.OPEN) CQ._wsMarket.send('ping');
    }, CONFIG.WS_PING_INTERVAL);
}

function scheduleWSReconnect() {
    if (CQ._wsReconnectTimer) return;
    CQ._wsReconnectTimer = setTimeout(() => {
        CQ._wsReconnectTimer = null;
        connectWebSocket();
    }, CQ._wsReconnectDelay);
    // Exponential backoff: 3s → 6s → 12s → 24s → 30s (capped)
    CQ._wsReconnectDelay = Math.min(CQ._wsReconnectDelay * 2, CONFIG.WS_RECONNECT_MAX);
}

// ── WebSocket pause/resume ──
function pauseWebSockets() {
    if (CQ._wsPaused) return;
    CQ._wsPaused = true;
    if (CQ._wsAccount) { CQ._wsAccount.onmessage = null; CQ._wsAccount.onclose = null; }
    if (CQ._wsMarket) { CQ._wsMarket.onmessage = null; CQ._wsMarket.onclose = null; }
}

function resumeWebSockets() {
    if (!CQ._wsPaused) return;
    CQ._wsPaused = false;
    if (CQ._wsAccount) {
        CQ._wsAccount.onmessage = throttle((e) => {
            try {
                const msg = JSON.parse(e.data);
                if (msg.type === 'account') {
                    updateDashboardFromWS(msg.data);
                    collectNotificationsFromWS(msg.data);
                }
                if (msg.type === 'notification') {
                    addNotification(msg.data);
                }
            } catch (err) { /* skip */ }
        }, 200);
        CQ._wsAccount.onclose = () => scheduleWSReconnect();
    }
    if (CQ._wsMarket) {
        CQ._wsMarket.onmessage = throttle((e) => {
            try {
                const msg = JSON.parse(e.data);
                if (msg.type === 'ticker') updateTickerFromWS(msg);
            } catch (err) { /* skip */ }
        }, 200);
        CQ._wsMarket.onclose = () => scheduleWSReconnect();
    }
}

// ═══════════════════════════════════════════
//  Navigation
// ═══════════════════════════════════════════

document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
        e.preventDefault();
        const page = item.dataset.page;
        const prevPage = CQ._currentPage;
        CQ._currentPage = page;
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        item.classList.add('active');

        // Determine transition direction based on nav order
        const navOrder = ['dashboard', 'strategy', 'strategy-store', 'backtest', 'lab', 'trades', 'risk', 'backup', 'alerts', 'recommend'];
        const prevIdx = navOrder.indexOf(prevPage);
        const currIdx = navOrder.indexOf(page);
        const direction = currIdx > prevIdx ? 'slide-left' : (currIdx < prevIdx ? 'slide-right' : 'fade');

        // Apply transition to all pages
        document.querySelectorAll('.page').forEach(p => {
            if (p.classList.contains('active')) {
                p.style.opacity = '0';
                p.style.transform = direction === 'slide-left' ? 'translateX(-30px)' : direction === 'slide-right' ? 'translateX(30px)' : 'translateY(8px)';
                setTimeout(() => {
                    p.classList.remove('active');
                    p.style.opacity = '';
                    p.style.transform = '';
                }, 250);
            }
        });

        setTimeout(() => {
            const pageEl = document.getElementById(`page-${page}`);
            if (pageEl) {
                pageEl.classList.add('active');
                // Trigger entrance animation
                pageEl.style.opacity = '0';
                pageEl.style.transform = direction === 'slide-left' ? 'translateX(30px)' : direction === 'slide-right' ? 'translateX(-30px)' : 'translateY(8px)';
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        pageEl.style.opacity = '1';
                        pageEl.style.transform = 'translateY(0) translateX(0)';
                    });
                });
            }
        }, 260);

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
            if (page === 'exchange') refreshExchangeConfig();
            if (page === 'alerts') refreshAlerts();
            if (page === 'recommend') loadRecommend();
        }
    });
});

// ═══════════════════════════════════════════
//  Format Helpers
// ═══════════════════════════════════════════

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

// Loading helper
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

// ═══════════════════════════════════════════
//  WebSocket Data Handlers
// ═══════════════════════════════════════════

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

// ═══════════════════════════════════════════
//  Toast Notification System
// ═══════════════════════════════════════════

function showToast(message, type = 'info') {
    // Create container if not exists
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    // Trigger animation
    requestAnimationFrame(() => toast.classList.add('show'));

    // Auto-dismiss
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, CONFIG.TOAST_DURATION);
}

// ═══════════════════════════════════════════
//  Sidebar
// ═══════════════════════════════════════════

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

// ═══════════════════════════════════════════
//  Initialization
// ═══════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    initWSStatusEl();
    connectWebSocket();
    // Dashboard is the default page — eager-load its module
    loadModule('dashboard', '/static/js/dashboard.js').then(() => {
        refreshDashboard();
        loadSignals();
    });
    loadCurrentMode();
    checkQuickStart();
});

// Fallback: still refresh periodically as backup
setInterval(() => {
    const dash = document.getElementById('page-dashboard');
    if (dash && dash.classList.contains('active')) {
        refreshDashboard();
        loadSignals();
    }
}, CONFIG.DASHBOARD_REFRESH_MS);

// ═══════════════════════════════════════════
//  Mode Switching
// ═══════════════════════════════════════════

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

// ═══════════════════════════════════════════
//  一键开箱 (Quick Start)
// ═══════════════════════════════════════════

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
    setLoading(btn, true);
    try {
        const result = await API.post('/api/quick-start', {});
        showToast(result.message, 'success');
        const area = document.getElementById('quick-start-area');
        if (area) area.style.display = 'none';
        setTimeout(() => refreshDashboard(), 2000);
    } catch(e) {
        showToast('启动失败: ' + friendlyError(e.message), 'error');
        setLoading(btn, false);
        btn.textContent = '🚀 一键启动（模拟盘）';
    }
}

// ═══════════════════════════════════════════
//  Strategy Lab
// ═══════════════════════════════════════════

function initLabPage() {
    // No heavy init needed - just ensure the panel is reset
    const panel = document.getElementById('lab-results-panel');
    if (panel) panel.style.display = 'none';
}

async function runLabTest() {
    const strategy = document.getElementById('lab-strategy').value;
    const symbol = document.getElementById('lab-symbol').value;
    const interval = '1h';
    const capital = CONFIG.DEFAULT_CAPITAL;
    const days = CONFIG.DEFAULT_DAYS;

    const btn = document.querySelector('#page-lab .btn-primary');
    setLoading(btn, true);

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
        setLoading(btn, false);
        btn.textContent = '🚀 开始测试';
    }
}

// ═══════════════════════════════════════════
//  Double-click dashboard title to refresh
// ═══════════════════════════════════════════

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

// ═══════════════════════════════════════════
//  Swipe left on position rows (mobile)
// ═══════════════════════════════════════════

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

// ═══════════════════════════════════════════
//  Theme Toggle (Dark/Light)
// ═══════════════════════════════════════════

function getPreferredTheme() {
    const stored = localStorage.getItem('theme');
    if (stored === 'light' || stored === 'dark') return stored;
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('theme-toggle-btn');
    if (btn) {
        btn.textContent = theme === 'light' ? '☀️' : '🌙';
        btn.title = theme === 'light' ? '切换到深色模式' : '切换到浅色模式';
    }
    localStorage.setItem('theme', theme);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || getPreferredTheme();
    const next = current === 'light' ? 'dark' : 'light';
    applyTheme(next);
}

// Initialize theme on load
document.addEventListener('DOMContentLoaded', () => {
    applyTheme(getPreferredTheme());
});

// Listen for system theme changes (only when no user preference is set)
window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', (e) => {
    if (!localStorage.getItem('theme')) {
        applyTheme(e.matches ? 'light' : 'dark');
    }
});

// ═══════════════════════════════════════════
//  Keyboard Shortcuts
// ═══════════════════════════════════════════

document.addEventListener('keydown', (e) => {
    // Ignore when focused on input/textarea/select
    const tag = document.activeElement?.tagName?.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || document.activeElement?.isContentEditable) return;

    const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
    const modKey = isMac ? e.metaKey : e.ctrlKey;

    // Ctrl/Cmd + K — Focus strategy search
    if (modKey && e.key === 'k') {
        e.preventDefault();
        const searchInput = document.getElementById('strategy-search');
        if (searchInput) {
            // Switch to strategy page first
            const strategyNav = document.querySelector('[data-page="strategy"]');
            if (strategyNav) strategyNav.click();
            setTimeout(() => searchInput.focus(), 300);
        }
    }

    // Ctrl/Cmd + D — Switch to Dashboard
    if (modKey && e.key === 'd') {
        e.preventDefault();
        const nav = document.querySelector('[data-page="dashboard"]');
        if (nav) nav.click();
    }

    // Ctrl/Cmd + B — Switch to Backtest
    if (modKey && e.key === 'b') {
        e.preventDefault();
        const nav = document.querySelector('[data-page="backtest"]');
        if (nav) nav.click();
    }

    // Escape — Close any open modal
    if (e.key === 'Escape') {
        const modals = document.querySelectorAll('.modal[style*="flex"]');
        const overlay = document.querySelector('.signal-detail-overlay');
        const notif = document.getElementById('notification-dropdown');
        const ctxMenu = document.querySelector('.context-menu');
        if (ctxMenu) ctxMenu.remove();
        if (notif && notif.style.display !== 'none') notif.style.display = 'none';
        if (overlay) overlay.remove();
        modals.forEach(m => {
            if (m.id === 'strategy-modal') closeStrategyModal();
            if (m.id === 'download-modal') closeDownloadModal();
            if (m.id === 'source-modal') closeSourceModal();
            if (m.id === 'template-modal') closeTemplateModal();
        });
    }

    // 1-9 — Quick switch to navigation pages
    if (e.key >= '1' && e.key <= '9') {
        const idx = parseInt(e.key) - 1;
        const navItems = document.querySelectorAll('.nav-item:not([style*="display:none"])');
        if (navItems[idx]) {
            e.preventDefault();
            navItems[idx].click();
        }
    }
});

// ═══════════════════════════════════════════
//  Notification Center
// ═══════════════════════════════════════════

function collectNotificationsFromWS(data) {
    if (!data) return;
    // Check for trades that were just executed
    if (data.last_trade) {
        const t = data.last_trade;
        const pnl = t.pnl || 0;
        const icon = t.side === 'CLOSE' ? (pnl >= 0 ? '✅' : '❌') : '📈';
        const title = t.side === 'CLOSE'
            ? `交易成交: ${t.symbol} (${pnl >= 0 ? '盈利' : '亏损'})`
            : `新开仓: ${t.symbol} ${t.side}`;
        const detail = t.side === 'CLOSE'
            ? `${t.symbol} ${t.side} | 盈亏: ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} USDT`
            : `${t.symbol} ${t.side} @ ${t.entry_price || '--'}`;
        addNotification({ icon, title, detail, timestamp: new Date().toISOString() });
    }
}

function addNotification(data) {
    if (!data || !data.title) return;
    CQ._notifications.unshift({
        icon: data.icon || '🔔',
        title: data.title,
        detail: data.detail || '',
        timestamp: data.timestamp || new Date().toISOString(),
        read: false,
    });
    // Keep only last 50
    if (CQ._notifications.length > 50) CQ._notifications.length = 50;
    updateNotificationBadge();
    updateNotificationList();
}

function updateNotificationBadge() {
    const badge = document.getElementById('notification-badge');
    if (!badge) return;
    const unread = CQ._notifications.filter(n => !n.read).length;
    if (unread > 0) {
        badge.style.display = 'inline-block';
        badge.textContent = unread > 99 ? '99+' : unread;
    } else {
        badge.style.display = 'none';
    }
}

function updateNotificationList() {
    const list = document.getElementById('notification-list');
    if (!list) return;
    const recent = CQ._notifications.slice(0, 5);
    if (recent.length === 0) {
        list.innerHTML = '<div class="notification-empty">暂无通知</div>';
        return;
    }
    list.innerHTML = recent.map((n, i) => `
        <div class="notification-item ${n.read ? '' : 'unread'}" onclick="markNotificationRead(${i})">
            <span class="notification-item-icon">${escHtml(n.icon)}</span>
            <div class="notification-item-body">
                <div class="notification-item-title">${escHtml(n.title)}</div>
                ${n.detail ? `<div class="notification-item-detail">${escHtml(n.detail)}</div>` : ''}
                <div class="notification-item-time">${fmtTime(n.timestamp)}</div>
            </div>
        </div>
    `).join('');
}

function markNotificationRead(index) {
    if (CQ._notifications[index]) CQ._notifications[index].read = true;
    updateNotificationBadge();
    updateNotificationList();
}

function clearNotifications() {
    CQ._notifications = [];
    updateNotificationBadge();
    updateNotificationList();
}

function toggleNotificationCenter() {
    const dropdown = document.getElementById('notification-dropdown');
    if (!dropdown) return;
    const isVisible = dropdown.style.display !== 'none';
    dropdown.style.display = isVisible ? 'none' : 'block';
    // Close context menu if open
    const ctxMenu = document.querySelector('.context-menu');
    if (ctxMenu) ctxMenu.remove();
    // Mark all as read when opening
    if (!isVisible) {
        CQ._notifications.forEach(n => n.read = true);
        updateNotificationBadge();
        updateNotificationList();
    }
}

// Close notification dropdown when clicking outside
document.addEventListener('click', (e) => {
    const dropdown = document.getElementById('notification-dropdown');
    const btn = document.getElementById('notification-btn');
    if (dropdown && dropdown.style.display !== 'none' &&
        !dropdown.contains(e.target) && !btn?.contains(e.target)) {
        dropdown.style.display = 'none';
    }
});

// ═══════════════════════════════════════════
//  Empty State Enhancements
// ═══════════════════════════════════════════

// Enhance positions empty state
function renderEmptyPositions() {
    const tbody = document.querySelector('#positions-table tbody');
    if (!tbody) return;
    tbody.textContent = '';
    const tr = document.createElement('tr');
    tr.innerHTML = `
        <td colspan="8">
            <div class="empty-state">
                <div class="empty-state-icon">📭</div>
                <div class="empty-state-title">暂无持仓</div>
                <div class="empty-state-desc">前往策略管理页面，选择一个策略开始交易</div>
                <button class="empty-state-btn" onclick="document.querySelector('[data-page=\\'strategy\\']').click()">
                    ⚙️ 去创建策略
                </button>
            </div>
        </td>`;
    tbody.appendChild(tr);
}

// Enhance signals empty state
function renderEmptySignals() {
    const grid = document.getElementById('signals-grid');
    if (!grid) return;
    grid.innerHTML = `
        <div style="grid-column:1/-1;">
            <div class="empty-state">
                <div class="empty-state-icon">📡</div>
                <div class="empty-state-title">暂无交易信号</div>
                <div class="empty-state-desc">策略正在监控市场中，信号将在条件满足时自动触发</div>
            </div>
        </div>`;
}

// Enhance backtest empty state
function renderEmptyBacktest() {
    const tbody = document.querySelector('#bt-trades-table tbody');
    if (!tbody) return;
    tbody.textContent = '';
    const tr = document.createElement('tr');
    tr.innerHTML = `
        <td colspan="6">
            <div class="empty-state">
                <div class="empty-state-icon">🔬</div>
                <div class="empty-state-title">请先运行回测</div>
                <div class="empty-state-desc">配置回测参数后点击"开始回测"按钮</div>
                <button class="empty-state-btn" onclick="runBacktest()">
                    🚀 开始回测
                </button>
            </div>
        </td>`;
    tbody.appendChild(tr);
}

// Enhance strategy list empty state
function renderEmptyStrategies() {
    const grid = document.getElementById('strategies-list');
    if (!grid) return;
    grid.innerHTML = `
        <div style="grid-column:1/-1;">
            <div class="empty-state">
                <div class="empty-state-icon">📦</div>
                <div class="empty-state-title">暂无策略</div>
                <div class="empty-state-desc">您还没有创建任何策略实例，开始创建第一个吧</div>
                <button class="empty-state-btn" onclick="showStrategyModal()">
                    + 新建策略实例
                </button>
            </div>
        </div>`;
}

// ═══════════════════════════════════════════
//  Mobile Enhancements: Pull-to-Refresh
// ═══════════════════════════════════════════

let _pullStartY = 0;
let _pullCurrentY = 0;
let _pullThreshold = 80;
let _pullActive = false;
let _pullEl = null;

function initPullToRefresh() {
    const dash = document.getElementById('page-dashboard');
    if (!dash) return;

    // Create pull indicator if not exists
    if (!document.getElementById('pull-refresh-indicator')) {
        const indicator = document.createElement('div');
        indicator.id = 'pull-refresh-indicator';
        indicator.className = 'pull-to-refresh';
        indicator.innerHTML = '<span class="pull-icon">⬇️</span> 下拉刷新';
        dash.insertBefore(indicator, dash.firstChild);
        _pullEl = indicator;
    }
}

document.addEventListener('touchstart', (e) => {
    if (window.scrollY > 5) return;
    const dash = document.getElementById('page-dashboard');
    if (!dash || !dash.classList.contains('active')) return;
    _pullStartY = e.touches[0].clientY;
    _pullCurrentY = _pullStartY;
    _pullActive = true;
}, { passive: true });

document.addEventListener('touchmove', (e) => {
    if (!_pullActive) return;
    _pullCurrentY = e.touches[0].clientY;
    const delta = _pullCurrentY - _pullStartY;
    if (delta > 0 && window.scrollY <= 5) {
        if (!_pullEl) initPullToRefresh();
        if (_pullEl) {
            _pullEl.style.display = 'block';
            _pullEl.style.maxHeight = Math.min(delta, 100) + 'px';
            _pullEl.classList.toggle('ready', delta > _pullThreshold);
        }
    }
}, { passive: true });

document.addEventListener('touchend', () => {
    if (!_pullActive) return;
    _pullActive = false;
    const delta = _pullCurrentY - _pullStartY;
    if (delta > _pullThreshold && _pullEl) {
        _pullEl.querySelector('.pull-icon').textContent = '🔄';
        _pullEl.classList.add('visible');
        // Refresh dashboard
        refreshDashboard().then(() => {
            if (_pullEl) {
                _pullEl.style.display = 'none';
                _pullEl.style.maxHeight = '0';
                _pullEl.classList.remove('visible', 'ready');
                _pullEl.querySelector('.pull-icon').textContent = '⬇️';
                showToast('数据已刷新', 'info');
            }
        });
    } else if (_pullEl) {
        _pullEl.style.maxHeight = '0';
        _pullEl.classList.remove('visible', 'ready');
    }
});

// Initialize pull-to-refresh on load
document.addEventListener('DOMContentLoaded', initPullToRefresh);

// ═══════════════════════════════════════════
//  Mobile Enhancements: Long-Press Context Menu
// ═══════════════════════════════════════════

let _longPressTimer = null;
let _longPressTarget = null;

document.addEventListener('touchstart', (e) => {
    const row = e.target.closest('#positions-table tbody tr');
    if (!row || row.classList.contains('empty-row')) return;
    _longPressTarget = row;
    _longPressTimer = setTimeout(() => {
        showContextMenu(row, e.touches[0].clientX, e.touches[0].clientY);
    }, 500);
}, { passive: true });

document.addEventListener('touchmove', () => {
    if (_longPressTimer) {
        clearTimeout(_longPressTimer);
        _longPressTimer = null;
        _longPressTarget = null;
    }
}, { passive: true });

document.addEventListener('touchend', () => {
    if (_longPressTimer) {
        clearTimeout(_longPressTimer);
        _longPressTimer = null;
    }
});

function showContextMenu(row, x, y) {
    // Remove existing menu
    const existing = document.querySelector('.context-menu');
    if (existing) existing.remove();

    const symbolCell = row.querySelector('td:first-child');
    const symbol = symbolCell ? symbolCell.textContent.trim() : '';

    const menu = document.createElement('div');
    menu.className = 'context-menu';
    menu.innerHTML = `
        <button class="context-menu-item danger" data-action="close">📉 平仓 ${escHtml(symbol)}</button>
        <button class="context-menu-item" data-action="detail">📋 查看详情</button>
    `;
    menu.style.left = Math.min(x, window.innerWidth - 160) + 'px';
    menu.style.top = Math.min(y, window.innerHeight - 120) + 'px';
    document.body.appendChild(menu);

    menu.addEventListener('click', (e) => {
        const action = e.target.closest('.context-menu-item')?.dataset?.action;
        if (action === 'close') {
            if (confirm(`确认平仓 ${symbol}？`)) {
                closePosition(symbol);
            }
        } else if (action === 'detail') {
            showToast(`${symbol} 持仓详情`, 'info');
        }
        menu.remove();
    });

    // Close on outside click
    const closeMenu = (e) => {
        if (!menu.contains(e.target)) {
            menu.remove();
            document.removeEventListener('click', closeMenu);
            document.removeEventListener('touchstart', closeMenu);
        }
    };
    setTimeout(() => {
        document.addEventListener('click', closeMenu);
        document.addEventListener('touchstart', closeMenu);
    }, 100);
}

// ═══════════════════════════════════════════
//  Signal Loading and Display
// ═══════════════════════════════════════════

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
            renderEmptySignals();
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

// ═══════════════════════════════════════════
//  Safety Shield
// ═══════════════════════════════════════════

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

// ═══════════════════════════════════════════
//  Backup & Restore
// ═══════════════════════════════════════════

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
        setLoading(btn, true);
        const result = await API.post('/api/backup/create', {});
        showToast(result.message, 'success');
        await refreshBackups();
    } catch (e) {
        showToast('创建备份失败: ' + friendlyError(e.message), 'error');
    } finally {
        const btn = document.querySelector('#page-backup .btn-primary');
        setLoading(btn, false);
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

// ═══════════════════════════════════════════
//  Custom Alerts
// ═══════════════════════════════════════════

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

// ═══════════════════════════════════════════
//  Trade Notes
// ═══════════════════════════════════════════

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

// ═══════════════════════════════════════════
//  AI Strategy Recommend
// ═══════════════════════════════════════════

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

// ═══════════════════════════════════════════
//  Global Error Boundary
// ═══════════════════════════════════════════

window.addEventListener('error', (e) => {
    if (e.error) {
        console.error('[CQ] Unhandled error:', e.error.message, e.error.stack);
    } else {
        console.error('[CQ] Resource error:', e.target?.src || e.target?.href || e.message);
    }
});

window.addEventListener('unhandledrejection', (e) => {
    console.error('[CQ] Unhandled promise rejection:', e.reason);
    e.preventDefault(); // Prevent default console error in some browsers
});

// ═══════════════════════════════════════════
//  Online/Offline Detection for WebSocket
// ═══════════════════════════════════════════

window.addEventListener('online', () => {
    updateWSStatus('connecting', '网络恢复');
    connectWebSocket();
});

window.addEventListener('offline', () => {
    updateWSStatus('disconnected', '网络断开');
    if (CQ._wsAccount) {
        CQ._wsAccount.onclose = null;
        CQ._wsAccount.close();
        CQ._wsAccount = null;
    }
    if (CQ._wsMarket) {
        CQ._wsMarket.onclose = null;
        CQ._wsMarket.close();
        CQ._wsMarket = null;
    }
    if (CQ._wsPingInterval) {
        clearInterval(CQ._wsPingInterval);
        CQ._wsPingInterval = null;
    }
    if (CQ._wsReconnectTimer) {
        clearTimeout(CQ._wsReconnectTimer);
        CQ._wsReconnectTimer = null;
    }
});

// ── 交易所设置 ──

function onExSetExchangeChange() {
    const ex = document.getElementById('ex-set-exchange').value;
    const pwGroup = document.getElementById('ex-set-password-group');
    if (pwGroup) pwGroup.style.display = ex === 'okx' ? 'block' : 'none';
}

async function refreshExchangeConfig() {
    try {
        const [status, config] = await Promise.all([
            API.get('/api/exchange/status'),
            API.get('/api/exchange/config'),
        ]);
        const el = document.getElementById('exchange-status');
        if (!el) return;

        const active = status.active;
        const binOk = status.binance?.configured;
        const okxOk = status.okx?.configured;

        el.innerHTML = `
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;">
                <div class="metric-card">
                    <div class="metric-label">活跃交易所</div>
                    <div class="metric-value" style="font-size:18px;">${active === 'okx' ? '欧易 OKX' : '币安 Binance'}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">币安状态</div>
                    <div class="metric-value" style="font-size:18px;color:${binOk ? 'var(--green)' : 'var(--text-muted)'};">${binOk ? '✅ 已配置' : '⬜ 未配置'}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">欧易状态</div>
                    <div class="metric-value" style="font-size:18px;color:${okxOk ? 'var(--green)' : 'var(--text-muted)'};">${okxOk ? '✅ 已配置' : '⬜ 未配置'}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">环境</div>
                    <div class="metric-value" style="font-size:18px;color:${config.testnet ? 'var(--orange)' : 'var(--red)'};">${config.testnet ? '模拟盘' : '实盘'}</div>
                </div>
            </div>
            ${config.configured ? `
            <div style="margin-top:12px;padding:12px;background:rgba(0,0,0,0.2);border-radius:8px;font-size:12px;color:var(--text-muted);">
                <div>API Key: ${escHtml(config.api_key)}</div>
                <div>API Secret: ${escHtml(config.api_secret)}</div>
                ${config.password ? '<div>API 密码: ' + escHtml(config.password) + '</div>' : ''}
            </div>` : ''}
        `;
    } catch (e) {
        console.error('加载交易所配置失败:', e);
    }
}

async function testExConnection() {
    const exchange = document.getElementById('ex-set-exchange').value;
    const apiKey = document.getElementById('ex-set-apikey').value.trim();
    const apiSecret = document.getElementById('ex-set-secret').value.trim();
    const password = document.getElementById('ex-set-password').value.trim();
    const testnet = document.getElementById('ex-set-testnet').checked;
    const resultEl = document.getElementById('ex-test-result');

    if (!apiKey || !apiSecret) {
        resultEl.textContent = '❌ 请填写 API Key 和 Secret';
        resultEl.style.color = 'var(--red)';
        return;
    }

    if (exchange === 'okx' && !password) {
        resultEl.textContent = '❌ OKX 需要填写 API 密码 (Passphrase)';
        resultEl.style.color = 'var(--red)';
        return;
    }

    resultEl.textContent = '⏳ 测试中...';
    resultEl.style.color = 'var(--text-muted)';

    try {
        const res = await fetch('/api/exchange/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                exchange_id: exchange,
                api_key: apiKey,
                api_secret: apiSecret,
                password: password || '',
                testnet: testnet,
            }),
        });
        const data = await res.json();

        if (res.ok && data.success) {
            resultEl.textContent = '✅ 连接成功！BTC价格: ' + (data.btc_price || '--');
            resultEl.style.color = 'var(--green)';
        } else {
            resultEl.textContent = '❌ ' + (data.detail || '连接失败');
            resultEl.style.color = 'var(--red)';
        }
    } catch (e) {
        resultEl.textContent = '❌ 网络错误: ' + e.message;
        resultEl.style.color = 'var(--red)';
    }
}

async function saveExKeys() {
    const exchange = document.getElementById('ex-set-exchange').value;
    const apiKey = document.getElementById('ex-set-apikey').value.trim();
    const apiSecret = document.getElementById('ex-set-secret').value.trim();
    const password = document.getElementById('ex-set-password').value.trim();
    const testnet = document.getElementById('ex-set-testnet').checked;

    if (!apiKey || !apiSecret) {
        showToast('请填写 API Key 和 Secret', 'warning');
        return;
    }

    if (exchange === 'okx' && !password) {
        showToast('OKX 需要填写 API 密码', 'warning');
        return;
    }

    try {
        const data = await API.post('/api/exchange/keys', {
            exchange_id: exchange,
            api_key: apiKey,
            api_secret: apiSecret,
            password: password || '',
            testnet: testnet,
        });

        // 同时切换到该交易所
        await API.post('/api/exchange/switch', { exchange_id: exchange });

        showToast('✅ ' + data.message, 'success');
        refreshExchangeConfig();
        loadCurrentMode();
    } catch (e) {
        showToast('保存失败: ' + friendlyError(e.message), 'error');
    }
}
