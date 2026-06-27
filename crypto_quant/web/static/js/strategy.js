/**
 * Strategy Management
 */
'use strict';

// Cache strategy definitions from API (on window for cross-module sharing)
window._strategyDefs = window._strategyDefs || {};

async function initStrategyPage() {
    await loadStrategies();
}

function getStrategyLabel(name) {
    // 优先使用 STRATEGY_GUIDE 中的中文名称
    if (typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[name] && STRATEGY_GUIDE[name].name) {
        return STRATEGY_GUIDE[name].name;
    }
    // Fallback to API definition
    const def = window._strategyDefs[name];
    if (def && def.description) {
        const firstLine = def.description.split('\n')[0].trim();
        if (/[\u4e00-\u9fff]/.test(firstLine)) return firstLine;
    }
    // Hardcoded fallback map
    const map = {
        'dual_ma': '双均线策略',
        'rsi_mean_reversion': 'RSI均值回归',
        'grid': '网格策略',
        'bollinger_bands': '布林带策略',
        'macd': 'MACD策略',
        'supertrend': '超级趋势',
        'turtle': '海龟交易',
    };
    return map[name] || name;
}

async function loadStrategies() {
    try {
        const data = await API.get('/api/strategies');
        // Cache defs for later use
        window._strategyDefs = {};
        for (const s of data.strategies) {
            window._strategyDefs[s.name] = s;
        }

        // Store strategies data for filtering
        window._allStrategies = data.strategies;
        renderStrategyCards(data.strategies);
    } catch (e) {
        console.error('Failed to load strategies:', e);
    }
}

function filterStrategies() {
    const searchTerm = (document.getElementById('strategy-search')?.value || '').toLowerCase();
    const difficultyFilter = document.getElementById('strategy-difficulty-filter')?.value || 'all';
    const typeFilter = document.getElementById('strategy-type-filter')?.value || 'all';

    const strategies = window._allStrategies || [];
    const filtered = strategies.filter(s => {
        const guide = (typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[s.name]) || null;
        const name = guide ? guide.name : getStrategyLabel(s.name);
        const desc = guide ? guide.description : (s.description || '');

        // Search filter
        if (searchTerm && !name.toLowerCase().includes(searchTerm) && !desc.toLowerCase().includes(searchTerm)) {
            return false;
        }

        // Difficulty filter
        if (difficultyFilter !== 'all' && (!guide || guide.difficulty !== difficultyFilter)) {
            return false;
        }

        // Type filter
        if (typeFilter !== 'all' && (!guide || guide.type !== typeFilter)) {
            return false;
        }

        return true;
    });

    renderStrategyCards(filtered);
}

function renderStrategyCards(strategies) {
    const grid = document.getElementById('strategies-list');
    if (!grid) return;

    if (strategies.length === 0) {
        grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--text-muted);padding:40px;">没有匹配的策略</div>';
        return;
    }

    grid.innerHTML = strategies.map(s => {
        const guide = (typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[s.name]) || null;
        const name = guide ? guide.name : getStrategyLabel(s.name);
        const icon = guide ? guide.icon : '📈';
        const description = guide ? guide.description : (s.description || '无描述');
        const difficulty = guide ? guide.difficulty : '';
        const diffColor = difficulty ? (DIFFICULTY_COLORS[difficulty] || '#888') : '#888';
        const tips = guide ? guide.tips : '';
        const suitable = guide ? guide.suitable : '';
        const unsuitable = guide ? guide.unsuitable : '';

        return `
        <div class="strategy-card">
            <div class="card-header">
                <span class="card-icon">${icon}</span>
                <div class="card-title-row">
                    <h3>${escHtml(name)}</h3>
                    ${difficulty ? `<span class="card-badge" style="background:${diffColor}20;color:${diffColor};border:1px solid ${diffColor}40">${difficulty}</span>` : ''}
                </div>
            </div>
            <p class="desc">${escHtml(description)}</p>
            ${suitable ? `<div class="card-tag tag-green">✅ 适合: ${escHtml(suitable)}</div>` : ''}
            ${unsuitable ? `<div class="card-tag tag-red">⚠️ 不适合: ${escHtml(unsuitable)}</div>` : ''}
            <div class="params">
                ${(s.parameters || []).map(p => {
                    const guideParam = guide && guide.params ? guide.params[p.name] : null;
                    const paramDesc = guideParam ? guideParam.desc : '';
                    const paramName = guideParam ? guideParam.name : p.label;
                    return `
                    <div class="param-item">
                        <span class="param-label">${escHtml(paramName)}</span>
                        <span class="param-value">${escHtml(String(p.default))}</span>
                        ${paramDesc ? `<span class="param-desc">${escHtml(paramDesc)}</span>` : ''}
                    </div>`;
                }).join('')}
            </div>
            ${tips ? `<p class="tips">💡 ${escHtml(tips)}</p>` : ''}
            <div class="card-actions">
                <button class="btn-sm-card primary" onclick="showStrategyModal('${s.name}')">创建实例</button>
                <button class="btn-sm-card" onclick="smartOptimize('${s.name}')" style="border-color:var(--orange);color:var(--orange);">🎯 智能优化</button>
            </div>
        </div>`;
    }).join('');
}

