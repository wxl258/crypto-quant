/**
 * Main Application - Navigation & Core Logic
 *
 * Defensive programming rewrite:
 *   - safeGet() wraps all DOM access (el?.property, if (!el) return)
 *   - All API calls use try/catch with toast error reporting
 *   - Lazy-loaded module functions are checked with typeof === 'function'
 *   - WebSocket connects only after HTTP API reachability confirmed
 *   - Null/undefined guards on every dereference
 *   - Preserves all original global function signatures for index.html compatibility
 */
'use strict';

/* ==========================================================================
 * SECTION 0 – Boot-time safety net
 * ========================================================================== */

if (typeof friendlyError !== 'function') {
    window.friendlyError = function(msg) {
        return '⚠️ ' + (msg || '未知错误');
    };
}
if (typeof showToast !== 'function') {
    window.showToast = function(msg, type) {
        console.log('[' + (type || 'info') + ']', msg);
    };
}

/* ==========================================================================
 * SECTION 1 – safeGet helper (defensive DOM access)
 * ========================================================================== */

/**
 * Safe DOM element getter.
 * Returns the element or null.  Every caller must handle null.
 * Also accepts a fallback element for chaining patterns.
 */
function safeGet(selectorOrId) {
    if (!selectorOrId) return null;
    // Heuristic: starts with '#' → getElementById, otherwise querySelector
    if (selectorOrId[0] === '#') {
        return document.getElementById(selectorOrId.slice(1));
    }
    return document.querySelector(selectorOrId);
}

/* ==========================================================================
 * SECTION 2 – API helper (try/catch at call sites, not here)
 * ========================================================================== */

const API = {
    async get(url) {
        const res = await fetch(url);
        if (!res.ok) {
            const body = await res.json().catch(function() { return {}; });
            throw new Error(friendlyError(body.detail || 'HTTP ' + res.status));
        }
        return res.json();
    },

    async post(url, data) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) {
            const body = await res.json().catch(function() { return {}; });
            throw new Error(friendlyError(body.detail || 'HTTP ' + res.status));
        }
        return res.json();
    },

    async delete(url) {
        const res = await fetch(url, { method: 'DELETE' });
        if (!res.ok) {
            const body = await res.json().catch(function() { return {}; });
            throw new Error(friendlyError(body.detail || 'HTTP ' + res.status));
        }
        return res.json();
    },

    /**
     * Streaming post – same as post() but with optional progress callback.
     * The onProgress arg is reserved for future use.
     */
    async postOptimize(url, data, onProgress) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) {
            const body = await res.json().catch(function() { return {}; });
            throw new Error(friendlyError(body.detail || 'HTTP ' + res.status));
        }
        return res.json();
    },
};

/* ==========================================================================
 * SECTION 3 – Lazy module loading (with callable guard)
 * ========================================================================== */

var moduleCache = {};

/**
 * Load a JS module script dynamically.
 * Returns a Promise that resolves when the script is loaded.
 */
function loadModule(name, path) {
    if (moduleCache[name]) return Promise.resolve();
    return new Promise(function(resolve, reject) {
        var script = document.createElement('script');
        script.src = path;
        script.onload = function() {
            moduleCache[name] = true;
            resolve();
        };
        script.onerror = function() {
            reject(new Error('Failed to load module: ' + path));
        };
        document.head.appendChild(script);
    });
}

/**
 * Safe lazy-load + call.
 * Loads a module, then calls fnName only if it is a function.
 * If fnName is not a function after load, logs a warning.
 */
function loadAndCall(name, path, fnName) {
    return loadModule(name, path).then(function() {
        if (typeof window[fnName] === 'function') {
            window[fnName]();
        } else {
            console.warn('Lazy-loaded module ' + name + ' did not expose ' + fnName);
        }
    }).catch(function(err) {
        console.error('Module load error (' + name + '):', err);
        showToast('模块加载失败: ' + name, 'error');
    });
}

/* ==========================================================================
 * SECTION 4 – API response cache (5 s TTL)
 * ========================================================================== */

var apiCache = {};

function cachedGet(url, ttl) {
    if (ttl === undefined) ttl = 5000;
    var now = Date.now();
    var entry = apiCache[url];
    if (entry && (now - entry.time) < ttl) {
        return Promise.resolve(entry.data);
    }
    return API.get(url).then(function(data) {
        apiCache[url] = { data: data, time: now };
        return data;
    });
}

/* ==========================================================================
 * SECTION 5 – WebSocket state & lifecycle
 * ========================================================================== */

var _wsPaused       = false;
var _wsAccount      = null;
var _wsMarket       = null;
var _wsReconnectTimer = null;
var _wsPingInterval = null;
var _wsReconnectDelay = 3000;

var _WS_MIN_DELAY = 3000;
var _WS_MAX_DELAY = 30000;

function pauseWebSockets() {
    if (_wsPaused) return;
    _wsPaused = true;
    if (_wsAccount) { _wsAccount.onmessage = null; _wsAccount.onclose = null; }
    if (_wsMarket)  { _wsMarket.onmessage  = null; _wsMarket.onclose  = null; }
}

function resumeWebSockets() {
    if (!_wsPaused) return;
    _wsPaused = false;
    if (_wsAccount) {
        _wsAccount.onmessage = function(e) {
            try {
                var msg = JSON.parse(e.data);
                if (msg && msg.type === 'account') {
                    updateDashboardFromWS(msg.data);
                }
            } catch (_) { /* skip malformed message */ }
        };
        _wsAccount.onclose = scheduleWSReconnect;
    }
    if (_wsMarket) {
        _wsMarket.onmessage = function(e) {
            try {
                var msg = JSON.parse(e.data);
                if (msg && msg.type === 'ticker') {
                    updateTickerFromWS(msg);
                }
            } catch (_) { /* skip malformed message */ }
        };
        _wsMarket.onclose = scheduleWSReconnect;
    }
}

function connectWebSocket() {
    // HTTP API health check before WebSocket
    fetch('/api/mode').then(function(r) {
        if (!r.ok) throw new Error('backend offline');
        return r.json();
    }).catch(function() {
        scheduleWSReconnect();
    }).then(function(data) {
        if (data) _doConnectWebSocket();
        // If data is falsy → backend offline, already handled in .catch()
    });
}

function _doConnectWebSocket() {
    // Tear down existing sockets
    if (_wsAccount) { _wsAccount.onclose = null; _wsAccount.close(); _wsAccount = null; }
    if (_wsMarket)  { _wsMarket.onclose  = null; _wsMarket.close();  _wsMarket  = null; }
    if (_wsPingInterval) { clearInterval(_wsPingInterval); _wsPingInterval = null; }

    var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var base = protocol + '//' + location.host;

    // ── Account channel ──
    _wsAccount = new WebSocket(base + '/api/ws/account');
    _wsAccount.onmessage = function(e) {
        try {
            var msg = JSON.parse(e.data);
            if (msg && msg.type === 'account') {
                updateDashboardFromWS(msg.data);
            }
        } catch (_) { /* skip */ }
    };
    _wsAccount.onopen = function() {
        _wsReconnectDelay = _WS_MIN_DELAY;
    };
    _wsAccount.onclose = scheduleWSReconnect;

    // ── Market channel ──
    _wsMarket = new WebSocket(base + '/api/ws/market');
    _wsMarket.onmessage = function(e) {
        try {
            var msg = JSON.parse(e.data);
            if (msg && msg.type === 'ticker') {
                updateTickerFromWS(msg);
            }
        } catch (_) { /* skip */ }
    };
    _wsMarket.onclose = scheduleWSReconnect;

    // Keepalive ping every 25 s
    _wsPingInterval = setInterval(function() {
        if (_wsAccount && _wsAccount.readyState === WebSocket.OPEN) {
            _wsAccount.send('ping');
        }
        if (_wsMarket && _wsMarket.readyState === WebSocket.OPEN) {
            _wsMarket.send('ping');
        }
    }, 25000);
}

