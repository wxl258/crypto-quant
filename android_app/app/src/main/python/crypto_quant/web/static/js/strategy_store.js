/**
 * Strategy Store — 策略商店管理
 */
'use strict';

// ── Page init ──

async function initStrategyStorePage() {
    await loadStrategyStore();
}

async function loadStrategyStore() {
    const tbody = document.getElementById('strategy-store-tbody');
    const countEl = document.getElementById('strategy-store-count');
    if (!tbody) return;

    tbody.innerHTML = '<tr class="empty-row"><td colspan="5"><div class="spinner"></div> 加载中...</td></tr>';

    try {
        const data = await API.get('/api/strategies/admin/discover');
        const strategies = data.strategies || [];
        
        if (countEl) countEl.textContent = `共 ${strategies.length} 个策略`;

        if (strategies.length === 0) {
            tbody.innerHTML = '<tr class="empty-row"><td colspan="5">暂无策略</td></tr>';
            return;
        }

        // Batch fetch info for all strategies
        var names = strategies.map(function(s) { return typeof s === 'string' ? s : s.name; });
        var batchResp = await API.post('/api/strategies/admin/info/batch', {names: names});
        var infoMap = (batchResp && batchResp.strategies) ? batchResp.strategies : {};

        const rows = [];
        for (const name of strategies) {
            const strategyName = typeof name === 'string' ? name : name.name;
            try {
                const info = infoMap[strategyName];
                if (!info || info.status === 'error' || info.status === 'unavailable') {
                    rows.push(`
                        <tr>
                            <td><strong>${escHtml(strategyName)}</strong></td>
                            <td>--</td><td>--</td><td>--</td>
                            <td><span style="color:var(--red);font-size:11px;">加载失败</span></td>
                        </tr>`);
                    continue;
                }
                const enabled = info.enabled !== false;
                const source = info.source || 'builtin';
                const sourceLabel = source === 'builtin' ? '🏗️ 内置' : 
                                    source === 'url' ? '🌐 远程' : '📁 自定义';
                const statusClass = enabled ? 'positive' : 'negative';
                const statusText = enabled ? '✅ 启用' : '⛔ 禁用';
                const isCustom = source !== 'builtin';

                rows.push(`
                    <tr>
                        <td>
                            <strong>${escHtml(strategyName)}</strong>
                            ${info.description ? `<br><span style="font-size:11px;color:var(--text-muted);">${escHtml(info.description.split('\\n')[0].substring(0, 80))}</span>` : ''}
                        </td>
                        <td><span style="font-size:12px;color:var(--text-muted);">${escHtml(info.class_name || '--')}</span></td>
                        <td>${sourceLabel}</td>
                        <td><span class="stat-change ${statusClass}" style="cursor:pointer;" onclick="toggleStrategy('${strategyName}', ${enabled})">${statusText}</span></td>
                        <td>
                            <div style="display:flex;gap:6px;flex-wrap:wrap;">
                                <button class="btn-sm" style="font-size:11px;padding:4px 8px;" onclick="viewStrategySource('${strategyName}')">📄 源码</button>
                                ${isCustom ? `<button class="btn-sm" style="font-size:11px;padding:4px 8px;background:rgba(211,47,47,0.15);color:var(--red);" onclick="deleteStrategyConfirm('${strategyName}')">🗑️ 删除</button>` : ''}
                            </div>
                        </td>
                    </tr>`);
            } catch (e) {
                rows.push(`
                    <tr>
                        <td><strong>${escHtml(strategyName)}</strong></td>
                        <td>--</td><td>--</td><td>--</td>
                        <td><span style="color:var(--red);font-size:11px;">加载失败</span></td>
                    </tr>`);
            }
        }

        tbody.innerHTML = rows.join('');
    } catch (e) {
        const msg = (e.message && e.message.includes('Failed to fetch'))
            ? '无法连接服务器，请确认后端已启动'
            : '加载策略列表失败: ' + e.message;
        tbody.innerHTML = `<tr class="empty-row"><td colspan="5" style="color:var(--orange);">${escHtml(msg)}<br><small style="color:var(--text-muted);">请检查后端服务是否运行</small></td></tr>`;
        showToast(msg, 'error');
    }
}

// ── 热重载 ──

async function hotReloadStrategies(e) {
    const btn = (e && e.target) ? e.target.closest('button') : null;
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ 重载中...';
    }

    try {
        const result = await API.post('/api/strategies/admin/reload', {});
        const msg = result && result.success 
            ? `热重载成功！已加载 ${result.count || 0} 个策略`
            : `部分重载失败: ${(result && result.errors && result.errors.length) || 0} 个错误`;
        showToast(msg, (result && result.success) ? 'success' : 'error');
        
        if (result && result.errors && result.errors.length > 0) {
            console.warn('Reload errors:', result.errors);
        }
        
        await loadStrategyStore();
    } catch (e) {
        showToast('热重载失败: ' + friendlyError(e.message), 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '🔄 热重载';
        }
    }
}