function showStrategyModal(name) {
    const modal = document.getElementById('strategy-modal');
    if (name) document.getElementById('modal-strategy').value = name;
    modal.style.display = 'flex';
    updateModalParams();
}

function closeStrategyModal() {
    document.getElementById('strategy-modal').style.display = 'none';
}

async function updateModalParams() {
    const name = document.getElementById('modal-strategy').value;
    const container = document.getElementById('modal-params');

    // 显示策略说明（如果有）
    const guide = (typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[name]) || null;
    let guideHtml = '';
    if (guide) {
        guideHtml = `
            <div class="strategy-guide-box">
                <div class="guide-title">${guide.icon} ${guide.name} <span style="color:${DIFFICULTY_COLORS[guide.difficulty] || '#888'}">${guide.difficulty}</span></div>
                <div class="guide-desc">${guide.description}</div>
                ${guide.tips ? `<div class="guide-tips">💡 ${guide.tips}</div>` : ''}
            </div>`;
    }

    // Fetch from API if not cached
    if (!window._strategyDefs[name]) {
        try {
            const info = await API.get(`/api/strategies/${name}`);
            window._strategyDefs[name] = info;
        } catch (e) {
            console.error('Failed to load strategy params:', e);
            container.innerHTML = guideHtml;
            return;
        }
    }

    const params = window._strategyDefs[name].parameters || [];
    const paramsHtml = params.map(p => {
        const guideParam = guide && guide.params ? guide.params[p.name] : null;
        const paramName = guideParam ? guideParam.name : p.label;
        const paramDesc = guideParam ? guideParam.desc : '';
        const step = p.step || (p.type === 'float' ? '0.01' : '1');
        const inputType = p.type === 'bool' ? 'checkbox' : 'number';
        if (inputType === 'checkbox') {
            return `
                <div class="form-group">
                    <label style="display:flex;align-items:center;gap:8px">
                        <input type="checkbox" id="param-${p.name}" ${p.default ? 'checked' : ''}>
                        ${escHtml(paramName)}
                    </label>
                    ${paramDesc ? `<div class="param-hint">${escHtml(paramDesc)}</div>` : ''}
                </div>`;
        }
        return `
            <div class="form-group">
                <label>${escHtml(paramName)}</label>
                ${paramDesc ? `<div class="param-hint">${escHtml(paramDesc)}</div>` : ''}
                <input type="${inputType}" id="param-${p.name}" class="form-input"
                       value="${p.default}" step="${step}"
                       min="${p.min || ''}" max="${p.max || ''}">
            </div>`;
    }).join('');

    container.innerHTML = guideHtml + paramsHtml;
}

document.getElementById('modal-strategy')?.addEventListener('change', updateModalParams);

async function createStrategy() {
    const strategy = document.getElementById('modal-strategy').value;
    const symbol = document.getElementById('modal-symbol').value;

    // Collect params from modal
    const params = {};
    document.querySelectorAll('#modal-params input').forEach(input => {
        const name = input.id.replace('param-', '');
        if (input.type === 'checkbox') {
            params[name] = input.checked;
        } else {
            const num = parseFloat(input.value);
            params[name] = isNaN(num) ? input.value : num;
        }
    });

    // Open a trade with this strategy
    try {
        const side = confirm('做多？按"确定"做多，按"取消"做空') ? 'LONG' : 'SHORT';
        const result = await API.post('/api/trade/open', {
            symbol, side, leverage: 3,
        });
        if (result.success) {
            showToast(`策略 ${getStrategyLabel(strategy)} 已启动`, 'success');
            closeStrategyModal();
            refreshDashboard();
        }
    } catch (e) {
        showToast('创建失败: ' + friendlyError(e.message), 'error');
    }
}

