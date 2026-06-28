/**
 * Dashboard – Account overview, K-line chart, positions, equity curve,
 * PnL calendar, day-trade detail, landscape fullscreen.
 *
 * Defensive rewrite:
 *   – All DOM access via safeGet() (defined in app.js)
 *   – LightweightCharts / Chart.js checked before use
 *   – Timestamp type-guarded (number vs string)
 *   – All API calls wrapped in try/catch
 *   – Null/undefined guards on every dereference
 *   – Preserves all original global function signatures
 */
'use strict';

/* ==========================================================================
 * Chart state (module-private)
 * ========================================================================== */

var klineChart    = null;
var candleSeries  = null;
var volumeSeries  = null;
var sma20Series   = null;
var sma50Series   = null;
var _currentSymbol   = null;
var _currentInterval = null;
var equityChart      = null;
var accountData      = null;

/* ==========================================================================
 * Defensive helpers (local)
 * ========================================================================== */

/** Guard a value: if null/undefined/NaN, return fallback. */
function _or(v, fallback) {
    if (v === null || v === undefined || (typeof v === 'number' && isNaN(v))) return fallback;
    return v;
}

/** Normalise a timestamp to a number (ms).  Accepts number or ISO string. */
function _toMs(ts) {
    if (ts === null || ts === undefined) return 0;
    if (typeof ts === 'number') return ts;
    if (typeof ts === 'string') {
        var d = Date.parse(ts);
        return isNaN(d) ? 0 : d;
    }
    return 0;
}

/** Normalise a timestamp to a Date object safely. */
function _toDate(ts) {
    var ms = _toMs(ts);
    if (ms <= 0) return new Date();
    return new Date(ms);
}

/** Build a calendar date string YYYY-MM-DD from any timestamp. */
function _toDateStr(ts) {
    var d = _toDate(ts);
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
}

/* ==========================================================================
 * Refresh dashboard (entry point)
 * ========================================================================== */

function refreshDashboard() {
    return Promise.all([
        loadAccount(),
        loadKlineChart(),
        loadEquityChart(),
    ]);
}

/* ==========================================================================
 * Account & positions
 * ========================================================================== */

function loadAccount() {
    return (async function() {
        try {
            var data = await API.get('/api/account');
            if (!data) return;
            accountData = data;

            // Stat cards
            var totalEquityEl      = safeGet('#total-equity');
            var availableBalanceEl = safeGet('#available-balance');
            var positionCountEl    = safeGet('#position-count');
            var totalTradesEl      = safeGet('#total-trades');
            var pnlEl              = safeGet('#total-pnl');

            if (totalEquityEl)      totalEquityEl.textContent      = fmtUSD(data.total_equity);
            if (availableBalanceEl) availableBalanceEl.textContent = fmtUSD(data.capital);
            if (positionCountEl)    positionCountEl.textContent    = _or(data.open_positions, 0);
            if (totalTradesEl)      totalTradesEl.textContent      = _or(data.total_trades, 0);

            if (pnlEl) {
                var pnl    = _or(data.total_pnl, 0);
                var pnlPct = _or(data.total_pnl_pct, 0);
                pnlEl.textContent = fmtUSD(pnl) + ' (' + fmtPct(pnlPct) + ')';
                pnlEl.className   = 'stat-change ' + (pnl >= 0 ? 'positive' : 'negative');
            }

            // Positions table
            var tbody = safeGet('#positions-table tbody');
            if (!tbody) return;

            var positions = Array.isArray(data.positions) ? data.positions : [];

            if (positions.length === 0) {
                tbody.textContent = '';
                var emptyRow = document.createElement('tr');
                emptyRow.className = 'empty-row';
                var emptyTd = document.createElement('td');
                emptyTd.colSpan = 8;
                emptyTd.textContent = '\u6682\u65e0\u6301\u4ed3'; // 暂无持仓
                emptyRow.appendChild(emptyTd);
                tbody.appendChild(emptyRow);
                return;
            }

            tbody.textContent = '';

            for (var i = 0; i < positions.length; i++) {
                var p = positions[i];
                if (!p) continue;
                var upnl = _or(p.unrealized_pnl, 0);
                var cls  = upnl >= 0 ? 'positive' : 'negative';
                var sideLabel = p.side === 'LONG' ? '\u505a\u591a' : '\u505a\u7a7a'; // 做多/做空

                var tr = document.createElement('tr');
                tr.innerHTML =
                    '<td><strong>' + escHtml(p.symbol) + '</strong></td>' +
                    '<td><span class="' + (p.side === 'LONG' ? 'positive' : 'negative') + '">' + sideLabel + '</span></td>' +
                    '<td>' + fmtUSD(p.entry_price) + '</td>' +
                    '<td>' + fmtUSD(p.current_price) + '</td>' +
                    '<td>' + ((typeof p.quantity === 'number') ? p.quantity.toFixed(4) : '--') + '</td>' +
                    '<td>' + (_or(p.leverage, 3)) + 'x</td>' +
                    '<td class="' + cls + '">' + fmtUSD(upnl) + '</td>' +
                    '<td></td>';

                var btn = document.createElement('button');
                btn.className = 'btn-sm-card danger';
                btn.textContent = '\u5e73\u4ed3'; // 平仓
                btn.addEventListener('click', (function(sym) {
                    return function() { closePosition(sym); };
                })(p.symbol));

                var lastTd = tr.querySelector('td:last-child');
                if (lastTd) lastTd.appendChild(btn);
                tbody.appendChild(tr);
            }
        } catch (e) {
            console.error('Failed to load account:', e);
        }
    })();
}

