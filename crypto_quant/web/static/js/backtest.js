/**
 * Backtest Module
 */
'use strict';

let btEquityChart = null;

function initBacktestPage() {
    updateBacktestParams();
}

document.getElementById('bt-strategy')?.addEventListener('change', updateBacktestParams);

// Cache strategy defs (shared with strategy.js via global _strategyDefs)
async function updateBacktestParams() {
    const name = document.getElementById('bt-strategy')?.value || 'dual_ma';
    const container = document.getElementById('bt-params');

    // Fetch from API if not already cached
    if (!window._strategyDefs || !window._strategyDefs[name]) {
        try {
            const info = await API.get(`/api/strategies/${name}`);
            if (!window._strategyDefs) window._strategyDefs = {};
            window._strategyDefs[name] = info;
        } catch (e) {
            console.error('Failed to load params:', e);
            container.innerHTML = '';
            return;
        }
    }

    const params = (window._strategyDefs[name] && window._strategyDefs[name].parameters) || [];
    const guide = (typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[name]) || null;

    container.innerHTML = `
        <h4 style="margin-bottom:10px;color:var(--text-muted);font-size:12px;">策略参数</h4>
        ${params.map(p => {
            const guideParam = guide && guide.params ? guide.params[p.name] : null;
            const paramName = guideParam ? guideParam.name : p.label;
            const paramDesc = guideParam ? guideParam.desc : '';
            const step = p.step || (p.type === 'float' ? '0.01' : '1');
            return `
            <div class="form-group">
                <label>${escHtml(paramName)}</label>
                ${paramDesc ? `<div class="param-hint">${escHtml(paramDesc)}</div>` : ''}
                <input type="number" id="bt-param-${p.name}" class="form-input"
                       value="${p.default}" step="${step}"
                       min="${p.min || ''}" max="${p.max || ''}">
            </div>`;
        }).join('')}
    `;
}

async function runBacktest() {
    const strategy = document.getElementById('bt-strategy').value;
    const symbol = document.getElementById('bt-symbol').value;
    const interval = document.getElementById('bt-interval').value;
    const capital = parseFloat(document.getElementById('bt-capital').value) || 10000;
    const days = parseInt(document.getElementById('bt-days').value) || 90;

    // Collect params
    const params = {};
    document.querySelectorAll('#bt-params input').forEach(input => {
        const name = input.id.replace('bt-param-', '');
        const num = parseFloat(input.value);
        params[name] = isNaN(num) ? input.value : num;
    });

    const btn = document.querySelector('#page-backtest .btn-primary');
    btn.textContent = '⏳ 回测中...';
    btn.disabled = true;

    // Read date range (if set, overrides days)
    const dateStart = document.getElementById('bt-date-start')?.value || null;
    const dateEnd = document.getElementById('bt-date-end')?.value || null;

    try {
        const payload = { strategy, symbol, interval, initial_capital: capital, params };
        if (dateStart || dateEnd) {
            payload.date_start = dateStart;
            payload.date_end = dateEnd;
        } else {
            payload.days = days;
        }
        const result = await API.post('/api/backtest', payload);

        displayBacktestResults(result);
    } catch (e) {
        showToast('回测失败: ' + friendlyError(e.message), 'error');
    } finally {
        btn.textContent = '🚀 开始回测';
        btn.disabled = false;
    }
}