// Close modal on overlay click
document.getElementById('strategy-modal')?.addEventListener('click', function(e) {
    if (e.target === this) closeStrategyModal();
});

// Strategy search and filter event listeners
document.getElementById('strategy-search')?.addEventListener('input', filterStrategies);
document.getElementById('strategy-difficulty-filter')?.addEventListener('change', filterStrategies);
document.getElementById('strategy-type-filter')?.addEventListener('change', filterStrategies);

// ── Strategy Ranking ──
let _rankingVisible = true;
let _rankingData = null;

function toggleRanking() {
    _rankingVisible = !_rankingVisible;
    const content = document.getElementById('strategy-ranking-content');
    const icon = document.getElementById('ranking-toggle-icon');
    if (content) content.style.display = _rankingVisible ? '' : 'none';
    if (icon) icon.textContent = _rankingVisible ? '▼' : '▶';
}

async function loadStrategyRanking() {
    const content = document.getElementById('strategy-ranking-content');
    if (!content) return;

    // Check localStorage cache (valid for 30 minutes)
    const cacheKey = 'strategy_ranking_cache';
    const cached = localStorage.getItem(cacheKey);
    if (cached) {
        try {
            const parsed = JSON.parse(cached);
            if (parsed.timestamp && Date.now() - parsed.timestamp < 30 * 60 * 1000) {
                renderRanking(parsed.data);
                return;
            }
        } catch (e) { /* invalid cache, refetch */ }
    }

    // Show loading
    content.innerHTML = '<div class="ranking-loading"><div class="spinner"></div> 加载策略排行中...</div>';

    try {
        const result = await API.post('/api/backtest/compare', {
            strategy: 'dual_ma',
            symbol: 'BTCUSDT',
            interval: '1h',
            initial_capital: 10000,
            days: 90,
            params: {},
        });

        // Sort by total_return desc, take top 10
        const sorted = (result.results || [])
            .filter(r => !r.error)
            .sort((a, b) => (b.total_return || -999) - (a.total_return || -999))
            .slice(0, 10);

        // Cache
        localStorage.setItem(cacheKey, JSON.stringify({
            timestamp: Date.now(),
            data: sorted,
        }));

        _rankingData = sorted;
        renderRanking(sorted);
    } catch (e) {
        content.innerHTML = '<div class="ranking-loading" style="color:var(--red);">加载排行失败</div>';
        console.error('Failed to load strategy ranking:', e);
    }
}

function renderRanking(data) {
    const content = document.getElementById('strategy-ranking-content');
    if (!content) return;

    if (!data || data.length === 0) {
        content.innerHTML = '<div class="ranking-loading">暂无排行数据</div>';
        return;
    }

    const medals = ['🥇', '🥈', '🥉'];

    let html = data.map((r, i) => {
        const guide = (typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[r.strategy]) || null;
        const name = guide ? guide.name : getStrategyLabel(r.strategy);
        const rank = i + 1;
        const rankDisplay = rank <= 3 ? medals[rank - 1] : `#${rank}`;
        const retCls = (r.total_return || 0) >= 0 ? 'var(--green)' : 'var(--red)';

        // Generate brief advice
        let advice = '';
        if (r.sharpe_ratio > 1.5) advice = '稳健优选';
        else if (r.total_return > 20) advice = '高收益';
        else if (r.win_rate > 60) advice = '高胜率';
        else if (r.max_drawdown < 10) advice = '低回撤';
        else if (r.total_return > 0) advice = '表现尚可';
        else advice = '需优化';

        return `
        <div class="ranking-row">
            <span class="rank-num">${rankDisplay}</span>
            <span class="rank-name">${escHtml(name)}</span>
            <div class="rank-metrics">
                <div class="metric-item">
                    <span class="metric-val" style="color:${retCls}">${(r.total_return || 0).toFixed(1)}%</span>
                    <span>收益</span>
                </div>
                <div class="metric-item">
                    <span class="metric-val">${(r.sharpe_ratio || 0).toFixed(2)}</span>
                    <span>夏普</span>
                </div>
                <div class="metric-item">
                    <span class="metric-val">${(r.win_rate || 0).toFixed(0)}%</span>
                    <span>胜率</span>
                </div>
            </div>
            <span class="rank-advice">${advice}</span>
        </div>`;
    }).join('');

    content.innerHTML = html;
}

