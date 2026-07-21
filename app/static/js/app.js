/**
 * LLMOKX 交易工具 - 前端交互
 */

// API请求封装
async function api(url, options = {}) {
    const resp = await fetch(url, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    const data = await resp.json();
    if (!resp.ok) {
        throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    return data;
}

// Toast消息提示
function toast(message, type = 'info') {
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => {
        el.style.opacity = '0';
        el.style.transform = 'translateX(100%)';
        setTimeout(() => el.remove(), 300);
    }, 3000);
}

// 页面切换
function showPage(pageId) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.menu-item').forEach(m => m.classList.remove('active'));
    document.getElementById(`page-${pageId}`).classList.add('active');
    document.querySelector(`.menu-item[data-page="${pageId}"]`).classList.add('active');

    // 加载对应页面数据
    const loaders = {
        dashboard: loadDashboard,
        llm: loadLLMConfig,
        prompts: loadPrompts,
        forward: loadForwardConfig,
        notification: loadNotificationConfig,
        monitor: loadMonitorPage,
        logs: loadLogFiles,
        update: loadUpdateVersion,
        settings: loadSettings,
    };
    if (loaders[pageId]) loaders[pageId]();
}

// ==================== 仪表盘 ====================
async function loadDashboard() {
    try {
        const status = await api('/api/settings/status');
        const llm = status.modules.llm_analysis;
        const fwd = status.modules.forward;
        const notif = status.modules.notification;

        document.getElementById('stat-llm-status').innerHTML = llm.enabled
            ? '<span class="badge badge-success">已启用</span>'
            : '<span class="badge badge-danger">已禁用</span>';
        document.getElementById('stat-llm-model').textContent = llm.model || '未配置';
        document.getElementById('stat-llm-api').innerHTML = llm.api_configured
            ? '<span class="badge badge-success">已配置</span>'
            : '<span class="badge badge-warning">未配置</span>';

        document.getElementById('stat-forward-status').innerHTML = fwd.enabled
            ? '<span class="badge badge-success">已启用</span>'
            : '<span class="badge badge-danger">已禁用</span>';
        document.getElementById('stat-forward-targets').textContent = fwd.targets_count;

        document.getElementById('stat-notify-status').innerHTML = notif.enabled
            ? '<span class="badge badge-success">已启用</span>'
            : '<span class="badge badge-danger">已禁用</span>';
        document.getElementById('stat-notify-wechat').innerHTML = notif.wechat_configured
            ? '<span class="badge badge-success">已配置</span>'
            : '<span class="badge badge-warning">未配置</span>';
        document.getElementById('stat-notify-telegram').innerHTML = notif.telegram_configured
            ? '<span class="badge badge-success">已配置</span>'
            : '<span class="badge badge-warning">未配置</span>';

        document.getElementById('stat-uptime').textContent = formatUptime(status.uptime);
        document.getElementById('stat-python').textContent = status.python_version;
    } catch (e) {
        toast('加载状态失败: ' + e.message, 'error');
    }
}

function formatUptime(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return `${h}时${m}分${s}秒`;
}

// ==================== LLM分析配置 ====================
async function loadLLMConfig() {
    try {
        const cfg = await api('/api/llm/config');
        document.getElementById('llm-enabled').checked = cfg.enabled !== false;
        document.getElementById('llm-provider').value = cfg.provider || 'openai';
        document.getElementById('llm-api-base').value = cfg.api_base || '';
        document.getElementById('llm-model').value = cfg.model || '';
        document.getElementById('llm-fallback-model').value = cfg.fallback_model || '';
        document.getElementById('llm-max-retries').value = cfg.max_retries || 2;
        document.getElementById('llm-temperature').value = cfg.temperature || 0.3;
        document.getElementById('llm-max-tokens').value = cfg.max_tokens || 800;
        document.getElementById('llm-timeout').value = cfg.timeout || 90;

        if (cfg.api_key_configured) {
            document.getElementById('llm-api-key').placeholder = cfg.api_key_masked || '已配置(输入新值覆盖)';
        }
    } catch (e) {
        toast('加载LLM配置失败: ' + e.message, 'error');
    }
}

