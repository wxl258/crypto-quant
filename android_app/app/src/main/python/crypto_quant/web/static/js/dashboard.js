/**
 * Dashboard - Account overview, K-line chart, positions
 */
'use strict';

let klineChart = null;
let candleSeries = null;
let volumeSeries = null;
let sma20Series = null;
let sma50Series = null;
let _currentSymbol = null;
let _currentInterval = null;
let equityChart = null;

async function refreshDashboard() {
    await Promise.all([
        loadAccount(),
        loadKlineChart(),
        loadEquityChart(),
    ]);
}

async function loadAccount() {
    try {
        const data = await API.get('/api/account');
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

        // Positions table
        const tbody = document.querySelector('#positions-table tbody');
        if (!tbody) return;

        if (!data.positions || data.positions.length === 0) {
            if (typeof renderEmptyPositions === 'function') {
                renderEmptyPositions();
            } else {
                tbody.textContent = '';
                const emptyRow = document.createElement('tr');
                emptyRow.className = 'empty-row';
                const emptyTd = document.createElement('td');
                emptyTd.colSpan = 8;
                emptyTd.textContent = '暂无持仓';
                emptyRow.appendChild(emptyTd);
                tbody.appendChild(emptyRow);
            }
            return;
        }

        tbody.textContent = '';
        data.positions.forEach(p => {
            const upnl = p.unrealized_pnl || 0;
            const cls = upnl >= 0 ? 'positive' : 'negative';
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${escHtml(p.symbol)}</strong></td>
                <td><span class="${p.side === 'LONG' ? 'positive' : 'negative'}">${p.side === 'LONG' ? '做多' : '做空'}</span></td>
                <td>${fmtUSD(p.entry_price)}</td>
                <td>${fmtUSD(p.current_price)}</td>
                <td>${p.quantity?.toFixed(4) || '--'}</td>
                <td>${p.leverage || 3}x</td>
                <td class="${cls}">${fmtUSD(upnl)}</td>
                <td></td>`;
            const btn = document.createElement('button');
            btn.className = 'btn-sm-card danger';
            btn.textContent = '平仓';
            btn.addEventListener('click', () => closePosition(p.symbol));
            const lastTd = tr.querySelector('td:last-child');
            if (lastTd) lastTd.appendChild(btn);
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed to load account:', e);
    }
}

async function closePosition(symbol) {
    if (!confirm(`确认平仓 ${symbol}？`)) return;
    try {
        await API.post('/api/trade/close', { symbol });
        await loadAccount();
    } catch (e) {
        showToast('平仓失败: ' + friendlyError(e.message), 'error');
    }
}

// K-line Chart
let _klineDebounceTimer = null;
let _lastKlineSymbol = null;
let _lastKlineInterval = null;

async function loadKlineChart() {
    const symbol = document.getElementById('dashboard-symbol')?.value || 'BTCUSDT';
    const interval = document.querySelector('.chart-controls .btn-sm.active')?.dataset?.interval || '1h';

    // Debounce: skip if same symbol+interval requested within 200ms
    const key = `${symbol}:${interval}`;
    if (key === _lastKlineSymbol + ':' + _lastKlineInterval && _klineDebounceTimer) {
        return;
    }

    _lastKlineSymbol = symbol;
    _lastKlineInterval = interval;

    if (_klineDebounceTimer) clearTimeout(_klineDebounceTimer);

    return new Promise((resolve) => {
        _klineDebounceTimer = setTimeout(async () => {
            _klineDebounceTimer = null;
            try {
                await _loadKlineChartInner(symbol, interval);
            } finally {
                resolve();
            }
        }, 200);
    });
}

async function _loadKlineChartInner(symbol, interval) {
    try {
        const data = await API.get(`/api/market/klines?symbol=${symbol}&interval=${interval}&limit=200`);
        if (!data || data.length === 0) return;

        const container = document.getElementById('kline-chart');
        if (!container) return;
        const symbolOrIntervalChanged = (symbol !== _currentSymbol || interval !== _currentInterval);

        // If chart exists and symbol/interval unchanged, just update data
        if (klineChart && !symbolOrIntervalChanged) {
            const candleData = data.map(d => ({
                time: d.timestamp / 1000,
                open: d.open,
                high: d.high,
                low: d.low,
                close: d.close,
            }));
            const volumeData = data.map(d => ({
                time: d.timestamp / 1000,
                value: d.volume,
                color: d.close >= d.open ? 'rgba(76,175,132,0.4)' : 'rgba(239,83,80,0.4)',
            }));

            if (candleSeries) candleSeries.setData(candleData);
            if (volumeSeries) volumeSeries.setData(volumeData);

            // Update SMA lines
            const closePrices = data.map(d => d.close);
            const sma20 = calcSMA(closePrices, 20);
            const sma50 = calcSMA(closePrices, 50);
            if (sma20Series) sma20Series.setData(data.map((d, i) => ({ time: d.timestamp / 1000, value: sma20[i] })).filter(d => d.value));
            if (sma50Series) sma50Series.setData(data.map((d, i) => ({ time: d.timestamp / 1000, value: sma50[i] })).filter(d => d.value));
            return;
        }

        // Destroy and recreate on symbol/interval change or first load
        if (klineChart) {
            klineChart.remove();
            klineChart = null;
        }

        _currentSymbol = symbol;
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
            width: container.clientWidth,
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

        const candleData = data.map(d => ({
            time: d.timestamp / 1000,
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
        }));

        const volumeData = data.map(d => ({
            time: d.timestamp / 1000,
            value: d.volume,
            color: d.close >= d.open ? 'rgba(76,175,132,0.4)' : 'rgba(239,83,80,0.4)',
        }));

        candleSeries.setData(candleData);
        volumeSeries.setData(volumeData);

        // SMA lines
        const closePrices = data.map(d => d.close);
        const sma20 = calcSMA(closePrices, 20);
        const sma50 = calcSMA(closePrices, 50);

        sma20Series = klineChart.addLineSeries({
            color: '#ffa726',
            lineWidth: 1,
        });
        sma50Series = klineChart.addLineSeries({
            color: '#7c4dff',
            lineWidth: 1,
        });

        sma20Series.setData(data.map((d, i) => ({ time: d.timestamp / 1000, value: sma20[i] })).filter(d => d.value !== null));
        sma50Series.setData(data.map((d, i) => ({ time: d.timestamp / 1000, value: sma50[i] })).filter(d => d.value !== null));
    } catch (e) {
        console.error('Failed to load kline:', e);
    }
}

// Register resize handler once, not on every chart refresh
let _resizeRegistered = false;
function _ensureResizeHandler() {
    if (_resizeRegistered) return;
    _resizeRegistered = true;
    window.addEventListener('resize', () => {
        const container = document.getElementById('kline-chart');
        if (klineChart && container) {
            klineChart.applyOptions({ width: container.clientWidth });
        }
    });
}
// Call once on first chart load
const _origLoadKline = loadKlineChart;
loadKlineChart = async function() {
    _ensureResizeHandler();
    return _origLoadKline();
};

function calcSMA(data, period) {
    const result = new Array(data.length).fill(null);
    for (let i = period - 1; i < data.length; i++) {
        let sum = 0;
        for (let j = 0; j < period; j++) sum += data[i - j];
        result[i] = sum / period;
    }
    return result;
}

// Equity Curve Chart — loads real equity data from account history
async function loadEquityChart() {
    try {
        const account = await API.get('/api/account');
        if (!account) return;
        const history = await API.get('/api/trade/history?limit=200');

        const canvas = document.getElementById('equity-chart');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        if (equityChart) equityChart.destroy();

        const points = buildEquityCurve(account, (history && history.trades) || []);
        const labels = points.map(p => p.label);
        const values = points.map(p => p.equity);

        equityChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: '权益',
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
                        ticks: { color: '#8b8fa8', callback: v => '$' + v.toLocaleString() },
                        grid: { color: 'rgba(46,49,80,0.3)' },
                    },
                },
            },
        });
    } catch (e) {
        console.error('Failed to load equity chart:', e);
    }
}

function buildEquityCurve(account, trades) {
    const initialCapital = account.initial_capital || 10000;
    const points = [];
    let equity = initialCapital;

    // Sort trades chronologically (oldest first)
    const sorted = [...trades].sort((a, b) =>
        new Date(a.timestamp) - new Date(b.timestamp)
    );

    // Start point
    if (sorted.length > 0) {
        points.push({
            label: new Date(sorted[0].timestamp).toLocaleDateString(),
            equity: initialCapital,
        });
    }

    for (const t of sorted) {
        if (t.side === 'CLOSE') {
            equity += (t.pnl || 0);
            points.push({
                label: new Date(t.timestamp).toLocaleDateString(),
                equity: Math.round(equity * 100) / 100,
            });
        }
    }

    // Current equity as final point
    const currentEquity = account.total_equity || equity;
    if (points.length === 0 || points[points.length - 1].equity !== currentEquity) {
        points.push({
            label: new Date().toLocaleDateString(),
            equity: currentEquity,
        });
    }

    // Ensure at least 2 points for a line
    if (points.length < 2) {
        points.unshift({ label: '', equity: initialCapital });
    }

    return points;
}

// ── Deferred initialization for non-viewport charts ──
function initCharts() {
    // Interval buttons
    document.querySelectorAll('.chart-controls .btn-sm').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.chart-controls .btn-sm').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadKlineChart();
        });
    });

    // Symbol selector
    document.getElementById('dashboard-symbol')?.addEventListener('change', () => {
        loadKlineChart();
    });
}

if ('requestIdleCallback' in window) {
    requestIdleCallback(() => initCharts());
} else {
    setTimeout(initCharts, 200);
}

// ── PnL Calendar ──
let _calendarYear = new Date().getFullYear();
let _calendarMonth = new Date().getMonth(); // 0-based

async function loadPnLCalendar() {
    try {
        const data = await API.get('/api/trade/history?limit=500');
        const trades = data.trades || [];

        // Aggregate PnL by date
        const dailyPnl = {};
        const dailyTrades = {};
        for (const t of trades) {
            const d = t.timestamp ? t.timestamp.split('T')[0] : null;
            if (!d) continue;
            dailyPnl[d] = (dailyPnl[d] || 0) + (t.pnl || 0);
            if (!dailyTrades[d]) dailyTrades[d] = [];
            dailyTrades[d].push(t);
        }

        renderCalendar(dailyPnl, dailyTrades);
    } catch (e) {
        console.error('Failed to load PnL calendar:', e);
    }
}

function renderCalendar(dailyPnl, dailyTrades) {
    const container = document.getElementById('pnl-calendar');
    if (!container) return;

    const year = _calendarYear;
    const month = _calendarMonth;

    // Update month label
    document.getElementById('calendar-month-label').textContent =
        `${year}年${month + 1}月`;

    // Find max abs PnL for color scaling
    const pnlValues = Object.values(dailyPnl).map(Math.abs);
    const maxAbsPnl = pnlValues.length > 0 ? Math.max(...pnlValues) : 0;

    // Weekday headers
    const weekdays = ['日', '一', '二', '三', '四', '五', '六'];
    let html = weekdays.map(d => `<div class="calendar-weekday">${d}</div>`).join('');

    // First day of month
    const firstDay = new Date(year, month, 1).getDay(); // 0=Sunday
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    // Today
    const today = new Date();
    const todayStr = today.toISOString().split('T')[0];

    // Fill empty cells before first day
    for (let i = 0; i < firstDay; i++) {
        html += '<div class="calendar-cell no-trade"></div>';
    }

    for (let day = 1; day <= daysInMonth; day++) {
        const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        const pnl = dailyPnl[dateStr];
        const trades = dailyTrades[dateStr] || [];
        const isToday = dateStr === todayStr;

        let cls = 'calendar-cell';
        let pnlText = '';

        if (pnl === undefined || pnl === null) {
            cls += ' no-trade';
        } else if (Math.abs(pnl) < 0.001) {
            cls += ' neutral';
        } else if (pnl > 0) {
            cls += maxAbsPnl > 0 && pnl / maxAbsPnl > 0.5 ? ' profit-high' : ' profit';
            pnlText = `+${pnl.toFixed(0)}`;
        } else {
            cls += maxAbsPnl > 0 && Math.abs(pnl) / maxAbsPnl > 0.5 ? ' loss-high' : ' loss';
            pnlText = pnl.toFixed(0);
        }

        if (isToday) cls += ' today';

        html += `<div class="${cls}" onclick="showDayTrades('${dateStr}')" title="${dateStr}: ${pnlText || '无交易'} (${trades.length}笔)">
            <span class="day-num">${day}</span>
            ${pnlText ? `<span class="day-pnl">${pnlText}</span>` : ''}
        </div>`;
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

function showDayTrades(dateStr) {
    // Fetch trades for that day and show in a modal
    (async () => {
        try {
            const data = await API.get('/api/trade/history?limit=500');
            const dayTrades = (data.trades || []).filter(t =>
                t.timestamp && t.timestamp.startsWith(dateStr)
            );

            const totalPnl = dayTrades.reduce((sum, t) => sum + (t.pnl || 0), 0);
            const pnlCls = totalPnl >= 0 ? 'var(--green)' : 'var(--red)';

            let html = `<div style="padding:16px;">
                <h3 style="margin-bottom:12px;">📅 ${dateStr} 交易详情</h3>
                <div style="font-size:16px;font-weight:700;color:${pnlCls};margin-bottom:16px;">
                    当日总盈亏: ${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)} USDT (${dayTrades.length}笔)
                </div>`;

            if (dayTrades.length === 0) {
                html += '<div style="color:var(--text-muted);text-align:center;padding:20px;">当日无交易</div>';
            } else {
                html += '<div class="table-container"><table class="data-table"><thead><tr><th>时间</th><th>交易对</th><th>方向</th><th>盈亏</th></tr></thead><tbody>';
                for (const t of dayTrades) {
                    const pnlC = (t.pnl || 0) >= 0 ? 'var(--green)' : 'var(--red)';
                    html += `<tr>
                        <td>${fmtTime(t.timestamp)}</td>
                        <td>${escHtml(t.symbol || '--')}</td>
                        <td>${escHtml(t.side || '--')}</td>
                        <td style="color:${pnlC};font-weight:600;">${(t.pnl || 0) >= 0 ? '+' : ''}${(t.pnl || 0).toFixed(2)}</td>
                    </tr>`;
                }
                html += '</tbody></table></div>';
            }
            html += '<button class="btn btn-primary btn-block" style="margin-top:12px;" onclick="this.closest(\'.signal-detail-overlay\').remove()">关闭</button></div>';

            // Reuse signal detail overlay pattern
            const overlay = document.createElement('div');
            overlay.className = 'signal-detail-overlay';
            overlay.innerHTML = `<div class="signal-detail-sheet">${html}</div>`;
            document.body.appendChild(overlay);
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) overlay.remove();
            });
            requestAnimationFrame(() => {
                const sheet = overlay.querySelector('.signal-detail-sheet');
                if (sheet) sheet.style.transform = 'translateY(0)';
            });
        } catch (e) {
            console.error('Failed to load day trades:', e);
        }
    })();
}

// ── Landscape Fullscreen K-line ──
let _isLandscape = false;

function handleLandscapeChange(e) {
    const dashboard = document.getElementById('page-dashboard');
    if (!dashboard || !dashboard.classList.contains('active')) return;

    if (e.matches) {
        // Enter landscape mode
        _isLandscape = true;
        enterLandscapeFullscreen();
    } else {
        // Exit landscape mode
        _isLandscape = false;
        exitLandscapeFullscreen();
    }
}

function enterLandscapeFullscreen() {
    const dashboard = document.getElementById('page-dashboard');
    if (!dashboard) return;

    dashboard.classList.add('landscape-fullscreen');

    // Add exit button if not exists
    let exitBtn = document.getElementById('landscape-exit-btn');
    if (!exitBtn) {
        exitBtn = document.createElement('button');
        exitBtn.id = 'landscape-exit-btn';
        exitBtn.className = 'landscape-exit-btn';
        exitBtn.textContent = '✕ 退出全屏';
        exitBtn.addEventListener('click', exitLandscapeFullscreen);
        const klineContainer = dashboard.querySelector('.chart-container.large');
        if (klineContainer) {
            klineContainer.style.position = 'relative';
            klineContainer.appendChild(exitBtn);
        }
    }

    // Resize kline chart
    if (klineChart) {
        const container = document.getElementById('kline-chart');
        if (container) {
            klineChart.applyOptions({
                width: window.innerWidth,
                height: window.innerHeight - 50,
            });
        }
    }
}

function exitLandscapeFullscreen() {
    const dashboard = document.getElementById('page-dashboard');
    if (!dashboard) return;

    dashboard.classList.remove('landscape-fullscreen');

    // Remove exit button
    const exitBtn = document.getElementById('landscape-exit-btn');
    if (exitBtn) exitBtn.remove();

    // Restore kline chart size
    if (klineChart) {
        const container = document.getElementById('kline-chart');
        if (container) {
            klineChart.applyOptions({
                width: container.clientWidth,
                height: 400,
            });
        }
    }
}

// Listen for orientation changes
const landscapeQuery = window.matchMedia('(orientation: landscape)');
landscapeQuery.addEventListener('change', handleLandscapeChange);

// Also check on page navigation to dashboard
const _origRefreshDash = refreshDashboard;
refreshDashboard = async function() {
    const result = await _origRefreshDash();
    // Load calendar after dashboard refresh
    loadPnLCalendar();
    // Check landscape state
    if (landscapeQuery.matches) {
        enterLandscapeFullscreen();
    }
    return result;
};

// Update resize handler to account for landscape mode
let _landscapeResizeAdded = false;
const _origEnsureResize = _ensureResizeHandler;
_ensureResizeHandler = function() {
    _origEnsureResize();
    if (_landscapeResizeAdded) return;
    _landscapeResizeAdded = true;
    window.addEventListener('resize', () => {
        if (_isLandscape && klineChart) {
            const container = document.getElementById('kline-chart');
            if (container) {
                klineChart.applyOptions({
                    width: window.innerWidth,
                    height: window.innerHeight - 50,
                });
            }
        }
    });
};