// Load ranking when strategy page becomes active
const _origInitStrategy = initStrategyPage;
initStrategyPage = async function() {
    await _origInitStrategy();
    loadStrategyRanking();
};

// ── Smart Optimization ──
async function smartOptimize(strategyName) {
    const guide = (typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[strategyName]) || null;
    const name = guide ? guide.name : getStrategyLabel(strategyName);

    // Build parameter grid from STRATEGY_GUIDE
    const paramGrid = buildOptimizationGrid(strategyName);
    if (paramGrid.length === 0) {
        showToast(`${name} 无可优化参数`, 'warning');
        return;
    }

    // Show progress modal
    const modal = showOptimizationModal(name, paramGrid.length);

    try {
        // Get default backtest first
        const defaultResult = await API.post('/api/backtest', {
            strategy: strategyName,
            symbol: 'BTCUSDT',
            interval: '1h',
            initial_capital: 10000,
            days: 90,
            params: getDefaultParams(strategyName),
        });

        // Run optimization - iterate through param combinations
        let bestResult = defaultResult;
        let bestParams = getDefaultParams(strategyName);
        let completed = 0;

        for (const params of paramGrid) {
            try {
                const result = await API.post('/api/backtest', {
                    strategy: strategyName,
                    symbol: 'BTCUSDT',
                    interval: '1h',
                    initial_capital: 10000,
                    days: 90,
                    params,
                });

                completed++;
                updateOptimizationProgress(modal, completed, paramGrid.length);

                if (result.metrics && result.metrics.total_return > (bestResult.metrics?.total_return || -Infinity)) {
                    bestResult = result;
                    bestParams = { ...params };
                }
            } catch (e) {
                completed++;
                updateOptimizationProgress(modal, completed, paramGrid.length);
                continue;
            }
        }

        // Show results
        showOptimizationResults(modal, name, defaultResult, bestResult, bestParams, getDefaultParams(strategyName));
    } catch (e) {
        if (modal) modal.remove();
        showToast('优化失败: ' + friendlyError(e.message), 'error');
    }
}

function buildOptimizationGrid(strategyName) {
    const guide = (typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[strategyName]) || null;
    if (!guide || !guide.params) return [];

    const paramRanges = {};
    for (const [key, param] of Object.entries(guide.params)) {
        const defaultValue = param.default;
        if (typeof defaultValue !== 'number') continue;

        // Generate 3 values around default: 0.5x, 1x, 2x
        const values = [
            Math.round(defaultValue * 0.5 * 100) / 100,
            defaultValue,
            Math.round(defaultValue * 2 * 100) / 100,
        ].filter((v, i, arr) => arr.indexOf(v) === i); // dedupe
        paramRanges[key] = values;
    }

    // Generate Cartesian product, limit to 30 combinations
    return cartesianProduct(paramRanges, 30);
}

function cartesianProduct(ranges, limit) {
    const keys = Object.keys(ranges);
    if (keys.length === 0) return [];

    const result = [];
    function recurse(idx, current) {
        if (result.length >= limit) return;
        if (idx === keys.length) {
            result.push({ ...current });
            return;
        }
        const key = keys[idx];
        for (const val of ranges[key]) {
            current[key] = val;
            recurse(idx + 1, current);
        }
    }
    recurse(0, {});
    return result;
}

function getDefaultParams(strategyName) {
    const guide = (typeof STRATEGY_GUIDE !== 'undefined' && STRATEGY_GUIDE[strategyName]) || null;
    const params = {};
    if (guide && guide.params) {
        for (const [key, param] of Object.entries(guide.params)) {
            params[key] = param.default;
        }
    }
    return params;
}