function closePosition(symbol) {
    if (!symbol) return;
    if (!confirm('\u786e\u8ba4\u5e73\u4ed3 ' + symbol + '\uff1f')) return; // 确认平仓?

    (async function() {
        try {
            await API.post('/api/trade/close', { symbol: symbol });
            loadAccount();
        } catch (e) {
            if (typeof showToast === 'function') {
                showToast('\u5e73\u4ed3\u5931\u8d25: ' + friendlyError(e.message), 'error');
            }
        }
    })();
}

/* ==========================================================================
 * K-line Chart (LightweightCharts)
 * ========================================================================== */

var _klineDebounceTimer = null;
var _lastKlineSymbol    = null;
var _lastKlineInterval  = null;

function loadKlineChart() {
    var symbolEl   = safeGet('#dashboard-symbol');
    var symbol     = (symbolEl && symbolEl.value) ? symbolEl.value : 'BTCUSDT';

    var activeBtn  = safeGet('.chart-controls .btn-sm.active');
    var interval   = (activeBtn && activeBtn.dataset) ? (activeBtn.dataset.interval || '1h') : '1h';

    // 如果图表已存在且symbol/interval相同，跳过重建
    if (klineChart && _lastKlineSymbol === symbol && _lastKlineInterval === interval) {
        return;
    }
    _lastKlineSymbol = symbol;
    _lastKlineInterval = interval;

    // Debounce guard
    var key = symbol + ':' + interval;
    if (key === _lastKlineSymbol + ':' + _lastKlineInterval && _klineDebounceTimer) {
        return Promise.resolve();
    }

    _lastKlineSymbol   = symbol;
    _lastKlineInterval = interval;

    if (_klineDebounceTimer) clearTimeout(_klineDebounceTimer);

    return new Promise(function(resolve) {
        _klineDebounceTimer = setTimeout(function() {
            _klineDebounceTimer = null;
            _loadKlineChartInner(symbol, interval).then(resolve).catch(function() {
                resolve(); // never reject the caller
            });
        }, 200);
    });
}