function scheduleWSReconnect() {
    if (_wsReconnectTimer) return;
    _wsReconnectTimer = setTimeout(function() {
        _wsReconnectTimer = null;
        connectWebSocket();
    }, _wsReconnectDelay);
    _wsReconnectDelay = Math.min(_wsReconnectDelay * 2, _WS_MAX_DELAY);
}

function updateDashboardFromWS(data) {
    if (!data) return;

    var totalEquityEl      = safeGet('#total-equity');
    var availableBalanceEl = safeGet('#available-balance');
    var positionCountEl    = safeGet('#position-count');
    var totalTradesEl      = safeGet('#total-trades');
    var pnlEl              = safeGet('#total-pnl');

    if (totalEquityEl)      totalEquityEl.textContent      = fmtUSD(data.total_equity);
    if (availableBalanceEl) availableBalanceEl.textContent = fmtUSD(data.capital);
    if (positionCountEl)    positionCountEl.textContent    = data.open_positions;
    if (totalTradesEl)      totalTradesEl.textContent      = data.total_trades;

    if (pnlEl) {
        var pnl    = data.total_pnl;
        var pnlPct = data.total_pnl_pct;
        pnlEl.textContent = fmtUSD(pnl) + ' (' + fmtPct(pnlPct) + ')';
        pnlEl.className   = 'stat-change ' + ((pnl || 0) >= 0 ? 'positive' : 'negative');
    }
}

function updateTickerFromWS(msg) {
    // Reserved for real-time K-line price line update when chart is visible.
    // No-op for now – the dashboard module handles this via polling.
}

/* ==========================================================================
 * SECTION 6 – Format helpers (defensive: guard null/undefined)
 * ========================================================================== */

function fmtUSD(n) {
    if (n === undefined || n === null) return '--';
    return Number(n).toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}

function fmtPct(n) {
    if (n === undefined || n === null) return '--';
    var v = Number(n);
    var sign = v >= 0 ? '+' : '';
    return sign + v.toFixed(2) + '%';
}

function fmtTime(ts) {
    if (!ts) return '--';
    return new Date(ts).toLocaleString('zh-CN');
}