function showOptimizationModal(name, total) {
    // Remove existing
    const existing = document.getElementById('opt-modal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'opt-modal';
    overlay.className = 'modal';
    overlay.style.display = 'flex';
    overlay.innerHTML = `
        <div class="modal-content" style="max-width:500px;">
            <div class="modal-header">
                <h3>🎯 智能优化 - ${escHtml(name)}</h3>
            </div>
            <div class="modal-body">
                <div class="opt-progress">
                    <div class="spinner"></div>
                    <div style="margin-top:12px;color:var(--text-secondary);">正在测试 ${total} 组参数组合...</div>
                    <div class="progress-bar">
                        <div class="progress-fill" id="opt-progress-fill" style="width:0%"></div>
                    </div>
                    <div style="font-size:12px;color:var(--text-muted);" id="opt-progress-text">0 / ${total}</div>
                </div>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    return overlay;
}

function updateOptimizationProgress(modal, completed, total) {
    const fill = modal.querySelector('#opt-progress-fill');
    const text = modal.querySelector('#opt-progress-text');
    if (fill) fill.style.width = `${Math.round((completed / total) * 100)}%`;
    if (text) text.textContent = `${completed} / ${total}`;
}

function showOptimizationResults(modal, name, defaultResult, bestResult, bestParams, defaultParams) {
    const dm = defaultResult.metrics || {};
    const bm = bestResult.metrics || {};

    const improvement = dm.total_return !== undefined
        ? ((bm.total_return || 0) - (dm.total_return || 0)).toFixed(2)
        : '--';

    const paramsDiff = Object.entries(bestParams)
        .filter(([k, v]) => defaultParams[k] !== undefined && defaultParams[k] !== v)
        .map(([k, v]) => {
            const guide = (typeof STRATEGY_GUIDE !== 'undefined') ? (STRATEGY_GUIDE[bestResult.strategy] || null) : null;
            const pName = (guide && guide.params && guide.params[k]) ? guide.params[k].name : k;
            return `${pName}: ${defaultParams[k]} → <strong style="color:var(--green)">${v}</strong>`;
        }).join('<br>') || '参数无变化';

    modal.querySelector('.modal-body').innerHTML = `
        <div class="opt-results">
            <h4 style="margin-bottom:12px;">优化完成 - ${escHtml(name)}</h4>

            <div class="opt-compare">
                <div class="opt-box">
                    <div class="opt-box-title">默认参数</div>
                    <div class="opt-box-value" style="color:${(dm.total_return || 0) >= 0 ? 'var(--green)' : 'var(--red)'}">${(dm.total_return || 0).toFixed(2)}%</div>
                    <div style="font-size:11px;color:var(--text-muted);">夏普 ${(dm.sharpe_ratio || 0).toFixed(2)}</div>
                </div>
                <div class="opt-box best">
                    <div class="opt-box-title">🏆 最优参数</div>
                    <div class="opt-box-value" style="color:${(bm.total_return || 0) >= 0 ? 'var(--green)' : 'var(--red)'}">${(bm.total_return || 0).toFixed(2)}%</div>
                    <div style="font-size:11px;color:var(--text-muted);">夏普 ${(bm.sharpe_ratio || 0).toFixed(2)}</div>
                </div>
            </div>

            <div style="margin-top:16px;padding:12px;background:rgba(79,195,247,0.05);border-radius:8px;">
                <div style="font-size:13px;color:var(--accent);font-weight:600;margin-bottom:8px;">
                    收益提升: ${Number(improvement) >= 0 ? '+' : ''}${improvement}%
                </div>
                <div style="font-size:12px;color:var(--text-secondary);line-height:1.8;">
                    ${paramsDiff}
                </div>
            </div>

            <button class="btn btn-primary btn-block" style="margin-top:16px;" onclick="applyOptimalParams('${bestResult.strategy || ''}', ${JSON.stringify(bestParams).replace(/"/g, '&quot;')})">
                ✅ 应用最优参数创建实例
            </button>
            <button class="btn btn-block" style="background:var(--bg-hover);color:var(--text-primary);margin-top:8px;" onclick="document.getElementById('opt-modal').remove()">
                关闭
            </button>
        </div>`;
}

function applyOptimalParams(strategyName, params) {
    document.getElementById('opt-modal')?.remove();
    showStrategyModal(strategyName);
    // Wait for modal to render, then set params
    setTimeout(() => {
        for (const [key, value] of Object.entries(params)) {
            const input = document.getElementById(`param-${key}`);
            if (input) input.value = value;
        }
    }, 300);
}