function _loadKlineChartInner(symbol, interval) {
    return (async function() {
        try {
            var data = await API.get('/api/market/klines?symbol=' + encodeURIComponent(symbol) + '&interval=' + encodeURIComponent(interval) + '&limit=200');
            if (!Array.isArray(data) || data.length === 0) return;

            // Guard: LightweightCharts must exist
            if (typeof LightweightCharts === 'undefined') {
                console.warn('LightweightCharts not available');
                return;
            }

            var container = safeGet('#kline-chart');
            if (!container) return;

            var symbolOrIntervalChanged = (symbol !== _currentSymbol || interval !== _currentInterval);

            // ---- Same symbol/interval → just update data ----
            if (klineChart && !symbolOrIntervalChanged) {
                var candleData2 = [];
                var volumeData2 = [];
                var closePrices2 = [];
                for (var i2 = 0; i2 < data.length; i2++) {
                    var d2 = data[i2];
                    if (!d2) continue;
                    var time2 = Math.floor(_toMs(d2.timestamp) / 1000);
                    if (time2 <= 0) continue;
                    candleData2.push({
                        time: time2,
                        open: _or(d2.open, 0),
                        high: _or(d2.high, 0),
                        low:  _or(d2.low, 0),
                        close: _or(d2.close, 0),
                    });
                    volumeData2.push({
                        time: time2,
                        value: _or(d2.volume, 0),
                        color: (_or(d2.close, 0) >= _or(d2.open, 0)) ? 'rgba(76,175,132,0.4)' : 'rgba(239,83,80,0.4)',
                    });
                    closePrices2.push(_or(d2.close, 0));
                }
                if (candleSeries && candleData2.length) candleSeries.setData(candleData2);
                if (volumeSeries && volumeData2.length) volumeSeries.setData(volumeData2);

                var sma20_2 = calcSMA(closePrices2, 20);
                var sma50_2 = calcSMA(closePrices2, 50);
                if (sma20Series && candleData2.length) {
                    var s20 = [];
                    for (var j2 = 0; j2 < candleData2.length; j2++) {
                        if (sma20_2[j2] !== null && sma20_2[j2] !== undefined) {
                            s20.push({ time: candleData2[j2].time, value: sma20_2[j2] });
                        }
                    }
                    sma20Series.setData(s20);
                }
                if (sma50Series && candleData2.length) {
                    var s50 = [];
                    for (var k2 = 0; k2 < candleData2.length; k2++) {
                        if (sma50_2[k2] !== null && sma50_2[k2] !== undefined) {
                            s50.push({ time: candleData2[k2].time, value: sma50_2[k2] });
                        }
                    }
                    sma50Series.setData(s50);
                }
                return;
            }

            // ---- Symbol/interval changed or first load → recreate chart ----
            if (klineChart) {
                try { klineChart.remove(); } catch (_) { /* ignore */ }
                klineChart   = null;
                candleSeries = null;
                volumeSeries = null;
                sma20Series  = null;
                sma50Series  = null;
            }

            _currentSymbol   = symbol;
            _currentInterval = interval;

            klineChart = LightweightCharts.createChart(container, {
                layout: {
                    background: { color: 'transparent' },
                    textColor: '#94a3b8',
                    fontSize: 12,
                },
                grid: {
                    vertLines: { color: 'rgba(35, 38, 53, 0.5)' },
                    horzLines: { color: 'rgba(35, 38, 53, 0.5)' },
                },
                crosshair: {
                    mode: 1,
                    vertLine: { color: 'rgba(59, 130, 246, 0.5)', width: 1 },
                    horzLine: { color: 'rgba(59, 130, 246, 0.5)', width: 1 },
                },
                rightPriceScale: { borderColor: '#232635' },
                timeScale: {
                    borderColor: '#232635',
                    timeVisible: true,
                    borderVisible: true,
                },
                width: Math.max(1, container.clientWidth || 800),
                height: Math.max(260, container.clientHeight || 400),
                handleScroll: { vertTouchDrag: false },
            });

            candleSeries = klineChart.addCandlestickSeries({
                upColor: '#22c55e',
                downColor: '#ef4444',
                borderUpColor: '#22c55e',
                borderDownColor: '#ef4444',
                wickUpColor: '#22c55e',
                wickDownColor: '#ef4444',
            });

            volumeSeries = klineChart.addHistogramSeries({
                color: 'rgba(59, 130, 246, 0.3)',
                priceFormat: { type: 'volume' },
                priceScaleId: '',
            });
            klineChart.priceScale('').applyOptions({
                scaleMargins: { top: 0.8, bottom: 0 },
            });

            var candleData = [];
            var volumeData = [];
            var closePrices = [];
            for (var i = 0; i < data.length; i++) {
                var d = data[i];
                if (!d) continue;
                var time = Math.floor(_toMs(d.timestamp) / 1000);
                if (time <= 0) continue;
                candleData.push({
                    time: time,
                    open: _or(d.open, 0),
                    high: _or(d.high, 0),
                    low:  _or(d.low, 0),
                    close: _or(d.close, 0),
                });
                volumeData.push({
                    time: time,
                    value: _or(d.volume, 0),
                    color: (_or(d.close, 0) >= _or(d.open, 0)) ? 'rgba(76,175,132,0.4)' : 'rgba(239,83,80,0.4)',
                });
                closePrices.push(_or(d.close, 0));
            }

            if (candleData.length) candleSeries.setData(candleData);
            if (volumeData.length) volumeSeries.setData(volumeData);

            // SMA overlays
            var sma20 = calcSMA(closePrices, 20);
            var sma50 = calcSMA(closePrices, 50);

            sma20Series = klineChart.addLineSeries({ color: '#ffa726', lineWidth: 1 });
            sma50Series = klineChart.addLineSeries({ color: '#7c4dff', lineWidth: 1 });

            var sma20Data = [];
            var sma50Data = [];
            for (var j = 0; j < candleData.length; j++) {
                if (sma20[j] !== null && sma20[j] !== undefined) {
                    sma20Data.push({ time: candleData[j].time, value: sma20[j] });
                }
                if (sma50[j] !== null && sma50[j] !== undefined) {
                    sma50Data.push({ time: candleData[j].time, value: sma50[j] });
                }
            }
            if (sma20Data.length) sma20Series.setData(sma20Data);
            if (sma50Data.length) sma50Series.setData(sma50Data);

            _ensureResizeHandler();
        } catch (e) {
            console.error('Failed to load kline:', e);
        }
    })();
}