async function saveLLMConfig() {
    const data = {
        enabled: document.getElementById('llm-enabled').checked,
        provider: document.getElementById('llm-provider').value,
        api_base: document.getElementById('llm-api-base').value,
        model: document.getElementById('llm-model').value,
        fallback_model: document.getElementById('llm-fallback-model').value,
        max_retries: parseInt(document.getElementById('llm-max-retries').value),
        temperature: parseFloat(document.getElementById('llm-temperature').value),
        max_tokens: parseInt(document.getElementById('llm-max-tokens').value),
        timeout: parseInt(document.getElementById('llm-timeout').value),
    };
    const apiKey = document.getElementById('llm-api-key').value;
    if (apiKey) data.api_key = apiKey;

    try {
        await api('/api/llm/config', {
            method: 'PUT',
            body: JSON.stringify(data),
        });
        toast('LLM配置已保存', 'success');
        loadLLMConfig();
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

async function testLLMConnection() {
    try {
        toast('正在测试连接...', 'info');
        const result = await api('/api/llm/test-connection', { method: 'POST' });
        if (result.success) {
            toast('连接配置正常: ' + result.info.model, 'success');
        } else {
            toast('连接测试失败: ' + result.error, 'error');
        }
    } catch (e) {
        toast('测试失败: ' + e.message, 'error');
    }
}

async function analyzeTest() {
    const text = document.getElementById('analyze-input').value.trim();
    if (!text) {
        toast('请输入要分析的消息', 'warning');
        return;
    }
    const context = document.getElementById('analyze-context').value.trim() || '无持仓无挂单';

    const resultEl = document.getElementById('analyze-result');
    resultEl.innerHTML = '<div style="color:#909399;">分析中...</div>';

    try {
        const result = await api('/api/llm/analyze', {
            method: 'POST',
            body: JSON.stringify({ text, context }),
        });
        resultEl.innerHTML = formatJSON(result);
    } catch (e) {
        resultEl.innerHTML = `<div style="color:#f56c6c;">错误: ${e.message}</div>`;
    }
}

// ==================== Prompt管理 ====================
async function loadPrompts() {
    try {
        const data = await api('/api/llm/prompts');
        const prompts = data.prompts;
        const stats = data.stats;

        // 统计信息
        document.getElementById('prompt-rules-count').textContent = stats.rules_count;
        document.getElementById('prompt-examples-count').textContent = stats.examples_count;
        document.getElementById('prompt-custom-rules-count').textContent = stats.custom_rules_count;
        document.getElementById('prompt-custom-examples-count').textContent = stats.custom_examples_count;

        // 系统提示词
        document.getElementById('prompt-system').value = prompts.system_prompt || '';

        // 内置规则列表
        renderRules('builtin-rules-list', prompts.rules || [], false);

        // 自定义规则列表
        renderRules('custom-rules-list', prompts.custom_rules || [], true);

        // 内置示例列表
        renderExamples('builtin-examples-list', prompts.examples || [], false);

        // 自定义示例列表
        renderExamples('custom-examples-list', prompts.custom_examples || [], true);
    } catch (e) {
        toast('加载Prompt失败: ' + e.message, 'error');
    }
}

function renderRules(containerId, rules, isCustom) {
    const container = document.getElementById(containerId);
    if (!rules || rules.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="empty-icon">📋</div><div>暂无规则</div></div>';
        return;
    }
    container.innerHTML = rules.map((r, i) => {
        if (isCustom && typeof r === 'object') {
            const enabled = r.enabled !== false;
            return `
                <div class="list-item">
                    <div class="list-item-content">
                        <div style="margin-bottom:4px;">${escapeHtml(r.rule || '')}</div>
                        ${r.description ? `<div style="font-size:12px;color:#909399;">${escapeHtml(r.description)}</div>` : ''}
                        <div style="font-size:12px;color:#909399;">优先级: ${r.priority || 0}
                            <span class="badge ${enabled ? 'badge-success' : 'badge-info'}" style="margin-left:8px;">${enabled ? '启用' : '禁用'}</span>
                        </div>
                    </div>
                    <div class="list-item-actions">
                        <button class="btn btn-sm ${enabled ? 'btn-warning' : 'btn-success'}" onclick="toggleCustomRule(${i}, ${!enabled})">${enabled ? '禁用' : '启用'}</button>
                        <button class="btn btn-sm btn-danger" onclick="deleteCustomRule(${i})">删除</button>
                    </div>
                </div>`;
        }
        const text = typeof r === 'string' ? r : (r.rule || JSON.stringify(r));
        return `<div class="list-item"><div class="list-item-content">${escapeHtml(text)}</div><span class="badge badge-info">内置</span></div>`;
    }).join('');
}

function renderExamples(containerId, examples, isCustom) {
    const container = document.getElementById(containerId);
    if (!examples || examples.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="empty-icon">📝</div><div>暂无示例</div></div>';
        return;
    }
    container.innerHTML = examples.map((ex, i) => {
        const desc = ex.description ? `(${escapeHtml(ex.description)})` : '';
        const deleteBtn = isCustom ? `<button class="btn btn-sm btn-danger" onclick="deleteCustomExample(${i})">删除</button>` : '<span class="badge badge-info">内置</span>';
        return `
            <div class="list-item" style="flex-direction:column;align-items:stretch;">
                <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
                    <strong>示例${i + 1} ${desc}</strong>
                    ${deleteBtn}
                </div>
                <div style="background:#f5f7fa;padding:8px;border-radius:4px;margin-bottom:6px;">
                    <div style="font-size:12px;color:#909399;margin-bottom:4px;">输入:</div>
                    <div>${escapeHtml(ex.input || '')}</div>
                </div>
                <div style="background:#f5f7fa;padding:8px;border-radius:4px;">
                    <div style="font-size:12px;color:#909399;margin-bottom:4px;">输出:</div>
                    <pre style="white-space:pre-wrap;font-size:12px;">${escapeHtml(ex.output || '')}</pre>
                </div>
            </div>`;
    }).join('');
}

function showAddRuleModal() {
    document.getElementById('modal-add-rule').style.display = 'flex';
}

function hideAddRuleModal() {
    document.getElementById('modal-add-rule').style.display = 'none';
    document.getElementById('new-rule-text').value = '';
    document.getElementById('new-rule-desc').value = '';
    document.getElementById('new-rule-priority').value = '0';
}

async function addCustomRule() {
    const rule = document.getElementById('new-rule-text').value.trim();
    if (!rule) { toast('请输入规则内容', 'error'); return; }
    const data = {
        rule,
        description: document.getElementById('new-rule-desc').value.trim(),
        priority: parseInt(document.getElementById('new-rule-priority').value) || 0,
        enabled: true,
    };
    try {
        await api('/api/llm/prompts/rules', { method: 'POST', body: JSON.stringify(data) });
        toast('规则已添加', 'success');
        hideAddRuleModal();
        loadPrompts();
    } catch (e) {
        toast('添加失败: ' + e.message, 'error');
    }
}

async function deleteCustomRule(index) {
    if (!confirm('确定删除这条规则?')) return;
    try {
        await api(`/api/llm/prompts/rules/${index}`, { method: 'DELETE' });
        toast('规则已删除', 'success');
        loadPrompts();
    } catch (e) {
        toast('删除失败: ' + e.message, 'error');
    }
}

async function toggleCustomRule(index, enabled) {
    try {
        await api(`/api/llm/prompts/rules/${index}/toggle?enabled=${enabled}`, { method: 'PUT' });
        toast(enabled ? '规则已启用' : '规则已禁用', 'success');
        loadPrompts();
    } catch (e) {
        toast('操作失败: ' + e.message, 'error');
    }
}

function showAddExampleModal() {
    document.getElementById('modal-add-example').style.display = 'flex';
}

function hideAddExampleModal() {
    document.getElementById('modal-add-example').style.display = 'none';
    document.getElementById('new-example-input').value = '';
    document.getElementById('new-example-output').value = '';
    document.getElementById('new-example-desc').value = '';
}

async function addCustomExample() {
    const input_text = document.getElementById('new-example-input').value.trim();
    const output_json = document.getElementById('new-example-output').value.trim();
    if (!input_text || !output_json) { toast('请填写输入和输出', 'error'); return; }
    const data = {
        input_text,
        output_json,
        description: document.getElementById('new-example-desc').value.trim(),
    };
    try {
        await api('/api/llm/prompts/examples', { method: 'POST', body: JSON.stringify(data) });
        toast('示例已添加', 'success');
        hideAddExampleModal();
        loadPrompts();
    } catch (e) {
        toast('添加失败: ' + e.message, 'error');
    }
}

async function deleteCustomExample(index) {
    if (!confirm('确定删除这个示例?')) return;
    try {
        await api(`/api/llm/prompts/examples/${index}`, { method: 'DELETE' });
        toast('示例已删除', 'success');
        loadPrompts();
    } catch (e) {
        toast('删除失败: ' + e.message, 'error');
    }
}

async function reloadPrompts() {
    try {
        await api('/api/llm/prompts/reload', { method: 'POST' });
        toast('Prompt已重新加载', 'success');
        loadPrompts();
    } catch (e) {
        toast('重载失败: ' + e.message, 'error');
    }
}

// ==================== 转发管理 ====================
async function loadForwardConfig() {
    try {
        const cfg = await api('/api/forward/config');
        document.getElementById('forward-enabled').checked = cfg.enabled !== false;
        document.getElementById('forward-userbot-enabled').checked = cfg.userbot_enabled !== false;
        document.getElementById('forward-skip-intents').value = (cfg.skip_intents || ['chat', 'query']).join(', ');
        document.getElementById('forward-bot-token').value = '';
        if (cfg.bot_token_configured) {
            document.getElementById('forward-bot-token').placeholder = cfg.telegram_bot_token_masked || '已配置';
        }
        await loadForwardTargets();
    } catch (e) {
        toast('加载转发配置失败: ' + e.message, 'error');
    }
}

async function loadForwardTargets() {
    try {
        const data = await api('/api/forward/targets');
        const container = document.getElementById('forward-targets-list');
        const targets = data.targets || [];
        if (targets.length === 0) {
            container.innerHTML = '<div class="empty-state"><div class="empty-icon">📤</div><div>暂无转发目标</div></div>';
            return;
        }
        container.innerHTML = targets.map((t, i) => `
            <div class="list-item">
                <div class="list-item-content">
                    <div><strong>${escapeHtml(t.description || '未命名')}</strong></div>
                    <div style="font-size:12px;color:#909399;">通道: ${escapeHtml(t.channel)} | 目标: ${escapeHtml(t.target)}</div>
                </div>
                <div class="list-item-actions">
                    <button class="btn btn-sm btn-primary" onclick="testForwardTarget(${i})">测试</button>
                    <button class="btn btn-sm btn-danger" onclick="deleteForwardTarget(${i})">删除</button>
                </div>
            </div>`).join('');
    } catch (e) {
        toast('加载转发目标失败: ' + e.message, 'error');
    }
}

async function saveForwardConfig() {
    const skipStr = document.getElementById('forward-skip-intents').value.trim();
    const data = {
        enabled: document.getElementById('forward-enabled').checked,
        userbot_enabled: document.getElementById('forward-userbot-enabled').checked,
        skip_intents: skipStr ? skipStr.split(',').map(s => s.trim()) : [],
    };
    const token = document.getElementById('forward-bot-token').value;
    if (token) data.telegram_bot_token = token;
    try {
        await api('/api/forward/config', { method: 'PUT', body: JSON.stringify(data) });
        toast('转发配置已保存', 'success');
        loadForwardConfig();
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

async function addForwardTarget() {
    const data = {
        channel: document.getElementById('new-target-channel').value,
        target: document.getElementById('new-target-id').value.trim(),
        description: document.getElementById('new-target-desc').value.trim(),
    };
    if (!data.target) { toast('请填写目标ID', 'error'); return; }
    try {
        await api('/api/forward/targets', { method: 'POST', body: JSON.stringify(data) });
        toast('转发目标已添加', 'success');
        document.getElementById('new-target-id').value = '';
        document.getElementById('new-target-desc').value = '';
        loadForwardTargets();
    } catch (e) {
        toast('添加失败: ' + e.message, 'error');
    }
}

async function deleteForwardTarget(index) {
    if (!confirm('确定删除此转发目标?')) return;
    try {
        await api(`/api/forward/targets/${index}`, { method: 'DELETE' });
        toast('已删除', 'success');
        loadForwardTargets();
    } catch (e) {
        toast('删除失败: ' + e.message, 'error');
    }
}

async function testForwardTarget(index) {
    try {
        const data = await api('/api/forward/targets');
        const target = data.targets[index];
        toast('正在测试转发...', 'info');
        const result = await api('/api/forward/test', { method: 'POST', body: JSON.stringify({ target }) });
        if (result.success) {
            toast('测试成功: ' + result.message, 'success');
        } else {
            toast('测试失败: ' + result.message, 'error');
        }
    } catch (e) {
        toast('测试失败: ' + e.message, 'error');
    }
}

// ==================== 通知管理 ====================
async function loadNotificationConfig() {
    try {
        const cfg = await api('/api/notification/config');
        // 全局开关
        document.getElementById('notify-enabled').checked = cfg.enabled !== false;
        document.getElementById('notify-max-retries').value = cfg.max_retries || 3;
        document.getElementById('notify-retry-interval').value = cfg.retry_interval || 5;
        document.getElementById('notify-parallel').checked = cfg.parallel !== false;

        // 微信通道
        const wechat = cfg.wechat || {};
        document.getElementById('notify-wechat-enabled').checked = wechat.enabled !== false;
        document.getElementById('notify-use-openclaw').checked = wechat.use_openclaw !== false;
        document.getElementById('notify-wechat-target').value = wechat.target || '';
        document.getElementById('notify-wechat-account').value = wechat.account || '';
        document.getElementById('notify-wechat-channel').value = wechat.channel || 'openclaw-weixin';
        document.getElementById('notify-webhook-url').value = wechat.webhook_url || '';

        // Telegram通道
        const telegram = cfg.telegram || {};
        document.getElementById('notify-telegram-enabled').checked = telegram.enabled !== false;
        document.getElementById('notify-telegram-bot-token').value = '';
        if (telegram.bot_token) {
            document.getElementById('notify-telegram-bot-token').placeholder = '已配置(输入新值覆盖)';
        }
        document.getElementById('notify-telegram-chat-id').value = telegram.chat_id || '';
        document.getElementById('notify-telegram-parse-mode').value = telegram.parse_mode || 'HTML';
        document.getElementById('notify-telegram-disable-notification').checked = telegram.disable_notification || false;
    } catch (e) {
        toast('加载通知配置失败: ' + e.message, 'error');
    }
}

async function saveNotificationGlobalConfig() {
    const data = {
        enabled: document.getElementById('notify-enabled').checked,
        max_retries: parseInt(document.getElementById('notify-max-retries').value),
        retry_interval: parseInt(document.getElementById('notify-retry-interval').value),
        parallel: document.getElementById('notify-parallel').checked,
    };
    try {
        await api('/api/notification/config', { method: 'PUT', body: JSON.stringify(data) });
        toast('全局配置已保存', 'success');
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

async function saveWechatConfig() {
    const data = {
        wechat: {
            enabled: document.getElementById('notify-wechat-enabled').checked,
            use_openclaw: document.getElementById('notify-use-openclaw').checked,
            target: document.getElementById('notify-wechat-target').value.trim(),
            account: document.getElementById('notify-wechat-account').value.trim(),
            channel: document.getElementById('notify-wechat-channel').value.trim(),
            webhook_url: document.getElementById('notify-webhook-url').value.trim(),
        },
    };
    try {
        await api('/api/notification/config', { method: 'PUT', body: JSON.stringify(data) });
        toast('微信配置已保存', 'success');
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

async function saveTelegramConfig() {
    const data = {
        telegram: {
            enabled: document.getElementById('notify-telegram-enabled').checked,
            chat_id: document.getElementById('notify-telegram-chat-id').value.trim(),
            parse_mode: document.getElementById('notify-telegram-parse-mode').value,
            disable_notification: document.getElementById('notify-telegram-disable-notification').checked,
        },
    };
    const token = document.getElementById('notify-telegram-bot-token').value;
    if (token) data.telegram.bot_token = token;
    try {
        await api('/api/notification/config', { method: 'PUT', body: JSON.stringify(data) });
        toast('Telegram配置已保存', 'success');
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

async function testNotification(channel) {
    try {
        toast(`正在发送测试通知(${channel})...`, 'info');
        const result = await api('/api/notification/test', {
            method: 'POST',
            body: JSON.stringify({ channel: channel || 'all' }),
        });
        if (result.success) {
            toast('测试通知发送成功!', 'success');
        } else {
            toast('测试失败: ' + result.message, 'error');
        }
    } catch (e) {
        toast('测试失败: ' + e.message, 'error');
    }
}

// ==================== 系统设置 ====================
async function loadSettings() {
    try {
        const cfg = await api('/api/settings/');
        document.getElementById('server-auth-enabled').checked = cfg.auth_enabled || false;
        document.getElementById('server-username').value = cfg.username || 'admin';
        document.getElementById('server-password').value = '';
        document.getElementById('server-password').placeholder = cfg.password_configured ? '已配置(输入新值覆盖)' : '请输入密码';

        const allCfg = await api('/api/settings/all-config');
        document.getElementById('all-config-display').innerHTML = formatJSON(allCfg);
    } catch (e) {
        toast('加载设置失败: ' + e.message, 'error');
    }
}

async function saveSettings() {
    const data = {
        auth_enabled: document.getElementById('server-auth-enabled').checked,
        username: document.getElementById('server-username').value.trim(),
    };
    const pwd = document.getElementById('server-password').value;
    if (pwd) data.password = pwd;
    try {
        await api('/api/settings/', { method: 'PUT', body: JSON.stringify(data) });
        toast('设置已保存', 'success');
        loadSettings();
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

async function reloadAllConfig() {
    if (!confirm('确定重新加载所有配置?')) return;
    try {
        await api('/api/settings/reload', { method: 'POST' });
        toast('所有配置已重新加载', 'success');
        loadSettings();
    } catch (e) {
        toast('重载失败: ' + e.message, 'error');
    }
}

// ==================== 监听管理 ====================
async function loadMonitorPage() {
    await Promise.all([loadMonitorConfig(), loadMonitorStatus(), loadUserbotConfig()]);
}

async function loadMonitorConfig() {
    try {
        const cfg = await api('/api/monitor/config');
        document.getElementById('monitor-enabled').checked = cfg.enabled !== false;
        document.getElementById('monitor-min-length').value = cfg.min_message_length || 5;
        document.getElementById('monitor-keywords').value = (cfg.keywords || []).join(', ');
        document.getElementById('monitor-notify-on-signal').checked = cfg.notify_on_signal !== false;
        renderMonitorChats(cfg.chat_ids || [], cfg.chat_names || {});
    } catch (e) {
        toast('加载监听配置失败: ' + e.message, 'error');
    }
}

function renderMonitorChats(chatIds, chatNames) {
    const container = document.getElementById('monitor-chats-list');
    if (!chatIds || chatIds.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="empty-icon">📡</div><div>暂无监听群，请添加</div></div>';
        return;
    }
    container.innerHTML = chatIds.map(id => {
        const name = chatNames[id] || '未命名';
        return `
            <div class="list-item">
                <div class="list-item-content">
                    <strong>${escapeHtml(name)}</strong>
                    <div style="font-size:12px;color:#909399;">Chat ID: ${escapeHtml(id)}</div>
                </div>
                <div class="list-item-actions">
                    <button class="btn btn-sm btn-danger" onclick="removeMonitorChat('${escapeHtml(id)}')">移除</button>
                </div>
            </div>`;
    }).join('');
}

async function saveMonitorConfig() {
    const kwStr = document.getElementById('monitor-keywords').value.trim();
    const data = {
        enabled: document.getElementById('monitor-enabled').checked,
        min_message_length: parseInt(document.getElementById('monitor-min-length').value),
        keywords: kwStr ? kwStr.split(',').map(s => s.trim()).filter(Boolean) : [],
        notify_on_signal: document.getElementById('monitor-notify-on-signal').checked,
    };
    try {
        await api('/api/monitor/config', { method: 'PUT', body: JSON.stringify(data) });
        toast('监听配置已保存', 'success');
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

async function addMonitorChat() {
    const chatId = document.getElementById('new-monitor-chat-id').value.trim();
    const name = document.getElementById('new-monitor-chat-name').value.trim();
    if (!chatId) { toast('请填写群 Chat ID', 'error'); return; }
    try {
        await api('/api/monitor/chats/add', { method: 'POST', body: JSON.stringify({ chat_id: chatId, name }) });
        toast('监听群已添加', 'success');
        document.getElementById('new-monitor-chat-id').value = '';
        document.getElementById('new-monitor-chat-name').value = '';
        loadMonitorConfig();
    } catch (e) {
        toast('添加失败: ' + e.message, 'error');
    }
}

async function removeMonitorChat(chatId) {
    if (!confirm(`确定移除监听群 ${chatId}?`)) return;
    try {
        await api('/api/monitor/chats/remove', { method: 'POST', body: JSON.stringify({ chat_id: chatId }) });
        toast('已移除', 'success');
        loadMonitorConfig();
    } catch (e) {
        toast('移除失败: ' + e.message, 'error');
    }
}

async function loadMonitorStatus() {
    try {
        const data = await api('/api/monitor/status');
        const container = document.getElementById('monitor-status-display');
        const statsContainer = document.getElementById('monitor-stats-display');

        const statusBadge = data.running
            ? '<span class="badge badge-success">运行中</span>'
            : '<span class="badge badge-info">已停止</span>';
        const connectedBadge = data.connected
            ? '<span class="badge badge-success">已连接</span>'
            : '<span class="badge badge-warning">未连接</span>';

        container.innerHTML = `
            <div class="stats-grid" style="margin-bottom:0;">
                <div class="stat-card">
                    <div class="stat-label">监听状态</div>
                    <div class="stat-value">${statusBadge}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Telegram连接</div>
                    <div class="stat-value">${connectedBadge}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">监听群数</div>
                    <div class="stat-value">${(data.chat_ids || []).length}</div>
                </div>
            </div>`;

        // 统计数据
        const s = data.stats || {};
        statsContainer.innerHTML = `
            <div class="stats-grid" style="margin-bottom:0;">
                <div class="stat-card">
                    <div class="stat-label">收到消息</div>
                    <div class="stat-value">${s.total_received || 0}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">已分析</div>
                    <div class="stat-value">${s.total_analyzed || 0}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">已转发</div>
                    <div class="stat-value">${s.total_forwarded || 0}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">已通知</div>
                    <div class="stat-value">${s.total_notified || 0}</div>
                </div>
            </div>
            ${s.last_message_time ? `<div style="margin-top:12px;font-size:13px;color:#909399;">
                最近消息: ${escapeHtml(s.last_message_time)} | ${escapeHtml(s.last_message_text || '')}
                ${s.last_intent ? ' | 意图: ' + escapeHtml(s.last_intent) : ''}
            </div>` : ''}`;

        // 按钮状态
        document.getElementById('btn-monitor-start').disabled = data.running;
        document.getElementById('btn-monitor-stop').disabled = !data.running;
    } catch (e) {
        toast('加载监听状态失败: ' + e.message, 'error');
    }
}

async function startMonitor() {
    try {
        toast('正在启动监听...', 'info');
        const result = await api('/api/monitor/start', { method: 'POST' });
        toast(result.message, result.success ? 'success' : 'error');
        setTimeout(loadMonitorStatus, 2000);
    } catch (e) {
        toast('启动失败: ' + e.message, 'error');
    }
}

async function stopMonitor() {
    try {
        const result = await api('/api/monitor/stop', { method: 'POST' });
        toast(result.message, result.success ? 'success' : 'error');
        loadMonitorStatus();
    } catch (e) {
        toast('停止失败: ' + e.message, 'error');
    }
}

async function testMonitorPipeline() {
    const text = document.getElementById('monitor-test-text').value.trim();
    if (!text) { toast('请输入测试消息', 'warning'); return; }
    const container = document.getElementById('monitor-test-result');
    container.innerHTML = '<div style="color:#909399;">处理中...</div>';
    try {
        const result = await api('/api/monitor/test?text=' + encodeURIComponent(text), { method: 'POST' });
        container.innerHTML = formatJSON(result);
    } catch (e) {
        container.innerHTML = `<div style="color:#f56c6c;">错误: ${e.message}</div>`;
    }
}

// ==================== Userbot 配置 ====================
async function loadUserbotConfig() {
    try {
        const cfg = await api('/api/monitor/userbot');
        document.getElementById('ub-enabled').checked = cfg.enabled !== false;
        document.getElementById('ub-api-id').value = cfg.api_id || '';
        document.getElementById('ub-api-hash').value = '';
        if (cfg.api_hash_configured) {
            document.getElementById('ub-api-hash').placeholder = '已配置(输入新值覆盖)';
        }
        document.getElementById('ub-phone').value = cfg.phone_number || '';
        document.getElementById('ub-session').value = cfg.session_file || 'config/userbot_session';
    } catch (e) {
        toast('加载 Userbot 配置失败: ' + e.message, 'error');
    }
}

async function saveUserbotConfig() {
    const data = {
        enabled: document.getElementById('ub-enabled').checked,
        api_id: parseInt(document.getElementById('ub-api-id').value) || null,
        phone_number: document.getElementById('ub-phone').value.trim(),
        session_file: document.getElementById('ub-session').value.trim(),
    };
    const hash = document.getElementById('ub-api-hash').value;
    if (hash && !hash.includes('****')) data.api_hash = hash;
    try {
        await api('/api/monitor/userbot', { method: 'PUT', body: JSON.stringify(data) });
        toast('Userbot 配置已保存', 'success');
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

async function testUserbotConnection() {
    const container = document.getElementById('ub-test-result');
    container.innerHTML = '<div style="color:#909399;">连接中...</div>';
    try {
        const result = await api('/api/monitor/userbot/test', { method: 'POST' });
        if (result.success) {
            container.innerHTML = `<div style="color:#67c23a;">✓ ${escapeHtml(result.message)}</div>`;
        } else {
            container.innerHTML = `<div style="color:#f56c6c;">✗ ${escapeHtml(result.message)}</div>`;
        }
    } catch (e) {
        container.innerHTML = `<div style="color:#f56c6c;">✗ ${e.message}</div>`;
    }
}

// ==================== 日志查看 ====================
let _logAutoRefreshTimer = null;

async function loadLogFiles() {
    try {
        const data = await api('/api/logs/files');
        const container = document.getElementById('log-files-list');
        if (data.error) { container.innerHTML = data.error; return; }

        let html = '<table class="table"><thead><tr>';
        html += '<th>模块</th><th>文件</th><th>大小</th><th>行数</th><th>错误</th><th>警告</th><th>结构化记录</th>';
        html += '</tr></thead><tbody>';

        for (const key of ['app', 'llm_analysis', 'forward', 'notification']) {
            const info = data[key];
            if (!info) continue;
            html += '<tr>';
            html += `<td><strong>${info.description || key}</strong><br><span style="font-size:11px;color:#909399;">${key}</span></td>`;
            html += `<td style="font-size:12px;">${info.file || '-'}<br>${info.exists ? '✓ 存在' : '<span style="color:#f56c6c;">不存在</span>'}</td>`;
            html += `<td>${info.size_human || '-'}</td>`;
            html += `<td>${info.lines || 0}</td>`;
            html += `<td>${info.errors ? `<span class="badge badge-danger">${info.errors}</span>` : '<span class="badge badge-success">0</span>'}</td>`;
            html += `<td>${info.warnings ? `<span class="badge badge-warning">${info.warnings}</span>` : '<span class="badge badge-success">0</span>'}</td>`;
            const recSize = info.record_exists ? (info.record_size_human || '-') : '<span style="color:#c0c4cc;">无</span>';
            html += `<td style="font-size:12px;">${recSize}</td>`;
            html += '</tr>';
        }
        html += '</tbody></table>';

        // 归档文件
        const archive = data._archive || {};
        if (archive.total_files > 0) {
            html += `<div style="margin-top:16px;padding-top:12px;border-top:1px solid #ebeef5;">`;
            html += `<h4 style="margin-bottom:8px;color:#606266;">历史归档 (${archive.total_files}个文件)</h4>`;
            html += '<div style="max-height:150px;overflow-y:auto;font-size:12px;color:#909399;">';
            (archive.files || []).forEach(f => {
                const date = new Date(f.date * 1000).toLocaleString('zh-CN');
                html += `<div>${f.name} (${f.size_human}) - ${date}</div>`;
            });
            html += '</div></div>';
        }

        container.innerHTML = html;
    } catch (e) {
        document.getElementById('log-files-list').innerHTML = `<div style="color:#f56c6c;">加载失败: ${e.message}</div>`;
    }
}

async function loadLogTail() {
    const module = document.getElementById('log-tail-module').value;
    const level = document.getElementById('log-tail-level').value;
    const lines = document.getElementById('log-tail-lines').value;
    const container = document.getElementById('log-tail-display');

    try {
        const params = new URLSearchParams({ lines });
        if (level) params.set('level', level);
        const data = await api(`/api/logs/${module}/tail?${params}`);

        if (data.error) {
            container.innerHTML = `<div style="color:#f56c6c;">${data.error}</div>`;
            return;
        }

        const logLines = data.lines || [];
        if (logLines.length === 0) {
            container.innerHTML = '<div class="empty-state"><div class="empty-icon">📋</div><div>暂无日志</div></div>';
        } else {
            // 日志着色
            const colored = logLines.map(line => {
                let color = '#d4d4d4';
                if (line.includes('[ERROR]')) color = '#f56c6c';
                else if (line.includes('[WARNING]')) color = '#e6a23c';
                else if (line.includes('[INFO]')) color = '#67c23a';
                return `<div style="color:${color};">${escapeHtml(line)}</div>`;
            }).join('');
            container.innerHTML = `<div class="json-display" style="max-height:500px;">${colored}</div>`;
        }

        document.getElementById('log-tail-display-info')?.remove();
        const info = document.createElement('div');
        info.id = 'log-tail-display-info';
        info.style = 'text-align:right;font-size:12px;color:#909399;margin-top:8px;';
        info.textContent = `模块: ${data.description || module} | 返回 ${data.returned}/${data.total} 行`;
        container.appendChild(info);
    } catch (e) {
        container.innerHTML = `<div style="color:#f56c6c;">加载失败: ${e.message}</div>`;
    }

    // 自动刷新
    const autoRefresh = document.getElementById('log-auto-refresh').checked;
    if (_logAutoRefreshTimer) { clearTimeout(_logAutoRefreshTimer); _logAutoRefreshTimer = null; }
    if (autoRefresh) {
        _logAutoRefreshTimer = setTimeout(loadLogTail, 5000);
    }
}

async function loadLogRecords() {
    const module = document.getElementById('log-records-module').value;
    const limit = document.getElementById('log-records-limit').value;
    const container = document.getElementById('log-records-display');

    try {
        const data = await api(`/api/logs/${module}/records?limit=${limit}`);

        if (data.error) {
            container.innerHTML = `<div style="color:#f56c6c;">${data.error}</div>`;
            return;
        }

        const records = data.records || [];
        if (records.length === 0) {
            container.innerHTML = '<div class="empty-state"><div class="empty-icon">📊</div><div>暂无操作记录</div></div>';
            return;
        }

        let html = `<div style="text-align:right;font-size:12px;color:#909399;margin-bottom:12px;">返回 ${data.returned}/${data.total} 条记录</div>`;
        records.forEach((r, i) => {
            const ts = r.timestamp || '';
            const success = r.success;
            const badge = success ? 'badge-success' : 'badge-danger';
            const statusText = success ? '成功' : '失败';
            const intent = r.intent || r.action || r.type || '';
            const elapsed = r.elapsed != null ? `${r.elapsed}s` : '';
            const errorMsg = r.error ? `<div style="color:#f56c6c;font-size:12px;margin-top:4px;">错误: ${escapeHtml(r.error)}</div>` : '';

            const inputPreview = r.input ? `<div style="font-size:12px;color:#909399;margin-top:4px;">输入: ${escapeHtml(String(r.input).substring(0, 120))}</div>` : '';
            const msgPreview = r.message ? `<div style="font-size:12px;color:#909399;margin-top:4px;">内容: ${escapeHtml(String(r.message).substring(0, 120))}</div>` : '';

            html += `
                <div style="background:#f5f7fa;padding:12px;border-radius:6px;margin-bottom:8px;border-left:3px solid ${success ? '#67c23a' : '#f56c6c'};">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div>
                            <span class="badge ${badge}">${statusText}</span>
                            <strong style="margin-left:8px;">${escapeHtml(intent)}</strong>
                            ${r.symbol ? `<span style="margin-left:8px;color:#409eff;">${escapeHtml(r.symbol)}</span>` : ''}
                            ${r.direction ? `<span style="color:#909399;">${escapeHtml(r.direction)}</span>` : ''}
                        </div>
                        <div style="font-size:12px;color:#909399;">${ts} ${elapsed ? '| ' + elapsed : ''}</div>
                    </div>
                    ${inputPreview}${msgPreview}${errorMsg}
                </div>`;
        });
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div style="color:#f56c6c;">加载失败: ${e.message}</div>`;
    }
}

async function loadLogStats() {
    const module = document.getElementById('log-stats-module').value;
    const container = document.getElementById('log-stats-display');

    try {
        const data = await api(`/api/logs/${module}/stats`);
        if (data.error) {
            container.innerHTML = `<div style="color:#f56c6c;">${data.error}</div>`;
            return;
        }

        const rate = data.success_rate || 0;
        const rateColor = rate >= 90 ? '#67c23a' : rate >= 70 ? '#e6a23c' : '#f56c6c';

        let html = `
        <div class="stats-grid" style="margin-bottom:0;">
            <div class="stat-card">
                <div class="stat-label">总操作数</div>
                <div class="stat-value">${data.total}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">成功</div>
                <div class="stat-value" style="color:#67c23a;">${data.success}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">失败</div>
                <div class="stat-value" style="color:#f56c6c;">${data.failed}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">成功率</div>
                <div class="stat-value" style="color:${rateColor};">${rate}%</div>
            </div>
        </div>`;

        if (data.avg_elapsed != null && data.avg_elapsed > 0) {
            html += `
            <div class="stats-grid" style="margin-top:12px;">
                <div class="stat-card">
                    <div class="stat-label">平均耗时</div>
                    <div class="stat-value">${data.avg_elapsed}s</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">最快</div>
                    <div class="stat-value">${data.min_elapsed}s</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">最慢</div>
                    <div class="stat-value">${data.max_elapsed}s</div>
                </div>
            </div>`;
        }

        if (data.by_type && Object.keys(data.by_type).length > 0) {
            html += '<div style="margin-top:16px;"><h4 style="margin-bottom:8px;color:#606266;">按类型统计</h4><table class="table"><thead><tr><th>类型</th><th>总数</th><th>成功</th><th>失败</th><th>成功率</th></tr></thead><tbody>';
            for (const [type, stats] of Object.entries(data.by_type)) {
                const tr = stats.total > 0 ? (stats.success / stats.total * 100).toFixed(1) : 0;
                html += `<tr><td><strong>${escapeHtml(type)}</strong></td><td>${stats.total}</td><td style="color:#67c23a;">${stats.success}</td><td style="color:#f56c6c;">${stats.failed}</td><td>${tr}%</td></tr>`;
            }
            html += '</tbody></table></div>';
        }

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div style="color:#f56c6c;">加载失败: ${e.message}</div>`;
    }
}

// ==================== 升级管理 ====================

async function loadUpdateVersion() {
    try {
        const data = await api('/api/update/version');
        const container = document.getElementById('update-version-display');

        let html = `
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">当前版本</div>
                <div class="stat-value">${escapeHtml(data.current_version || '0.0.0')}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">升级方式</div>
                <div class="stat-value" style="font-size:18px;">${data.method === 'git' ? 'Git Pull' : 'GitHub Releases'}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">自动升级</div>
                <div class="stat-value">${data.update_enabled ? '<span style="color:#67c23a;">已启用</span>' : '<span style="color:#f56c6c;">已禁用</span>'}</div>
            </div>
        </div>`;
        container.innerHTML = html;

        // 同时加载配置
        await loadUpdateConfig();
        await loadBackups();
    } catch (e) {
        document.getElementById('update-version-display').innerHTML = `<div style="color:#f56c6c;">加载失败: ${e.message}</div>`;
    }
}

async function loadUpdateConfig() {
    try {
        const cfg = await api('/api/update/config');
        document.getElementById('update-method').value = cfg.method || 'release';
        document.getElementById('update-github-repo').value = cfg.github_repo || '';
        document.getElementById('update-asset-pattern').value = cfg.asset_pattern || 'llmokx-*.tar.gz';
        document.getElementById('update-restart-command').value = cfg.restart_command || 'systemctl restart llmokx';
        document.getElementById('update-enabled').checked = cfg.enabled !== false;
        document.getElementById('update-check-on-startup').checked = cfg.check_on_startup !== false;
        document.getElementById('update-notify-on-update').checked = cfg.notify_on_update !== false;
    } catch (e) {
        toast('加载升级配置失败: ' + e.message, 'error');
    }
}

async function saveUpdateConfig() {
    const data = {
        method: document.getElementById('update-method').value,
        github_repo: document.getElementById('update-github-repo').value.trim(),
        asset_pattern: document.getElementById('update-asset-pattern').value.trim(),
        restart_command: document.getElementById('update-restart-command').value.trim(),
        enabled: document.getElementById('update-enabled').checked,
        check_on_startup: document.getElementById('update-check-on-startup').checked,
        notify_on_update: document.getElementById('update-notify-on-update').checked,
    };
    try {
        await api('/api/update/config', { method: 'PUT', body: JSON.stringify(data) });
        toast('升级配置已保存', 'success');
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

async function checkUpdates() {
    const container = document.getElementById('update-check-display');
    container.innerHTML = '<div style="color:#909399;">检查中...</div>';

    try {
        const data = await api('/api/update/check');

        if (data.error) {
            container.innerHTML = `<div style="color:#f56c6c;">❌ ${escapeHtml(data.error)}</div>`;
            return;
        }

        if (!data.has_update) {
            let html = '<div style="background:#f0f9eb;padding:16px;border-radius:6px;border-left:3px solid #67c23a;">';
            html += '<strong style="color:#67c23a;">✓ 已是最新版本</strong><br>';
            html += `当前版本: <code>${escapeHtml(data.current_version)}</code>`;
            if (data.latest_version) {
                html += ` | GitHub最新: <code>${escapeHtml(data.latest_version)}</code>`;
            }
            if (data.method === 'git' && data.current_commit) {
                html += ` | 当前提交: <code>${data.current_commit}</code>`;
            }
            html += '</div>';
            container.innerHTML = html;
            return;
        }

        // 有新版本
        let html = '<div style="background:#fdf6ec;padding:16px;border-radius:6px;border-left:3px solid #e6a23c;">';
        html += '<strong style="color:#e6a23c;">📢 有新版本可用！</strong><br><br>';
        html += `当前版本: <code>${escapeHtml(data.current_version)}</code> → 最新版本: <code style="color:#67c23a;">${escapeHtml(data.latest_version || data.remote_commit)}</code><br><br>`;

        if (data.changelog) {
            html += '<strong>更新日志:</strong><br>';
            html += `<div style="background:#fff;padding:8px;border-radius:4px;max-height:200px;overflow-y:auto;font-size:13px;">${escapeHtml(data.changelog)}</div><br>`;
        }

        if (data.method === 'git' && data.new_commits && data.new_commits.length > 0) {
            html += '<strong>新提交:</strong><br>';
            html += '<div style="background:#fff;padding:8px;border-radius:4px;font-size:12px;">';
            data.new_commits.slice(0, 8).forEach(c => {
                html += `<div>${escapeHtml(c)}</div>`;
            });
            html += '</div><br>';
        }

        if (data.asset_name) {
            html += `<small>资源文件: ${escapeHtml(data.asset_name)}</small><br>`;
        }
        if (data.published_at) {
            html += `<small>发布时间: ${escapeHtml(data.published_at)}</small><br>`;
        }
        if (data.release_url) {
            html += `<small><a href="${escapeHtml(data.release_url)}" target="_blank">查看 Release</a></small><br>`;
        }
        if (!data.download_url && data.method === 'release') {
            html += '<div style="color:#f56c6c;margin-top:8px;">⚠️ 该 Release 没有匹配的资源文件，请先在 GitHub 上传对应压缩包</div>';
        }
        html += '</div>';
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div style="color:#f56c6c;">检查失败: ${e.message}</div>`;
    }
}

async function performUpdate() {
    if (!confirm('确定执行升级吗？\n\n升级前会自动备份当前版本，config/data/logs/venv 目录会保留。\n升级后需要手动重启服务。')) {
        return;
    }

    const btn = document.getElementById('btn-perform-update');
    btn.disabled = true;
    btn.textContent = '升级中...';
    document.getElementById('update-progress').style.display = 'block';
    document.getElementById('update-progress-text').textContent = '正在下载并替换文件...';
    document.getElementById('update-result').innerHTML = '';

    try {
        const result = await api('/api/update/perform', { method: 'POST' });

        document.getElementById('update-progress').style.display = 'none';

        if (result.success) {
            let html = '<div style="background:#f0f9eb;padding:16px;border-radius:6px;border-left:3px solid #67c23a;">';
            html += '<strong style="color:#67c23a;">✅ 升级成功！</strong><br>';
            html += escapeHtml(result.message || '') + '<br>';
            if (result.backup_path) {
                html += `<small>备份位置: ${escapeHtml(result.backup_path)}</small><br>`;
            }
            if (result.new_version) {
                html += `<small>新版本: ${escapeHtml(result.new_version)}</small><br>`;
            }
            html += '<br><button class="btn btn-warning" onclick="restartService()">立即重启服务</button>';
            html += '</div>';
            document.getElementById('update-result').innerHTML = html;
            toast('升级成功！请重启服务', 'success');
            await loadUpdateVersion();
        } else {
            let html = '<div style="background:#fef0f0;padding:16px;border-radius:6px;border-left:3px solid #f56c6c;">';
            html += '<strong style="color:#f56c6c;">❌ 升级失败</strong><br>';
            html += escapeHtml(result.message || '') + '<br>';
            html += '</div>';
            document.getElementById('update-result').innerHTML = html;
            toast('升级失败: ' + (result.message || '未知错误'), 'error');
        }
    } catch (e) {
        document.getElementById('update-progress').style.display = 'none';
        document.getElementById('update-result').innerHTML = `<div style="color:#f56c6c;">升级异常: ${e.message}</div>`;
        toast('升级异常: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '立即升级';
    }
}

async function restartService() {
    if (!confirm('确定重启服务吗？\n\nWeb界面将短暂不可访问，重启后自动恢复。')) {
        return;
    }
    try {
        toast('正在发送重启命令...', 'info');
        const result = await api('/api/update/restart', { method: 'POST' });
        if (result.success) {
            toast(result.message || '重启命令已派出', 'success');
        } else {
            toast('重启失败: ' + (result.message || ''), 'error');
        }
    } catch (e) {
        toast('重启异常: ' + e.message, 'error');
    }
}

async function loadBackups() {
    const container = document.getElementById('update-backups-list');
    try {
        const data = await api('/api/update/backups');
        const backups = data.backups || [];
        if (backups.length === 0) {
            container.innerHTML = '<div class="empty-state"><div class="empty-icon">📦</div><div>暂无备份</div></div>';
            return;
        }
        let html = '<table class="table"><thead><tr><th>备份名称</th><th>版本</th><th>时间</th><th>大小</th><th>操作</th></tr></thead><tbody>';
        backups.forEach(b => {
            const sizeMB = (b.size / 1048576).toFixed(2);
            const tsFormatted = b.timestamp ? b.timestamp.replace(/(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/, '$1-$2-$3 $4:$5:$6') : '';
            html += `<tr>
                <td style="font-size:12px;">${escapeHtml(b.name)}</td>
                <td><code>${escapeHtml(b.version || '未知')}</code></td>
                <td style="font-size:12px;">${tsFormatted}</td>
                <td>${sizeMB}MB</td>
                <td>
                    <button class="btn btn-sm btn-warning" onclick="rollbackBackup('${escapeHtml(b.name)}')">回滚</button>
                    <button class="btn btn-sm btn-danger" onclick="deleteBackup('${escapeHtml(b.name)}')">删除</button>
                </td>
            </tr>`;
        });
        html += '</tbody></table>';
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div style="color:#f56c6c;">加载失败: ${e.message}</div>`;
    }
}

async function rollbackBackup(name) {
    if (!confirm(`确定回滚到 ${name} 吗？\n\n回滚后需要重启服务才能生效。`)) return;
    try {
        const result = await api('/api/update/rollback', {
            method: 'POST',
            body: JSON.stringify({ backup_name: name }),
        });
        if (result.success) {
            toast('回滚成功，请重启服务', 'success');
            document.getElementById('update-result').innerHTML = `<div style="background:#f0f9eb;padding:12px;border-radius:6px;">✅ ${escapeHtml(result.message)}<br><button class="btn btn-warning" onclick="restartService()">立即重启服务</button></div>`;
            await loadUpdateVersion();
        } else {
            toast('回滚失败: ' + (result.message || ''), 'error');
        }
    } catch (e) {
        toast('回滚异常: ' + e.message, 'error');
    }
}

async function deleteBackup(name) {
    if (!confirm(`确定删除备份 ${name}？此操作不可恢复。`)) return;
    try {
        await api(`/api/update/backups/${encodeURIComponent(name)}`, { method: 'DELETE' });
        toast('备份已删除', 'success');
        await loadBackups();
    } catch (e) {
        toast('删除失败: ' + e.message, 'error');
    }
}

// ==================== 工具函数 ====================
function formatJSON(obj) {
    const json = JSON.stringify(obj, null, 2);
    return `<div class="json-display">${escapeHtml(json)}</div>`;
}

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    showPage('dashboard');
    // 加载版本号到侧边栏
    api('/api/health').then(data => {
        document.getElementById('sidebar-version').textContent = 'v' + (data.version || '--');
    }).catch(() => {});
});