function displayBacktestResults(result) {
    const m = result.metrics;

    // Update date range info
    const dateInfo = document.getElementById('bt-date-info');
    if (dateInfo && result.date_start) {
        dateInfo.textContent = `${result.date_start} → ${result.date_end} (${result.candles?.toLocaleString() || '?'} 根K线)`;
        dateInfo.style.display = 'block';
    }

    // Show export buttons
    const exportDiv = document.getElementById('bt-export');
    if (exportDiv) exportDiv.style.display = 'block';

    // Store last backtest params for export
    window._lastBtParams = getBacktestPayload();

    // Update metrics
    document.getElementById('m-total').textContent = fmtPct(m.total_return);
    document.getElementById('m-total').className = `metric-value ${m.total_return >= 0 ? 'positive' : 'negative'}`;

    document.getElementById('m-annual').textContent = fmtPct(m.annual_return);
    document.getElementById('m-annual').className = `metric-value ${m.annual_return >= 0 ? 'positive' : 'negative'}`;

    document.getElementById('m-sharpe').textContent = m.sharpe_ratio?.toFixed(2) || '--';
    document.getElementById('m-dd').textContent = fmtPct(m.max_drawdown);
    document.getElementById('m-winrate').textContent = fmtPct(m.win_rate);
    document.getElementById('m-pf').textContent = m.profit_factor?.toFixed(2) || '--';
    document.getElementById('m-calmar').textContent = m.calmar_ratio?.toFixed(2) || '--';
    document.getElementById('m-trades').textContent = m.total_trades || 0;

    // Equity curve chart
    drawEquityCurve(result.equity_curve);

    // Trades table
    const tbody = document.querySelector('#bt-trades-table tbody');
    if (!tbody) return;
    tbody.textContent = '';

    if (!result.trades || result.trades.length === 0) {
        const row = document.createElement('tr');
        row.className = 'empty-row';
        const td = document.createElement('td');
        td.colSpan = 6;
        td.textContent = '无交易记录';
        row.appendChild(td);
        tbody.appendChild(row);
    } else {
        result.trades.forEach(t => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${fmtTime(t.entry_time)}</td>
                <td><span class="${t.side === 'LONG' ? 'positive' : 'negative'}">${escHtml(t.side)}</span></td>
                <td>${fmtUSD(t.entry_price)}</td>
                <td>${fmtUSD(t.exit_price)}</td>
                <td class="${t.pnl >= 0 ? 'positive' : 'negative'}">${fmtUSD(t.pnl)}</td>
                <td class="${t.pnl_pct >= 0 ? 'positive' : 'negative'}">${fmtPct(t.pnl_pct)}</td>`;
            tbody.appendChild(tr);
        });
    }
}

function drawEquityCurve(data) {
    const ctx = document.getElementById('bt-equity-chart');
    if (!ctx) return;
    const canvasCtx = ctx.getContext('2d');

    if (btEquityChart) btEquityChart.destroy();

    const labels = data.map(d => new Date(d.timestamp).toLocaleDateString());
    const values = data.map(d => d.equity);

    btEquityChart = new Chart(canvasCtx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: '权益曲线',
                data: values,
                borderColor: '#4fc3f7',
                backgroundColor: 'rgba(79,195,247,0.1)',
                fill: true,
                tension: 0.2,
                pointRadius: 0,
                borderWidth: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: true, labels: { color: '#8b8fa8' } },
                tooltip: {
                    callbacks: {
                        label: ctx => `权益: $${ctx.raw.toFixed(2)}`,
                    },
                },
            },
            scales: {
                x: {
                    ticks: { color: '#5c6080', maxTicksLimit: 10 },
                    grid: { display: false },
                },
                y: {
                    ticks: { color: '#8b8fa8', callback: v => '$' + v.toLocaleString() },
                    grid: { color: 'rgba(46,49,80,0.3)' },
                },
            },
        },
    });
}

// Refresh trades page
async function refreshTrades() {
    try {
        const data = await API.get('/api/trade/history?limit=50');
        const tbody = document.querySelector('#trades-history-table tbody');
        if (!tbody) return;
        tbody.textContent = '';

        if (!data.trades || data.trades.length === 0) {
            const row = document.createElement('tr');
            row.className = 'empty-row';
            const td = document.createElement('td');
            td.colSpan = 8;
            td.textContent = '暂无交易记录';
            row.appendChild(td);
            tbody.appendChild(row);
            return;
        }

        data.trades.reverse().forEach(t => {
            const tr = document.createElement('tr');
            const pnlCls = (t.pnl || 0) >= 0 ? 'positive' : 'negative';
            tr.innerHTML = `
                <td>${fmtTime(t.timestamp)}</td>
                <td><strong>${escHtml(t.symbol)}</strong></td>
                <td>${escHtml(t.side)}</td>
                <td>${fmtUSD(t.entry_price)}</td>
                <td>${fmtUSD(t.price)}</td>
                <td class="${pnlCls}">${fmtUSD(t.pnl)}</td>
                <td>${escHtml(t.reason || '--')}</td>
                <td><span style="cursor:pointer;font-size:16px;" onclick="showNoteEditor(${t.id || 0}, '${escHtml((t.notes || '').replace(/'/g, "\\'"))}')" title="${escHtml(t.notes || '添加笔记')}">${t.notes ? '📝' : '➕'}</span></td>`;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed to load trades:', e);
    }
}