/* ==========================================================================
 * Resize handler (registered once)
 * ========================================================================== */

var _resizeRegistered = false;

function _ensureResizeHandler() {
    if (_resizeRegistered) return;
    _resizeRegistered = true;

    window.addEventListener('resize', function() {
        var container = safeGet('#kline-chart');
        if (!klineChart || !container) return;
        try {
            klineChart.applyOptions({ width: container.clientWidth });
        } catch (_) { /* ignore */ }
    });
}

/* ==========================================================================
 * SMA calculation
 * ========================================================================== */

function calcSMA(data, period) {
    if (!Array.isArray(data)) return [];
    var result = new Array(data.length);
    for (var i = 0; i < data.length; i++) result[i] = null;

    for (var i2 = period - 1; i2 < data.length; i2++) {
        var sum = 0;
        for (var j = 0; j < period; j++) {
            sum += _or(data[i2 - j], 0);
        }
        result[i2] = sum / period;
    }
    return result;
}

/* ==========================================================================
 * Equity Curve Chart (Chart.js)
 * ========================================================================== */

function loadEquityChart() {
    return (async function() {
        try {
            var account = await API.get('/api/account');
            if (!account) return;

            var history = await API.get('/api/trade/history?limit=200');
            var trades = (history && Array.isArray(history.trades)) ? history.trades : [];

            // Guard: Chart must exist
            if (typeof Chart === 'undefined') {
                console.warn('Chart.js not available');
                return;
            }

            var canvas = safeGet('#equity-chart');
            if (!canvas) return;
            var ctx = canvas.getContext('2d');
            if (!ctx) return;

            if (equityChart) {
                try { equityChart.destroy(); } catch (_) { /* ignore */ }
                equityChart = null;
            }

            var points = buildEquityCurve(account, trades);
            var labels = [];
            var values = [];
            for (var i = 0; i < points.length; i++) {
                labels.push(_or(points[i].label, ''));
                values.push(_or(points[i].equity, 0));
            }

            equityChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: '\u6743\u76ca', // 权益
                        data: values,
                        borderColor: '#4fc3f7',
                        backgroundColor: 'rgba(79,195,247,0.1)',
                        fill: true,
                        tension: 0.3,
                        pointRadius: 0,
                        borderWidth: 2,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { display: false },
                        y: {
                            ticks: { color: '#8b8fa8', callback: function(v) { return '$' + v.toLocaleString(); } },
                            grid: { color: 'rgba(46,49,80,0.3)' },
                        },
                    },
                },
            });
        } catch (e) {
            console.error('Failed to load equity chart:', e);
        }
    })();
}

function buildEquityCurve(account, trades) {
    if (!account) return [{ label: '', equity: 0 }];

    var initialCapital = _or(account.initial_capital, 10000);
    var points = [];
    var equity = initialCapital;

    // Sort trades chronologically (oldest first)
    var sorted = Array.isArray(trades) ? trades.slice() : [];
    sorted.sort(function(a, b) {
        var aMs = _toMs((a && a.timestamp) ? a.timestamp : 0);
        var bMs = _toMs((b && b.timestamp) ? b.timestamp : 0);
        return aMs - bMs;
    });

    // Start point
    if (sorted.length > 0) {
        points.push({
            label: _toDate(sorted[0].timestamp).toLocaleDateString(),
            equity: initialCapital,
        });
    }

    for (var i = 0; i < sorted.length; i++) {
        var t = sorted[i];
        if (!t) continue;
        if (t.side === 'CLOSE') {
            equity += _or(t.pnl, 0);
            points.push({
                label: _toDate(t.timestamp).toLocaleDateString(),
                equity: Math.round(equity * 100) / 100,
            });
        }
    }

    // Current equity as final point
    var currentEquity = _or(account.total_equity, equity);
    if (points.length === 0 || points[points.length - 1].equity !== currentEquity) {
        points.push({
            label: new Date().toLocaleDateString(),
            equity: currentEquity,
        });
    }

    // Ensure at least 2 points
    if (points.length < 2) {
        points.unshift({ label: '', equity: initialCapital });
    }

    return points;
}