function escHtml(str) {
    if (str === undefined || str === null) return '';
    var div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

function showLoading(el, colSpan) {
    if (!el) return;
    if (colSpan === undefined) colSpan = 10;
    el.textContent = '';
    var row = document.createElement('tr');
    row.className = 'empty-row';
    var td = document.createElement('td');
    td.colSpan = colSpan;
    var spinner = document.createElement('div');
    spinner.className = 'spinner';
    td.appendChild(spinner);
    td.appendChild(document.createTextNode(' 加载中...'));
    row.appendChild(td);
    el.appendChild(row);
}

/* ==========================================================================
 * SECTION 7 – Navigation (fully defensive)
 * ========================================================================== */

(function() {
    var navItems = document.querySelectorAll('.nav-item');
    if (!navItems || navItems.length === 0) return;

    for (var i = 0; i < navItems.length; i++) {
        (function(item) {
            item.addEventListener('click', function(e) {
                e.preventDefault();
                var page = item.dataset.page;
                if (!page) return;

                // Toggle active class on all nav items
                var allNav = document.querySelectorAll('.nav-item');
                for (var j = 0; j < allNav.length; j++) {
                    allNav[j].classList.remove('active');
                }
                item.classList.add('active');

                // Toggle page visibility
                var allPages = document.querySelectorAll('.page');
                for (var k = 0; k < allPages.length; k++) {
                    allPages[k].classList.remove('active');
                }
                var pageEl = safeGet('#page-' + page);
                if (pageEl) pageEl.classList.add('active');

                // Pause / resume WebSocket per page
                if (page === 'dashboard') {
                    resumeWebSockets();
                    loadAndCall('dashboard', '/static/js/dashboard.js', 'refreshDashboard');
                } else {
                    pauseWebSockets();
                    if (page === 'strategy')       loadAndCall('strategy',       '/static/js/strategy.js',       'initStrategyPage');
                    if (page === 'strategy-store') loadAndCall('strategy_store', '/static/js/strategy_store.js', 'initStrategyStorePage');
                    if (page === 'backtest')       loadAndCall('backtest',       '/static/js/backtest.js',       'initBacktestPage');
                    if (page === 'trades')         safeCallGlobal('refreshTrades');
                    if (page === 'risk')           safeCallGlobal('refreshRisk');
                    if (page === 'lab')            safeCallGlobal('initLabPage');
                    if (page === 'backup')         safeCallGlobal('refreshBackups');
                    if (page === 'alerts')         safeCallGlobal('refreshAlerts');
                    if (page === 'recommend')      safeCallGlobal('loadRecommend');
                }
            });
        })(navItems[i]);
    }
})();

/**
 * Call a global function only if it is a function.
 */
function safeCallGlobal(fnName) {
    if (typeof window[fnName] === 'function') {
        try {
            window[fnName]();
        } catch (e) {
            console.error('Error calling ' + fnName + ':', e);
            showToast('操作失败: ' + friendlyError(e.message), 'error');
        }
    }
}

/* ==========================================================================
 * SECTION 8 – Toast notification system
 * ========================================================================== */

function showToast(message, type) {
    if (!message) return;
    if (type === undefined) type = 'info';

    var container = safeGet('#toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        document.body.appendChild(container);
    }

    var toast = document.createElement('div');
    toast.className = 'toast toast-' + type;
    toast.textContent = message;
    container.appendChild(toast);

    requestAnimationFrame(function() {
        toast.classList.add('show');
    });

    setTimeout(function() {
        toast.classList.remove('show');
        setTimeout(function() {
            if (toast && toast.parentNode) toast.remove();
        }, 300);
    }, 3000);
}

/* ==========================================================================
 * SECTION 9 – Mobile sidebar toggle
 * ========================================================================== */

function toggleSidebar() {
    var sidebar = safeGet('#sidebar');
    var btn     = safeGet('#hamburger-btn');
    if (!sidebar) return;

    sidebar.classList.toggle('sidebar-open');
    if (btn) {
        btn.textContent = sidebar.classList.contains('sidebar-open') ? '\u2715' : '\u2630';
    }
}

// Close sidebar when clicking a nav item on mobile
document.addEventListener('click', function(e) {
    var sidebar = safeGet('#sidebar');
    if (!sidebar) return;
    var target = e.target;
    if (target && target.closest && target.closest('.nav-item') && sidebar.classList.contains('sidebar-open')) {
        sidebar.classList.remove('sidebar-open');
        var btn = safeGet('#hamburger-btn');
        if (btn) btn.textContent = '\u2630';
    }
});

/* ==========================================================================
 * SECTION 10 – Initialization (DOMContentLoaded)
 * ========================================================================== */

document.addEventListener('DOMContentLoaded', function() {
    connectWebSocket();

    // Dashboard is the default page – eager-load its module
    loadModule('dashboard', '/static/js/dashboard.js').then(function() {
        safeCallGlobal('refreshDashboard');
        safeCallGlobal('loadSignals');
    }).catch(function(err) {
        console.error('Failed to load dashboard module:', err);
    });

    safeCallGlobal('loadCurrentMode');
    safeCallGlobal('checkQuickStart');
});

// Fallback periodic refresh every 60 s
setInterval(function() {
    var dash = safeGet('#page-dashboard');
    if (dash && dash.classList.contains('active')) {
        safeCallGlobal('refreshDashboard');
        safeCallGlobal('loadSignals');
    }
}, 60000);

/* ==========================================================================
 * SECTION 11 – Mode switching
 * ========================================================================== */

async function loadCurrentMode() {
    try {
        var data = await API.get('/api/mode');
        if (data && data.mode) updateModeUI(data.mode);
    } catch (e) {
        console.error('Failed to load mode:', e);
        showToast('加载模式失败: ' + friendlyError(e.message), 'error');
    }
}

function updateModeUI(mode) {
    if (!mode) return;

    var badge   = safeGet('#sidebar-mode-badge');
    var display = safeGet('#mode-display');
    var toggle  = safeGet('#mode-switch');
    var isLive  = mode === 'live';

    if (badge) {
        badge.textContent = isLive ? '\u5b9e\u76d8' : '\u6a21\u62df\u76d8'; // 实盘 / 模拟盘
        badge.style.background = isLive ? 'rgba(76,175,132,0.15)' : 'rgba(255,167,38,0.15)';
        badge.style.color      = isLive ? 'var(--green)' : 'var(--orange)';
    }
    if (display) {
        display.textContent = isLive ? '\u5b9e\u76d8' : '\u6a21\u62df\u76d8';
    }
    if (toggle) {
        toggle.checked = isLive;
    }
}

async function toggleMode() {
    var toggle  = safeGet('#mode-switch');
    if (!toggle) return;

    var newMode = toggle.checked ? 'live' : 'paper';

    try {
        var result = await API.post('/api/mode', { mode: newMode });
        if (result && result.mode) updateModeUI(result.mode);
        var label = (result && result.mode === 'live') ? '\u5b9e\u76d8' : '\u6a21\u62df\u76d8';
        showToast('\u5df2\u5207\u6362\u81f3' + label + '\u6a21\u5f0f', 'info');
    } catch (e) {
        // Revert toggle on failure
        toggle.checked = !toggle.checked;
        showToast('\u6a21\u5f0f\u5207\u6362\u5931\u8d25: ' + friendlyError(e.message), 'error');
    }
}

/* ==========================================================================
 * SECTION 12 – Quick start (一键开箱)
 * ========================================================================== */

async function checkQuickStart() {
    try {
        var status = await API.get('/api/live/status');
        if (!status) return;
        var area = safeGet('#quick-start-area');
        if (!area) return;
        if (!status.bots || Object.keys(status.bots).length === 0) {
            area.style.display = 'block';
        }
    } catch (_) {
        // Silently ignore – quick-start area stays hidden
    }
}

async function quickStart() {
    var btn = safeGet('#quick-start-btn');
    if (!btn) return;

    btn.disabled = true;
    btn.textContent = '\u23f3 \u542f\u52a8\u4e2d...'; // ⏳ 启动中...

    try {
        var result = await API.post('/api/quick-start', {});
        if (result && result.message) showToast(result.message, 'success');
        var area = safeGet('#quick-start-area');
        if (area) area.style.display = 'none';
        setTimeout(function() {
            safeCallGlobal('refreshDashboard');
        }, 2000);
    } catch (e) {
        showToast('\u542f\u52a8\u5931\u8d25: ' + friendlyError(e.message), 'error');
        btn.disabled = false;
        btn.textContent = '\ud83d\ude80 \u4e00\u952e\u542f\u52a8\uff08\u6a21\u62df\u76d8\uff09';
    }
}

/* ==========================================================================
 * SECTION 13 – Strategy Lab
 * ========================================================================== */

function initLabPage() {
    var panel = safeGet('#lab-results-panel');
    if (panel) panel.style.display = 'none';
}

async function runLabTest() {
    var strategyEl = safeGet('#lab-strategy');
    var symbolEl   = safeGet('#lab-symbol');
    var btn        = safeGet('#page-lab .btn-primary');

    if (!strategyEl || !symbolEl) {
        showToast('页面元素缺失，请刷新后重试', 'error');
        return;
    }

    var strategy = strategyEl.value;
    var symbol   = symbolEl.value;

    if (btn) {
        btn.textContent = '\u23f3 \u6d4b\u8bd5\u4e2d...'; // ⏳ 测试中...
        btn.disabled = true;
    }

    try {
        var result = await API.post('/api/backtest', {
            strategy: strategy,
            symbol: symbol,
            interval: '1h',
            initial_capital: 10000,
            days: 90,
            params: {},
        });

        var m = (result && result.metrics) ? result.metrics : null;
        if (!m) {
            showToast('回测结果为空', 'warning');
            return;
        }

        // Show results panel
        var panel = safeGet('#lab-results-panel');
        if (panel) panel.style.display = 'block';

        var retEl = safeGet('#lab-return');
        if (retEl) {
            retEl.textContent = fmtPct(m.total_return);
            retEl.style.color = m.total_return >= 0 ? 'var(--green)' : 'var(--red)';
        }

        var wrEl = safeGet('#lab-winrate');
        if (wrEl) {
            wrEl.textContent = fmtPct(m.win_rate);
            wrEl.style.color = m.win_rate >= 50 ? 'var(--green)' : 'var(--orange)';
        }

        var ddEl = safeGet('#lab-dd');
        if (ddEl) {
            ddEl.textContent = fmtPct(m.max_drawdown);
            ddEl.style.color = Math.abs(m.max_drawdown) <= 20 ? 'var(--green)' : 'var(--red)';
        }

        // One-sentence summary
        var verdict = m.total_return >= 10 ? '\u8868\u73b0\u4e0d\u9519'   // 表现不错
                    : m.total_return >=  0 ? '\u8868\u73b0\u4e00\u822c'   // 表现一般
                    : '\u8868\u73b0\u4e0d\u4f73';                        // 表现不佳

        var summaryEl = safeGet('#lab-summary');
        if (summaryEl) {
            summaryEl.innerHTML =
                '\u8fd9\u4e2a\u7b56\u7565\u5728\u8fc7\u53bb' + 90 + '\u5929' +
                '<strong style="color:' + (m.total_return >= 0 ? 'var(--green)' : 'var(--red)') + '">' + verdict + '</strong>\uff0c' +
                '\u6536\u76ca<strong>' + fmtPct(m.total_return) + '</strong>\uff0c' +
                '\u80dc\u7387<strong>' + fmtPct(m.win_rate) + '</strong>';
        }

        if (panel) panel.scrollIntoView({ behavior: 'smooth' });
    } catch (e) {
        showToast('\u6d4b\u8bd5\u5931\u8d25: ' + friendlyError(e.message), 'error');
    } finally {
        if (btn) {
            btn.textContent = '\ud83d\ude80 \u5f00\u59cb\u6d4b\u8bd5';
            btn.disabled = false;
        }
    }
}

/* ==========================================================================
 * SECTION 14 – Double-click dashboard title to refresh
 * ========================================================================== */

document.addEventListener('DOMContentLoaded', function() {
    var h2 = safeGet('#page-dashboard .page-header h2');
    if (!h2) return;

    h2.style.cursor = 'pointer';
    h2.title = '\u53cc\u51fb\u5237\u65b0\u6570\u636e'; // 双击刷新数据
    h2.addEventListener('dblclick', function() {
        safeCallGlobal('refreshDashboard');
        safeCallGlobal('loadSignals');
        showToast('\u6570\u636e\u5df2\u5237\u65b0', 'info');
    });
});

/* ==========================================================================
 * SECTION 15 – Swipe-left on position rows (mobile)
 * ========================================================================== */

var _swipeStartX = 0;
var _swipeTarget = null;

document.addEventListener('touchstart', function(e) {
    var touch = (e.touches && e.touches[0]) ? e.touches[0] : null;
    if (!touch) return;
    var row = e.target.closest('#positions-table tbody tr');
    if (row && !row.classList.contains('empty-row')) {
        _swipeStartX = touch.clientX;
        _swipeTarget = row;
    }
}, { passive: true });

document.addEventListener('touchend', function(e) {
    if (!_swipeTarget) return;
    var changedTouch = (e.changedTouches && e.changedTouches[0]) ? e.changedTouches[0] : null;
    var clientX = changedTouch ? changedTouch.clientX : _swipeStartX;
    var deltaX = clientX - _swipeStartX;

    if (deltaX < -60) {
        var symbolCell = _swipeTarget.querySelector('td:first-child');
        if (symbolCell) {
            var symbol = symbolCell.textContent.trim();
            if (symbol && confirm('\u786e\u8ba4\u5e73\u4ed3 ' + symbol + '\uff1f')) { // 确认平仓
                if (typeof closePosition === 'function') {
                    closePosition(symbol);
                }
            }
        }
    }
    _swipeTarget = null;
});

/* ==========================================================================
 * SECTION 16 – Signal loading & display
 * ========================================================================== */

async function loadSignals() {
    try {
        var data = await API.get('/api/live/status');
        if (!data) return;

        var panel = safeGet('#signals-panel');
        var grid  = safeGet('#signals-grid');
        if (!panel || !grid) return;

        // Update time
        var timeEl = safeGet('#signal-update-time');
        if (timeEl) {
            timeEl.textContent = '\u66f4\u65b0\u4e8e ' + new Date().toLocaleTimeString('zh-CN');
        }

        var bots = data.bots || {};
        var botList = Object.values(bots);

        if (botList.length === 0) {
            panel.style.display = 'block';
            grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--text-muted);padding:20px;">\u6682\u65e0\u4ea4\u6613\u4fe1\u53f7\uff0c\u7b56\u7565\u76d1\u63a7\u4e2d...</div>';
            return;
        }

        panel.style.display = 'block';
        grid.innerHTML = botList.map(function(bot) {
            var strategyKey = bot.strategy || 'unknown';
            var guide = (typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[strategyKey]) || null;
            var strategyName = guide ? guide.name : (typeof getStrategyLabel === 'function' ? getStrategyLabel(strategyKey) : strategyKey);
            var icon  = guide ? guide.icon : '\ud83d\udcc8';
            var direction = bot.side || 'NEUTRAL';
            var dirLabel = direction === 'LONG' ? '\u4e70\u5165\ud83d\udfe2'
                         : direction === 'SHORT' ? '\u5356\u51fa\ud83d\udd34'
                         : '\u89c2\u671b\ud83d\udfe1';
            var dirClass = direction === 'LONG' ? 'buy' : direction === 'SHORT' ? 'sell' : 'neutral';
            var confidence = bot.confidence || 0;
            var stars = '\u2b50'.repeat(Math.min(5, Math.round(confidence * 5)));
            var price = bot.current_price ? fmtUSD(bot.current_price) : '--';

            var guideJSON = guide ? JSON.stringify(guide).replace(/"/g, '&quot;') : 'null';
            return '<div class="signal-card" data-action="showSignalDetail" data-action-args="' + escHtml(JSON.stringify({strategyKey: strategyKey, direction: direction, guideData: guideData})) + '" style="cursor:pointer;">' +
                '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">' +
                    '<span style="font-size:20px;">' + icon + '</span>' +
                    '<div>' +
                        '<div style="font-weight:600;font-size:14px;">' + escHtml(strategyName) + '</div>' +
                        '<div style="font-size:11px;color:var(--text-muted);">' + escHtml(bot.symbol || '--') + '</div>' +
                    '</div>' +
                '</div>' +
                '<div style="display:flex;justify-content:space-between;align-items:center;">' +
                    '<span class="signal-badge ' + dirClass + '">' + dirLabel + '</span>' +
                    '<span style="font-size:12px;color:var(--text-muted);">' + stars + '</span>' +
                '</div>' +
                '<div style="margin-top:8px;font-size:13px;font-weight:600;">$' + price + '</div>' +
                '</div>';
        }).join('');
    } catch (e) {
        console.error('Failed to load signals:', e);
        showToast('\u52a0\u8f7d\u4fe1\u53f7\u5931\u8d25: ' + friendlyError(e.message), 'error');
    }
}

/* ==========================================================================
 * SECTION 17 – Signal detail popup (BottomSheet style)
 * ========================================================================== */

function showSignalDetail(strategyKey, direction, guideData) {
    if (!strategyKey) return;

    var explainer = (typeof SIGNAL_EXPLAINER !== 'undefined' && SIGNAL_EXPLAINER[strategyKey]) || null;
    var guide = guideData || ((typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[strategyKey]) || null);

    var explanation = '';
    if (explainer && explainer[direction]) {
        explanation = explainer[direction];
    } else {
        explanation = direction === 'LONG' ? '\u7b56\u7565\u53d1\u51fa\u4e70\u5165\u4fe1\u53f7\uff0c\u5efa\u8bae\u5173\u6ce8\u5165\u573a\u65f6\u673a\u3002'
                    : direction === 'SHORT' ? '\u7b56\u7565\u53d1\u51fa\u5356\u51fa\u4fe1\u53f7\uff0c\u5efa\u8bae\u5173\u6ce8\u51fa\u573a\u65f6\u673a\u3002'
                    : '\u7b56\u7565\u5f53\u524d\u65e0\u660e\u786e\u65b9\u5411\u4fe1\u53f7\uff0c\u5efa\u8bae\u89c2\u671b\u7b49\u5f85\u3002';
    }

    var strategyName = guide ? guide.name : (typeof getStrategyLabel === 'function' ? getStrategyLabel(strategyKey) : strategyKey);
    var icon  = guide ? guide.icon : '\ud83d\udcc8';
    var dirLabel = direction === 'LONG' ? '\u4e70\u5165'
                 : direction === 'SHORT' ? '\u5356\u51fa'
                 : '\u89c2\u671b';
    var dirColor = direction === 'LONG' ? 'var(--green)'
                 : direction === 'SHORT' ? 'var(--red)'
                 : 'var(--orange)';

    var overlay = document.createElement('div');
    overlay.className = 'signal-detail-overlay';
    overlay.innerHTML =
        '<div class="signal-detail-sheet">' +
            '<div class="signal-detail-handle"></div>' +
            '<div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">' +
                '<span style="font-size:32px;">' + icon + '</span>' +
                '<div>' +
                    '<div style="font-size:18px;font-weight:700;">' + escHtml(strategyName) + '</div>' +
                    '<div style="font-size:14px;color:' + dirColor + ';font-weight:600;">' + dirLabel + '\u4fe1\u53f7</div>' +
                '</div>' +
            '</div>' +
            '<div style="background:rgba(0,0,0,0.2);border-radius:8px;padding:16px;margin-bottom:16px;">' +
                '<div style="font-size:14px;line-height:1.8;color:var(--text-primary);">' + explanation + '</div>' +
            '</div>' +
            (guide ? (
            '<div style="font-size:12px;color:var(--text-muted);line-height:1.6;">' +
                '<div>\ud83d\udcca \u9002\u5408\u884c\u60c5: ' + escHtml(guide.suitable || '--') + '</div>' +
                '<div>\u26a0\ufe0f \u4e0d\u9002\u5408: ' + escHtml(guide.unsuitable || '--') + '</div>' +
                (guide.tips ? '<div>\ud83d\udca1 ' + escHtml(guide.tips) + '</div>' : '') +
            '</div>') : '') +
            '<button class="btn btn-primary btn-block" style="margin-top:16px;" data-action="closeOverlay">\u77e5\u9053\u4e86</button>' +
        '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener('click', function(e) {
        if (e.target === overlay) overlay.remove();
    });

    requestAnimationFrame(function() {
        var sheet = overlay.querySelector('.signal-detail-sheet');
        if (sheet) sheet.style.transform = 'translateY(0)';
    });
}