// Refresh risk page
async function refreshRisk() {
    try {
        const data = await API.get('/api/risk/summary');
        document.getElementById('risk-losses').textContent = data.consecutive_losses || 0;
        document.getElementById('risk-daily-pnl').textContent = fmtUSD(data.daily_pnl);
        document.getElementById('risk-exposure').textContent = fmtPct(data.total_exposure_pct);

        const indicator = document.querySelector('.risk-indicator');
        if (data.trading_paused) {
            indicator.className = 'risk-indicator danger';
            indicator.textContent = '⛔ 交易已暂停: ' + (data.pause_reason || '');
        } else if (data.consecutive_losses >= 2) {
            indicator.className = 'risk-indicator warning';
            indicator.textContent = '⚠️ 连续亏损预警';
        } else {
            indicator.className = 'risk-indicator safe';
            indicator.textContent = '✅ 风控正常';
        }

        // 更新止损保护状态指示器
        updateSafetyShield();
    } catch (e) {
        console.error('Failed to load risk:', e);
    }
}

async function updateRiskLimits() {
    const limits = {
        max_position_pct: parseFloat(document.getElementById('risk-max-pos').value) / 100,
        max_daily_loss_pct: parseFloat(document.getElementById('risk-daily-loss').value) / 100,
        stop_loss_pct: parseFloat(document.getElementById('risk-sl').value) / 100,
        take_profit_pct: parseFloat(document.getElementById('risk-tp').value) / 100,
    };

    try {
        await API.post('/api/risk/limits', limits);
        showToast('风控参数已更新', 'success');
        // 保存后更新止损保护状态
        updateSafetyShield();
    } catch (e) {
        showToast('更新失败: ' + friendlyError(e.message), 'error');
    }
}

// ── Export helpers ──

function getBacktestPayload() {
    const params = {};
    document.querySelectorAll('#bt-params input').forEach(input => {
        const name = input.id.replace('bt-param-', '');
        const num = parseFloat(input.value);
        params[name] = isNaN(num) ? input.value : num;
    });
    const payload = {
        strategy: document.getElementById('bt-strategy').value,
        symbol: document.getElementById('bt-symbol').value,
        interval: document.getElementById('bt-interval').value,
        initial_capital: parseFloat(document.getElementById('bt-capital').value) || 10000,
        params,
    };
    const dateStart = document.getElementById('bt-date-start')?.value || null;
    const dateEnd = document.getElementById('bt-date-end')?.value || null;
    if (dateStart || dateEnd) {
        payload.date_start = dateStart;
        payload.date_end = dateEnd;
    } else {
        payload.days = parseInt(document.getElementById('bt-days').value) || 90;
    }
    return payload;
}