/* ==========================================================================
 * Interval buttons (delegated)
 * ========================================================================== */

(function() {
    var buttons = document.querySelectorAll('.chart-controls .btn-sm');
    if (!buttons || buttons.length === 0) return;
    for (var i = 0; i < buttons.length; i++) {
        buttons[i].addEventListener('click', function() {
            var all = document.querySelectorAll('.chart-controls .btn-sm');
            for (var j = 0; j < all.length; j++) {
                all[j].classList.remove('active');
            }
            this.classList.add('active');
            loadKlineChart();
        });
    }
})();

// Symbol selector
(function() {
    var sel = safeGet('#dashboard-symbol');
    if (sel) {
        sel.addEventListener('change', function() { loadKlineChart(); });
    }
})();

/* ==========================================================================
 * PnL Calendar
 * ========================================================================== */

var _calendarYear  = new Date().getFullYear();
var _calendarMonth = new Date().getMonth(); // 0-based

function loadPnLCalendar() {
    return (async function() {
        try {
            var data = await API.get('/api/trade/history?limit=500');
            var trades = (data && Array.isArray(data.trades)) ? data.trades : [];

            // Aggregate PnL by date
            var dailyPnl = {};
            var dailyTrades = {};
            for (var i = 0; i < trades.length; i++) {
                var t = trades[i];
                if (!t) continue;
                var d = _toDateStr(t.timestamp);
                if (!d) continue;
                dailyPnl[d] = (_or(dailyPnl[d], 0)) + _or(t.pnl, 0);
                if (!dailyTrades[d]) dailyTrades[d] = [];
                dailyTrades[d].push(t);
            }

            renderCalendar(dailyPnl, dailyTrades);
        } catch (e) {
            console.error('Failed to load PnL calendar:', e);
        }
    })();
}

function renderCalendar(dailyPnl, dailyTrades) {
    var container = safeGet('#pnl-calendar');
    if (!container) return;

    var year  = _calendarYear;
    var month = _calendarMonth;

    // Month label
    var monthLabel = safeGet('#calendar-month-label');
    if (monthLabel) {
        monthLabel.textContent = year + '\u5e74' + (month + 1) + '\u6708'; // 年/月
    }

    // Max abs PnL for color scaling
    var maxAbsPnl = 0;
    var pnlKeys = Object.keys(dailyPnl);
    for (var ki = 0; ki < pnlKeys.length; ki++) {
        var absV = Math.abs(dailyPnl[pnlKeys[ki]] || 0);
        if (absV > maxAbsPnl) maxAbsPnl = absV;
    }

    // Weekday headers
    var weekdays = ['\u65e5', '\u4e00', '\u4e8c', '\u4e09', '\u56db', '\u4e94', '\u516d']; // 日一二三四五六
    var html = '';
    for (var w = 0; w < weekdays.length; w++) {
        html += '<div class="calendar-weekday">' + weekdays[w] + '</div>';
    }

    // First day of month
    var firstDay   = new Date(year, month, 1).getDay();
    var daysInMonth = new Date(year, month + 1, 0).getDate();

    var todayStr = _toDateStr(new Date().toISOString());

    // Empty cells before first day
    for (var e = 0; e < firstDay; e++) {
        html += '<div class="calendar-cell no-trade"></div>';
    }

    for (var day = 1; day <= daysInMonth; day++) {
        var dateStr = year + '-' + String(month + 1).padStart(2, '0') + '-' + String(day).padStart(2, '0');
        var pnl   = dailyPnl[dateStr];
        var trades = dailyTrades[dateStr] || [];
        var isToday = dateStr === todayStr;

        var cls = 'calendar-cell';
        var pnlText = '';

        if (pnl === undefined || pnl === null) {
            cls += ' no-trade';
        } else if (Math.abs(pnl) < 0.001) {
            cls += ' neutral';
        } else if (pnl > 0) {
            // 盈亏深度分级 (替换原有的 profit/loss 单一类名)
            var pnlRatio = Math.abs(pnl) / (accountData ? accountData.capital || 10000 : 10000);
            var pnlClass = pnlRatio > 0.02 ? 'profit-high' : pnlRatio > 0.005 ? 'profit-medium' : 'profit-low';
            cls += ' ' + pnlClass;
            pnlText = '+' + pnl.toFixed(0);
        } else {
            var pnlRatio = Math.abs(pnl) / (accountData ? accountData.capital || 10000 : 10000);
            var pnlClass = pnlRatio > 0.02 ? 'loss-high' : pnlRatio > 0.005 ? 'loss-medium' : 'loss-low';
            cls += ' ' + pnlClass;
            pnlText = pnl.toFixed(0);
        }

        if (isToday) cls += ' today';

        html += '<div class="' + cls + '" onclick="showDayTrades(\'' + dateStr + '\')" title="' + dateStr + ': ' + (pnlText || '\u65e0\u4ea4\u6613') + ' (' + trades.length + '\u7b14)">' +
            '<span class="day-num">' + day + '</span>' +
            (pnlText ? '<span class="day-pnl">' + pnlText + '</span>' : '') +
        '</div>';
    }

    container.innerHTML = html;
}