/* ==========================================================================
 * SECTION 18 – Safety shield (risk page)
 * ========================================================================== */

function updateSafetyShield() {
    var shield  = safeGet('#safety-shield');
    var title   = safeGet('#safety-title');
    var detail  = safeGet('#safety-detail');
    var badges  = safeGet('#safety-badges');
    if (!shield || !title || !detail || !badges) return;

    var slEl        = safeGet('#risk-sl');
    var tpEl        = safeGet('#risk-tp');
    var dailyLossEl = safeGet('#risk-daily-loss');
    var maxPosEl    = safeGet('#risk-max-pos');

    var sl        = slEl        ? (parseFloat(slEl.value) || 0) : 0;
    var tp        = tpEl        ? (parseFloat(tpEl.value) || 0) : 0;
    var dailyLoss = dailyLossEl ? (parseFloat(dailyLossEl.value) || 0) : 0;
    var maxPos    = maxPosEl    ? (parseFloat(maxPosEl.value) || 0) : 0;

    var stopLossOk      = sl > 0;
    var takeProfitOk    = tp > 0;
    var dailyLimitOk    = dailyLoss > 0;
    var positionLimitOk = maxPos > 0;
    var allProtected    = stopLossOk && takeProfitOk && dailyLimitOk && positionLimitOk;

    if (allProtected) {
        shield.style.background = 'linear-gradient(135deg, #1b5e20, #2e7d32)';
        title.textContent = '\u6b62\u635f\u4fdd\u62a4\u5df2\u5f00\u542f';
        title.style.color = '#a5d6a7';
        detail.textContent = '\u5355\u7b14\u6b62\u635f ' + sl + '% | \u65e5\u4e8f\u635f\u4e0a\u9650 ' + dailyLoss + '% | \u8fde\u7eed\u4e8f\u635f\u7194\u65ad 3\u6b21';
        detail.style.color = '#81c784';
    } else {
        shield.style.background = 'linear-gradient(135deg, #b71c1c, #c62828)';
        title.textContent = '\u26a0\ufe0f \u6b62\u635f\u4fdd\u62a4\u672a\u5b8c\u5168\u5f00\u542f';
        title.style.color = '#ffcdd2';
        var parts = [];
        if (!stopLossOk)      parts.push('\u6b62\u635f\u5df2\u5173\u95ed');
        if (!takeProfitOk)    parts.push('\u6b62\u76c8\u5df2\u5173\u95ed');
        if (!dailyLimitOk)    parts.push('\u65e5\u4e8f\u635f\u4e0a\u9650\u5df2\u5173\u95ed');
        if (!positionLimitOk) parts.push('\u4ed3\u4f4d\u9650\u5236\u5df2\u5173\u95ed');
        detail.textContent = parts.join(' | ');
        detail.style.color = '#ef9a9a';
    }

    badges.innerHTML =
        '<span style="background: rgba(255,255,255,0.15); padding: 4px 12px; border-radius: 12px; font-size: 12px; color: ' + (stopLossOk ? '#c8e6c9' : '#ef9a9a') + ';">' + (stopLossOk ? '\u2705' : '\u274c') + ' \u6b62\u635f\u5355</span>' +
        '<span style="background: rgba(255,255,255,0.15); padding: 4px 12px; border-radius: 12px; font-size: 12px; color: ' + (takeProfitOk ? '#c8e6c9' : '#ef9a9a') + ';">' + (takeProfitOk ? '\u2705' : '\u274c') + ' \u6b62\u76c8\u5355</span>' +
        '<span style="background: rgba(255,255,255,0.15); padding: 4px 12px; border-radius: 12px; font-size: 12px; color: ' + (dailyLimitOk ? '#c8e6c9' : '#ef9a9a') + ';">' + (dailyLimitOk ? '\u2705' : '\u274c') + ' \u7194\u65ad\u673a\u5236</span>';
}