async function exportBacktestCSV() {
    const payload = window._lastBtParams || getBacktestPayload();
    try {
        const resp = await fetch('/api/backtest/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!resp.ok) {
            const err = await resp.json();
            showToast('导出失败: ' + friendlyError(err.detail || resp.statusText), 'error');
            return;
        }
        const blob = await resp.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const disposition = resp.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="?(.+?)"?$/);
        a.download = match ? match[1] : 'backtest.csv';
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
        showToast('CSV 已导出', 'success');
    } catch (e) {
        showToast('导出失败: ' + friendlyError(e.message), 'error');
    }
}

async function exportBacktestPDF() {
    const payload = window._lastBtParams || getBacktestPayload();
    try {
        const resp = await fetch('/api/backtest/export/pdf', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!resp.ok) {
            const err = await resp.json();
            showToast('导出失败: ' + friendlyError(err.detail || resp.statusText), 'error');
            return;
        }
        const blob = await resp.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const disposition = resp.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="?(.+?)"?$/);
        a.download = match ? match[1] : 'backtest.pdf';
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
        showToast('PDF 已导出', 'success');
    } catch (e) {
        showToast('导出失败: ' + friendlyError(e.message), 'error');
    }
}

// ── Compare All Strategies ──
async function runCompareAll() {
    const strategy = document.getElementById('bt-strategy').value;
    const symbol = document.getElementById('bt-symbol').value;
    const interval = document.getElementById('bt-interval').value;
    const capital = parseFloat(document.getElementById('bt-capital').value) || 10000;
    const dateStart = document.getElementById('bt-date-start')?.value || null;
    const dateEnd = document.getElementById('bt-date-end')?.value || null;

    const btn = document.querySelector('#page-backtest button[onclick="runCompareAll()"]');
    btn.textContent = '⏳ 对比中...';
    btn.disabled = true;

    try {
        const payload = { strategy, symbol, interval, initial_capital: capital, params: {} };
        if (dateStart || dateEnd) { payload.date_start = dateStart; payload.date_end = dateEnd; }
        else { payload.days = parseInt(document.getElementById('bt-days').value) || 90; }

        const result = await API.post('/api/backtest/compare', payload);

        // Show panel
        const panel = document.getElementById('bt-compare-panel');
        panel.style.display = 'block';
        document.getElementById('bt-compare-info').textContent =
            `${result.date_start} → ${result.date_end} (${result.candles?.toLocaleString()} 根K线)`;

        // Build table
        const tbody = document.querySelector('#bt-compare-table tbody');
        tbody.textContent = '';

        // Sort by sharpe desc
        const sorted = [...result.results].sort((a, b) => (b.sharpe_ratio || -999) - (a.sharpe_ratio || -999));

        sorted.forEach(r => {
            if (r.error) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td><strong>${escHtml(r.name)}</strong></td><td colspan="6" style="color:var(--red)">${escHtml(r.error)}</td>`;
                tbody.appendChild(tr);
                return;
            }
            const isBest = r === sorted[0];
            const tr = document.createElement('tr');
            if (isBest) tr.style.background = 'rgba(79,195,247,0.06)';
            const retCls = r.total_return >= 0 ? 'positive' : 'negative';
            tr.innerHTML = `
                <td><strong>${escHtml(r.name)}</strong>${isBest ? ' 🏆' : ''}</td>
                <td>${r.trades}</td>
                <td>${r.win_rate?.toFixed(1)}%</td>
                <td class="${retCls}">${r.total_return?.toFixed(2)}%</td>
                <td>${r.sharpe_ratio?.toFixed(2)}</td>
                <td class="negative">${r.max_drawdown?.toFixed(2)}%</td>
                <td>${r.profit_factor?.toFixed(2) || '--'}</td>`;
            tbody.appendChild(tr);
        });

        // Scroll to compare panel
        panel.scrollIntoView({ behavior: 'smooth' });
    } catch (e) {
        showToast('对比失败: ' + friendlyError(e.message), 'error');
    } finally {
        btn.textContent = '📊 全部策略对比';
        btn.disabled = false;
    }
}