function changeCalendarMonth(delta) {
    _calendarMonth += delta;
    if (_calendarMonth < 0) {
        _calendarMonth = 11;
        _calendarYear--;
    } else if (_calendarMonth > 11) {
        _calendarMonth = 0;
        _calendarYear++;
    }
    loadPnLCalendar();
}

/* ==========================================================================
 * Day trade detail (bottom-sheet modal)
 * ========================================================================== */

function showDayTrades(dateStr) {
    if (!dateStr) return;

    (async function() {
        try {
            var data = await API.get('/api/trade/history?limit=500');
            var allTrades = (data && Array.isArray(data.trades)) ? data.trades : [];

            // Filter by date
            var dayTrades = [];
            for (var i = 0; i < allTrades.length; i++) {
                var t = allTrades[i];
                if (!t) continue;
                var tStr = _toDateStr(t.timestamp);
                if (tStr === dateStr) dayTrades.push(t);
            }

            var totalPnl = 0;
            for (var j = 0; j < dayTrades.length; j++) {
                totalPnl += _or(dayTrades[j].pnl, 0);
            }

            var pnlCls = totalPnl >= 0 ? 'var(--green)' : 'var(--red)';

            var html = '<div style="padding:16px;">' +
                '<h3 style="margin-bottom:12px;">\ud83d\udcc5 ' + dateStr + ' \u4ea4\u6613\u8be6\u60c5</h3>' + // 交易详情
                '<div style="font-size:16px;font-weight:700;color:' + pnlCls + ';margin-bottom:16px;">' +
                    '\u5f53\u65e5\u603b\u76c8\u4e8f: ' + (totalPnl >= 0 ? '+' : '') + totalPnl.toFixed(2) + ' USDT (' + dayTrades.length + '\u7b14)' + // 当日总盈亏 / 笔
                '</div>';

            if (dayTrades.length === 0) {
                html += '<div style="color:var(--text-muted);text-align:center;padding:20px;">\u5f53\u65e5\u65e0\u4ea4\u6613</div>'; // 当日无交易
            } else {
                html += '<div class="table-container"><table class="data-table"><thead><tr><th>\u65f6\u95f4</th><th>\u4ea4\u6613\u5bf9</th><th>\u65b9\u5411</th><th>\u76c8\u4e8f</th></tr></thead><tbody>';
                for (var k = 0; k < dayTrades.length; k++) {
                    var t2 = dayTrades[k];
                    var pnlC = (_or(t2.pnl, 0)) >= 0 ? 'var(--green)' : 'var(--red)';
                    html += '<tr>' +
                        '<td>' + fmtTime(t2.timestamp) + '</td>' +
                        '<td>' + escHtml(t2.symbol || '--') + '</td>' +
                        '<td>' + escHtml(t2.side || '--') + '</td>' +
                        '<td style="color:' + pnlC + ';font-weight:600;">' + ((_or(t2.pnl, 0)) >= 0 ? '+' : '') + (_or(t2.pnl, 0)).toFixed(2) + '</td>' +
                    '</tr>';
                }
                html += '</tbody></table></div>';
            }
            html += '<button class="btn btn-primary btn-block" style="margin-top:12px;" onclick="this.closest(\'.signal-detail-overlay\').remove()">\u5173\u95ed</button></div>'; // 关闭

            var overlay = document.createElement('div');
            overlay.className = 'signal-detail-overlay';
            overlay.innerHTML = '<div class="signal-detail-sheet">' + html + '</div>';
            document.body.appendChild(overlay);

            overlay.addEventListener('click', function(e) {
                if (e.target === overlay) {
                    try { overlay.remove(); } catch (_) { /* ignore */ }
                }
            });

            requestAnimationFrame(function() {
                var sheet = overlay.querySelector('.signal-detail-sheet');
                if (sheet) sheet.style.transform = 'translateY(0)';
            });
        } catch (e) {
            console.error('Failed to load day trades:', e);
        }
    })();
}