// ── 从URL安装 ──

function showDownloadModal() {
    const overlay = document.getElementById('download-overlay');
    if (!overlay) return;
    overlay.style.display = 'flex';
    const urlEl = document.getElementById('download-url');
    if (urlEl) urlEl.value = '';
    const sha256El = document.getElementById('download-sha256');
    if (sha256El) sha256El.value = '';
}

function closeDownloadModal() {
    const overlay = document.getElementById('download-overlay');
    if (overlay) overlay.style.display = 'none';
}

async function downloadStrategy() {
    const urlEl = document.getElementById('download-url');
    const sha256El = document.getElementById('download-sha256');
    if (!urlEl || !sha256El) return;
    const url = urlEl.value.trim();
    const sha256 = sha256El.value.trim();

    if (!url) {
        showToast('请输入策略文件URL', 'error');
        return;
    }

    const btn = document.querySelector('#download-overlay .btn-primary');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = '⏳ 下载中...';

    try {
        const result = await API.post('/api/strategies/admin/download', { url, sha256 });
        showToast(result.message, 'success');
        closeDownloadModal();
        await loadStrategyStore();
    } catch (e) {
        showToast('下载失败: ' + friendlyError(e.message), 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '📥 下载并安装';
    }
}

// ── 启用/禁用 ──

async function toggleStrategy(name, currentlyEnabled) {
    const endpoint = currentlyEnabled ? 'disable' : 'enable';
    const action = currentlyEnabled ? '禁用' : '启用';
    
    if (currentlyEnabled && !confirm(`确认禁用策略 "${name}"？\n禁用后重启生效。`)) return;

    try {
        const result = await API.post(`/api/strategies/admin/${endpoint}`, { name });
        showToast(result.message, 'success');
        await loadStrategyStore();
    } catch (e) {
        showToast(`${action}失败: ` + friendlyError(e.message), 'error');
    }
}

// ── 删除 ──

async function deleteStrategyConfirm(name) {
    if (!confirm(`确认删除自定义策略 "${name}"？\n此操作不可撤销！`)) return;

    try {
        const result = await API.post('/api/strategies/admin/delete', { name });
        showToast(result.message, 'success');
        await loadStrategyStore();
    } catch (e) {
        showToast('删除失败: ' + friendlyError(e.message), 'error');
    }
}

// ── 查看源码 ──

async function viewStrategySource(name) {
    const overlay = document.getElementById('source-overlay');
    const title = document.getElementById('source-modal-title');
    const content = document.getElementById('source-content');
    if (!overlay || !title || !content) return;

    overlay.style.display = 'flex';
    title.textContent = `策略源码: ${name}`;
    content.textContent = '加载中...';

    try {
        const result = await API.get(`/api/strategies/admin/source/${name}`);
        content.textContent = result.source || '(空)';
    } catch (e) {
        content.textContent = '加载失败: ' + e.message;
    }
}

function closeSourceModal() {
    const overlay = document.getElementById('source-overlay');
    if (overlay) overlay.style.display = 'none';
}

// ── 模板 ──

async function getStrategyTemplate() {
    const overlay = document.getElementById('template-overlay');
    const content = document.getElementById('template-content');
    if (!overlay || !content) return;

    overlay.style.display = 'flex';
    content.textContent = '加载中...';

    try {
        const result = await API.get('/api/strategies/admin/template');
        content.textContent = result.template || '(空)';
    } catch (e) {
        content.textContent = '加载失败: ' + e.message;
    }
}

function closeTemplateModal() {
    const overlay = document.getElementById('template-overlay');
    if (overlay) overlay.style.display = 'none';
}

function copyTemplate() {
    const contentEl = document.getElementById('template-content');
    if (!contentEl) return;
    const content = contentEl.textContent;
    if (!navigator.clipboard) {
        showToast('剪贴板不可用，请手动复制', 'error');
        return;
    }
    navigator.clipboard.writeText(content).then(() => {
        showToast('模板已复制到剪贴板', 'success');
    }).catch(() => {
        showToast('复制失败，请手动选择复制', 'error');
    });
}

// ── 扫描策略 ──

async function discoverStrategies() {
    try {
        const result = await API.get('/api/strategies/admin/discover');
        showToast(`发现 ${result.count} 个策略`, 'info');
        await loadStrategyStore();
    } catch (e) {
        const msg = (e.message && e.message.includes('Failed to fetch'))
            ? '无法连接服务器，请确认后端已启动'
            : '扫描失败: ' + e.message;
        showToast(msg, 'error');
    }
}

// ── Modal overlay click to close ──
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal')) {
        e.target.style.display = 'none';
    }
});