/* ==========================================================================
 * SECTION 19 – Backup & Restore
 * ========================================================================== */

async function refreshBackups() {
    try {
        var results = await Promise.all([
            API.get('/api/backup/storage'),
            API.get('/api/backup/list'),
        ]);
        var storage = results[0] || {};
        var list    = results[1] || {};

        var dbSizeEl   = safeGet('#bk-db-size');
        var bkSizeEl   = safeGet('#bk-backup-size');
        var bkCountEl  = safeGet('#bk-count');
        if (dbSizeEl)  dbSizeEl.textContent  = (storage.database_mb || 0).toFixed(1) + ' MB';
        if (bkSizeEl)  bkSizeEl.textContent  = (storage.backups_mb || 0).toFixed(1) + ' MB';
        if (bkCountEl) bkCountEl.textContent = storage.backup_count || 0;

        var tbody   = safeGet('#backups-tbody');
        var backups = list.backups || [];
        if (!tbody) return;

        if (backups.length === 0) {
            tbody.innerHTML = '<tr class="empty-row"><td colspan="4">\u6682\u65e0\u5907\u4efd</td></tr>';
            return;
        }

        tbody.innerHTML = backups.map(function(b) {
            return '<tr>' +
                '<td><strong>' + escHtml(b.filename) + '</strong></td>' +
                '<td>' + (b.size_mb || 0) + ' MB</td>' +
                '<td>' + fmtTime(b.created_at) + '</td>' +
                '<td>' +
                    '<button class="btn-sm-card primary" data-action="downloadBackup" data-action-args="' + escHtml(JSON.stringify({filename: b.filename})) + '">\u4e0b\u8f7d</button>' +
                    '<button class="btn-sm-card" style="border-color:var(--orange);color:var(--orange)" data-action="restoreBackup" data-action-args="' + escHtml(JSON.stringify({filename: b.filename})) + '">\u6062\u590d</button>' +
                    '<button class="btn-sm-card danger" data-action="deleteBackup" data-action-args="' + escHtml(JSON.stringify({filename: b.filename})) + '">\u5220\u9664</button>' +
                '</td>' +
            '</tr>';
        }).join('');
    } catch (e) {
        showToast('\u52a0\u8f7d\u5907\u4efd\u5931\u8d25: ' + friendlyError(e.message), 'error');
    }
}