/* ==========================================================================
 * Landscape fullscreen K-line
 * ========================================================================== */

var _isLandscape = false;

function handleLandscapeChange(e) {
    var dashboard = safeGet('#page-dashboard');
    if (!dashboard || !dashboard.classList.contains('active')) return;

    if (e.matches) {
        _isLandscape = true;
        enterLandscapeFullscreen();
    } else {
        _isLandscape = false;
        exitLandscapeFullscreen();
    }
}

function enterLandscapeFullscreen() {
    var dashboard = safeGet('#page-dashboard');
    if (!dashboard) return;

    dashboard.classList.add('landscape-fullscreen');

    // Add exit button if not exists
    var exitBtn = safeGet('#landscape-exit-btn');
    if (!exitBtn) {
        exitBtn = document.createElement('button');
        exitBtn.id = 'landscape-exit-btn';
        exitBtn.className = 'landscape-exit-btn';
        exitBtn.textContent = '\u2715 \u9000\u51fa\u5168\u5c4f'; // 退出全屏
        exitBtn.addEventListener('click', exitLandscapeFullscreen);
        var klineContainer = dashboard.querySelector('.chart-container.large');
        if (klineContainer) {
            klineContainer.style.position = 'relative';
            klineContainer.appendChild(exitBtn);
        }
    }

    // Resize kline chart
    if (klineChart) {
        var container = safeGet('#kline-chart');
        if (container) {
            try {
                klineChart.applyOptions({
                    width: window.innerWidth,
                    height: window.innerHeight - 50,
                });
            } catch (_) { /* ignore */ }
        }
    }
}

function exitLandscapeFullscreen() {
    var dashboard = safeGet('#page-dashboard');
    if (!dashboard) return;

    dashboard.classList.remove('landscape-fullscreen');

    // Remove exit button
    var exitBtn = safeGet('#landscape-exit-btn');
    if (exitBtn) {
        try { exitBtn.remove(); } catch (_) { /* ignore */ }
    }

    // Restore kline chart size
    if (klineChart) {
        var container = safeGet('#kline-chart');
        if (container) {
            try {
                klineChart.applyOptions({
                    width: container.clientWidth,
                    height: 400,
                });
            } catch (_) { /* ignore */ }
        }
    }
}

// Listen for orientation changes
var landscapeQuery = window.matchMedia('(orientation: landscape)');
if (landscapeQuery && typeof landscapeQuery.addEventListener === 'function') {
    landscapeQuery.addEventListener('change', handleLandscapeChange);
}

// ── refreshDashboard override: also load calendar & check landscape ──
var _origRefreshDash = refreshDashboard;
refreshDashboard = function() {
    var result = _origRefreshDash();
    loadPnLCalendar();
    if (landscapeQuery && landscapeQuery.matches) {
        enterLandscapeFullscreen();
    }
    return result;
};

// ── Extend resize handler for landscape ──
var _landscapeResizeAdded = false;
var _origEnsureResize = _ensureResizeHandler;
_ensureResizeHandler = function() {
    _origEnsureResize();
    if (_landscapeResizeAdded) return;
    _landscapeResizeAdded = true;
    window.addEventListener('resize', function() {
        if (!_isLandscape || !klineChart) return;
        var container = safeGet('#kline-chart');
        if (container) {
            try {
                klineChart.applyOptions({
                    width: window.innerWidth,
                    height: window.innerHeight - 50,
                });
            } catch (_) { /* ignore */ }
        }
    });
};