async function createBackup() {
    var btn = safeGet('#page-backup .btn-primary');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '\u23f3 \u521b\u5efa\u4e2d...';
    }
    try {
        var result = await API.post('/api/backup/create', {});
        if (result && result.message) showToast(result.message, 'success');
        await refreshBackups();
    } catch (e) {
        showToast('\u521b\u5efa\u5907\u4efd\u5931\u8d25: ' + friendlyError(e.message), 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '+ \u521b\u5efa\u5907\u4efd';
        }
    }
}

function downloadBackup(filename) {
    if (!filename) return;
    window.open('/api/backup/download/' + encodeURIComponent(filename), '_blank');
}

async function restoreBackup(filename) {
    if (!filename) return;
    if (!confirm('\u6062\u590d\u5907\u4efd\u5c06\u8986\u76d6\u5f53\u524d\u6570\u636e\uff0c\u786e\u5b9a\u7ee7\u7eed\uff1f')) return;
    try {
        var result = await API.post('/api/backup/restore/' + encodeURIComponent(filename), {});
        if (result && result.message) showToast(result.message, 'success');
        await refreshBackups();
    } catch (e) {
        showToast('\u6062\u590d\u5931\u8d25: ' + friendlyError(e.message), 'error');
    }
}

async function deleteBackup(filename) {
    if (!filename) return;
    if (!confirm('\u786e\u5b9a\u5220\u9664\u5907\u4efd ' + filename + '\uff1f')) return;
    try {
        var result = await API.delete('/api/backup/delete/' + encodeURIComponent(filename));
        if (result && result.message) showToast(result.message, 'success');
        await refreshBackups();
    } catch (e) {
        showToast('\u5220\u9664\u5931\u8d25: ' + friendlyError(e.message), 'error');
    }
}

/* ==========================================================================
 * SECTION 20 – Custom Alerts
 * ========================================================================== */

function onAlertTypeChange() {
    var type      = safeGet('#alert-type');
    if (!type) return;

    var symbolGroup = safeGet('#alert-symbol-group');
    var valueLabel  = safeGet('#alert-value-label');
    var valueInput  = safeGet('#alert-value');
    var condInput   = safeGet('#alert-condition');

    if (type.value === 'pnl') {
        if (symbolGroup) symbolGroup.style.display = 'none';
        if (valueLabel)  valueLabel.textContent = '\u4e8f\u635f\u9608\u503c (USDT)';
        if (valueInput)  valueInput.value = 100;
        if (condInput)   condInput.value = 'below';
    } else {
        if (symbolGroup) symbolGroup.style.display = 'block';
        if (valueLabel)  valueLabel.textContent = '\u9608\u503c';
        if (valueInput)  valueInput.value = 60000;
    }
}

async function refreshAlerts() {
    try {
        var data = await API.get('/api/alerts/custom/list');
        var alerts = (data && data.alerts) ? data.alerts : [];
        var tbody = safeGet('#alerts-tbody');
        if (!tbody) return;

        if (alerts.length === 0) {
            tbody.innerHTML = '<tr class="empty-row"><td colspan="7">\u6682\u65e0\u81ea\u5b9a\u4e49\u544a\u8b66</td></tr>';
            return;
        }

        tbody.innerHTML = alerts.map(function(a) {
            var typeLabel  = a.type === 'price' ? '\u4ef7\u683c\u544a\u8b66'
                           : a.type === 'pnl'   ? '\u76c8\u4e8f\u544a\u8b66'
                           : a.type;
            var condLabel  = a.condition === 'above' ? '\u7a81\u7834\u4e0a\u9650'
                           : a.condition === 'below' ? '\u8dcc\u7834\u4e0b\u9650'
                           : a.condition;
            var statusCls  = a.triggered ? 'negative' : 'positive';
            var statusLabel = a.triggered ? '\u5df2\u89e6\u53d1'
                            : (a.enabled  ? '\u76d1\u63a7\u4e2d'
                            : '\u5df2\u7981\u7528');
            return '<tr>' +
                '<td>' + escHtml(typeLabel) + '</td>' +
                '<td>' + escHtml(a.symbol || '--') + '</td>' +
                '<td>' + escHtml(condLabel) + '</td>' +
                '<td>' + escHtml(String(a.value)) + '</td>' +
                '<td>' + escHtml(a.message || '--') + '</td>' +
                '<td><span class="' + statusCls + '">' + statusLabel + '</span></td>' +
                '<td>' +
                    '<button class="btn-sm-card" data-action="toggleAlert" data-action-args="' + escHtml(JSON.stringify({alertId: a.id, enabled: !a.enabled})) + '">' + (a.enabled ? '\u7981\u7528' : '\u542f\u7528') + '</button>' +
                    '<button class="btn-sm-card danger" data-action="deleteAlert" data-action-args="' + escHtml(JSON.stringify({alertId: a.id})) + '">\u5220\u9664</button>' +
                '</td>' +
            '</tr>';
        }).join('');
    } catch (e) {
        showToast('\u52a0\u8f7d\u544a\u8b66\u5931\u8d25: ' + friendlyError(e.message), 'error');
    }
}

async function createAlert() {
    var typeEl      = safeGet('#alert-type');
    var symbolEl    = safeGet('#alert-symbol');
    var condEl      = safeGet('#alert-condition');
    var valueEl     = safeGet('#alert-value');
    var messageEl   = safeGet('#alert-message');

    if (!typeEl || !condEl || !valueEl) {
        showToast('\u9875\u9762\u5143\u7d20\u7f3a\u5931', 'error');
        return;
    }

    var type      = typeEl.value;
    var symbol    = type === 'pnl' ? '' : (symbolEl ? symbolEl.value : '');
    var condition = condEl.value;
    var value     = parseFloat(valueEl.value);
    var message   = messageEl ? messageEl.value : '';

    if (isNaN(value) || value <= 0) {
        showToast('\u8bf7\u8f93\u5165\u6709\u6548\u7684\u9608\u503c', 'warning');
        return;
    }

    try {
        await API.post('/api/alerts/custom/create', {
            type: type,
            symbol: symbol,
            condition: condition,
            value: value,
            message: message,
            enabled: true,
        });
        showToast('\u544a\u8b66\u5df2\u6dfb\u52a0', 'success');
        if (messageEl) messageEl.value = '';
        await refreshAlerts();
    } catch (e) {
        showToast('\u6dfb\u52a0\u544a\u8b66\u5931\u8d25: ' + friendlyError(e.message), 'error');
    }
}

async function toggleAlert(alertId, enabled) {
    if (alertId === undefined || alertId === null) return;
    try {
        await API.post('/api/alerts/custom/toggle', { alert_id: alertId, enabled: enabled });
        showToast(enabled ? '\u544a\u8b66\u5df2\u542f\u7528' : '\u544a\u8b66\u5df2\u7981\u7528', 'info');
        await refreshAlerts();
    } catch (e) {
        showToast('\u64cd\u4f5c\u5931\u8d25: ' + friendlyError(e.message), 'error');
    }
}

async function deleteAlert(alertId) {
    if (alertId === undefined || alertId === null) return;
    if (!confirm('\u786e\u5b9a\u5220\u9664\u8be5\u544a\u8b66\uff1f')) return;
    try {
        await API.delete('/api/alerts/custom/' + encodeURIComponent(alertId));
        showToast('\u544a\u8b66\u5df2\u5220\u9664', 'success');
        await refreshAlerts();
    } catch (e) {
        showToast('\u5220\u9664\u5931\u8d25: ' + friendlyError(e.message), 'error');
    }
}

/* ==========================================================================
 * SECTION 21 – Trade Notes
 * ========================================================================== */

async function showNoteEditor(tradeId, currentNote) {
    if (tradeId === undefined || tradeId === null) return;

    var overlay = document.createElement('div');
    overlay.className = 'signal-detail-overlay';
    overlay.innerHTML =
        '<div class="signal-detail-sheet">' +
            '<div class="signal-detail-handle"></div>' +
            '<h3 style="margin-bottom:12px;">\ud83d\udcdd \u4ea4\u6613\u7b14\u8bb0</h3>' +
            '<textarea id="note-textarea" style="width:100%;min-height:120px;background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);color:var(--text-primary);padding:12px;font-size:14px;resize:vertical;" placeholder="\u8bb0\u5f55\u8fd9\u7b14\u4ea4\u6613\u7684\u60f3\u6cd5...">' + escHtml(currentNote || '') + '</textarea>' +
            '<div style="display:flex;gap:8px;margin-top:12px;">' +
                '<button class="btn btn-primary" style="flex:1;" data-action="saveNoteFromEditor" data-action-args="' + escHtml(JSON.stringify({tradeId: tradeId})) + '">\u4fdd\u5b58</button>' +
                '<button class="btn" style="flex:1;background:var(--bg-hover);color:var(--text-primary);" data-action="closeOverlay">\u53d6\u6d88</button>' +
            '</div>' +
        '</div>';
    document.body.appendChild(overlay);

    overlay.addEventListener('click', function(e) {
        if (e.target === overlay) overlay.remove();
    });

    requestAnimationFrame(function() {
        var sheet = overlay.querySelector('.signal-detail-sheet');
        if (sheet) sheet.style.transform = 'translateY(0)';
    });
}

async function saveNoteFromEditor(tradeId) {
    if (tradeId === undefined || tradeId === null) return;
    var textarea = safeGet('#note-textarea');
    var noteText = textarea ? textarea.value : '';
    var overlay = document.querySelector('.signal-detail-overlay');
    await saveNote(tradeId, noteText, overlay);
}

async function saveNote(tradeId, noteText, overlay) {
    if (tradeId === undefined || tradeId === null) return;

    try {
        await API.post('/api/trade/note', { trade_id: tradeId, note: noteText });
        showToast('\u7b14\u8bb0\u5df2\u4fdd\u5b58', 'success');
        if (overlay) overlay.remove();

        // Refresh trades table if on trades page
        var tradesPage = safeGet('#page-trades');
        if (tradesPage && tradesPage.classList.contains('active')) {
            if (typeof refreshTrades === 'function') refreshTrades();
        }
    } catch (e) {
        showToast('\u4fdd\u5b58\u5931\u8d25: ' + friendlyError(e.message), 'error');
    }
}

/* ==========================================================================
 * SECTION 22 – AI Strategy Recommend
 * ========================================================================== */

async function loadRecommend() {
    var loading = safeGet('#recommend-loading');
    var content = safeGet('#recommend-content');
    var symbolEl = safeGet('#recommend-symbol');
    var symbol = symbolEl ? (symbolEl.value || 'BTCUSDT') : 'BTCUSDT';

    if (loading) loading.style.display = 'block';
    if (content) content.style.display = 'none';

    try {
        var data = await API.get('/api/recommend/analyze?symbol=' + encodeURIComponent(symbol));
        if (!data) return;

        if (loading) loading.style.display = 'none';
        if (content) content.style.display = 'block';

        // Error message
        var errEl = safeGet('#rec-error-msg');
        if (data.error) {
            if (errEl) { errEl.style.display = 'block'; errEl.textContent = data.error; }
        } else {
            if (errEl) errEl.style.display = 'none';
        }

        // Current price
        var priceEl = safeGet('#rec-current-price');
        if (priceEl && data.current_price) {
            priceEl.textContent = fmtUSD(data.current_price);
        }

        // Market state cards
        var ms = data.market_state || {};

        var trendBar = safeGet('#rec-trend-bar');
        if (trendBar) trendBar.style.width = (ms.trend_strength || 0) + '%';

        var trendStrength = safeGet('#rec-trend-strength');
        if (trendStrength) trendStrength.textContent = (ms.trend_strength || 0) + '%';

        var trendDir = safeGet('#rec-trend-dir');
        if (trendDir) {
            var dirMap = { up: '\ud83d\udc02 \u591a\u5934', down: '\ud83d\udc3b \u7a7a\u5934', neutral: '\u2194\ufe0f \u4e2d\u6027' };
            trendDir.textContent = dirMap[ms.trend_direction] || '--';
        }

        var volEl = safeGet('#rec-volatility');
        if (volEl) {
            var volMap = { high: '\ud83c\udf0a \u9ad8\u6ce2\u52a8', normal: '\ud83d\udcca \u6b63\u5e38', low: '\ud83d\ude34 \u4f4e\u6ce2\u52a8' };
            volEl.textContent = volMap[ms.volatility_regime] || '--';
        }

        var rsiVal = safeGet('#rec-rsi-value');
        if (rsiVal) rsiVal.textContent = (ms.rsi || 0).toFixed(1);

        // Draw RSI gauge
        drawRSIGauge(ms.rsi || 50);

        // Market summary
        var summaryEl = safeGet('#rec-summary-text');
        if (summaryEl && data.market_summary && data.market_summary.summaries) {
            summaryEl.innerHTML = data.market_summary.summaries.map(function(s) {
                return '<div style="padding:6px 0;font-size:14px;line-height:1.6;">' + escHtml(s) + '</div>';
            }).join('');
        }

        // Strategy recommendations
        var strategiesEl = safeGet('#rec-strategies');
        if (strategiesEl && data.recommendations) {
            strategiesEl.innerHTML = data.recommendations.map(function(r, i) {
                var scoreColor = r.score >= 8 ? 'var(--green)' : r.score >= 6 ? 'var(--blue)' : 'var(--orange)';
                var rankIcon = i === 0 ? '\ud83e\udd47' : i === 1 ? '\ud83e\udd48' : i === 2 ? '\ud83e\udd49' : (i + 1);
                return '<div style="display:flex;align-items:center;gap:12px;padding:14px 12px;border-bottom:1px solid var(--border);transition:background 0.2s;" ' +
                            'onmouseover="this.style.background=\'rgba(255,255,255,0.03)\'" onmouseout="this.style.background=\'\'">' +
                    '<div style="font-size:24px;min-width:36px;text-align:center;">' + rankIcon + '</div>' +
                    '<div style="flex:1;min-width:0;">' +
                        '<div style="font-weight:600;font-size:15px;margin-bottom:4px;">' + escHtml(r.name_cn) + '</div>' +
                        '<div style="font-size:12px;color:var(--text-muted);line-height:1.5;">' + escHtml(r.advice) + '</div>' +
                        '<div style="font-size:13px;color:' + scoreColor + ';margin-top:4px;">' +
                            '\u8bc4\u5206: <strong>' + r.score + '</strong>/10 ' + r.star_rating +
                        '</div>' +
                    '</div>' +
                    '<div style="display:flex;flex-direction:column;gap:6px;flex-shrink:0;">' +
                        '<div style="width:60px;height:6px;background:rgba(255,255,255,0.1);border-radius:3px;overflow:hidden;">' +
                            '<div style="height:100%;background:' + scoreColor + ';border-radius:3px;width:' + (r.score * 10) + '%;transition:width 0.8s ease;"></div>' +
                        '</div>' +
                        '<button class="btn-sm-card primary" data-action="useRecommendedStrategy" data-action-args="' + escHtml(JSON.stringify({strategyKey: r.strategy, strategyName: r.name_cn})) + '" style="font-size:12px;white-space:nowrap;">' +
                            '\ud83d\ude80 \u4f7f\u7528\u6b64\u7b56\u7565' +
                        '</button>' +
                    '</div>' +
                '</div>';
            }).join('');
        }
    } catch (e) {
        if (loading) loading.style.display = 'none';
        if (content) content.style.display = 'block';
        showToast('\u52a0\u8f7d\u63a8\u8350\u5931\u8d25: ' + friendlyError(e.message), 'error');
    }
}

/* ==========================================================================
 * SECTION 23 – RSI Gauge drawing
 * ========================================================================== */

function drawRSIGauge(rsi) {
    var canvas = safeGet('#rec-rsi-gauge');
    if (!canvas) return;

    var ctx = canvas.getContext('2d');
    if (!ctx) return;

    var w = canvas.width;
    var h = canvas.height;
    var cx = w / 2;
    var cy = h / 2;
    var radius = 30;
    var lineWidth = 8;

    ctx.clearRect(0, 0, w, h);

    // Background arc
    ctx.beginPath();
    ctx.arc(cx, cy, radius, Math.PI, 0);
    ctx.strokeStyle = 'rgba(255,255,255,0.1)';
    ctx.lineWidth = lineWidth;
    ctx.stroke();

    // Value arc (0–100 RSI maps to PI → 0)
    var angle = Math.PI + (Math.PI * Math.min(100, Math.max(0, rsi)) / 100);

    var color;
    if (rsi > 70) color = '#ef5350';
    else if (rsi < 30) color = '#66bb6a';
    else color = '#42a5f5';

    ctx.beginPath();
    ctx.arc(cx, cy, radius, Math.PI, angle);
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.stroke();

    // Rounded cap
    var capX = cx + radius * Math.cos(angle);
    var capY = cy + radius * Math.sin(angle);
    ctx.beginPath();
    ctx.arc(capX, capY, lineWidth / 2, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
}

/* ==========================================================================
 * SECTION 24 – useRecommendedStrategy (cross-page navigation)
 * ========================================================================== */

function useRecommendedStrategy(strategyKey, strategyName) {
    if (!strategyKey) return;

    // Deactivate all nav items
    var navItems = document.querySelectorAll('.nav-item');
    for (var i = 0; i < navItems.length; i++) {
        navItems[i].classList.remove('active');
    }

    // Activate strategy nav
    var strategyNav = document.querySelector('[data-page="strategy"]');
    if (strategyNav) strategyNav.classList.add('active');

    // Switch to strategy page
    var allPages = document.querySelectorAll('.page');
    for (var j = 0; j < allPages.length; j++) {
        allPages[j].classList.remove('active');
    }
    var strategyPage = safeGet('#page-strategy');
    if (strategyPage) strategyPage.classList.add('active');

    // Pre-select the strategy in the creation modal after a short delay
    setTimeout(function() {
        if (typeof showStrategyModal === 'function') {
            showStrategyModal();
        }
        var sel = safeGet('#modal-strategy');
        if (sel) sel.value = strategyKey;
    }, 200);

    showToast('\u5df2\u9009\u62e9\u7b56\u7565: ' + (strategyName || strategyKey), 'info');
}

function closeOverlay() {
    var overlay = document.querySelector('.signal-detail-overlay');
    if (overlay) overlay.remove();
}

/* ==========================================================================
 * SECTION 25 – Unified event delegation for data-action
 * ========================================================================== */

(function() {
    document.addEventListener('click', function(e) {
        var target = e.target.closest('[data-action]');
        if (!target) return;

        var action = target.getAttribute('data-action');
        if (!action) return;

        // Handle action:arg syntax (e.g. "changeCalendarMonth:-1")
        var colonIdx = action.indexOf(':');
        var fnName = colonIdx !== -1 ? action.substring(0, colonIdx) : action;
        var arg = colonIdx !== -1 ? action.substring(colonIdx + 1) : undefined;

        // Check for JSON args in data-action-args
        var argsJson = target.getAttribute('data-action-args');

        // Map to global function
        if (typeof window[fnName] === 'function') {
            e.preventDefault();
            try {
                if (argsJson) {
                    var parsed = JSON.parse(argsJson);
                    // Call function with parsed values keyed by function parameter names
                    var funcStr = window[fnName].toString();
                    var paramMatch = funcStr.match(/\(([^)]*)\)/);
                    var paramNames = [];
                    if (paramMatch && paramMatch[1].trim()) {
                        paramNames = paramMatch[1].split(',').map(function(s) {
                            return s.trim().replace(/\/\*.*?\*\//g, '').replace(/\/\/.*/g, '').trim();
                        });
                    }
                    var argsArr = paramNames.map(function(name) {
                        return parsed[name];
                    });
                    window[fnName].apply(window, argsArr);
                } else if (arg !== undefined) {
                    // Try numeric first
                    var numVal = Number(arg);
                    if (!isNaN(numVal) && String(numVal) === arg) {
                        window[fnName](numVal);
                    } else {
                        window[fnName](arg);
                    }
                } else {
                    window[fnName]();
                }
            } catch (err) {
                console.error('Error calling ' + fnName + ':', err);
                showToast('操作失败: ' + friendlyError(err.message), 'error');
            }
        }
    });

    // Handle change events on select elements with data-action
    document.addEventListener('change', function(e) {
        var target = e.target.closest('[data-action]');
        if (!target) return;

        var action = target.getAttribute('data-action');
        if (!action) return;

        if (typeof window[action] === 'function') {
            try {
                window[action]();
            } catch (err) {
                console.error('Error calling ' + action + ':', err);
                showToast('操作失败: ' + friendlyError(err.message), 'error');
            }
        }
    });
})();
