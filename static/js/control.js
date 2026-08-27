/* QTrade-owned read-only operations console. No command, trading, or write API. */
(() => {
  'use strict';

  const API_PATHS = Object.freeze({
    health: '/api/health',
    update: '/api/update/status',
    autoPaper: '/api/auto/paper?action=status',
    factorLibrary: '/api/factor-library',
    harness: '/api/harness/status',
  });
  const NAVIGATION_PAGES = new Set([
    'market', 'portal', 'pitch', 'factorboard', 'factors', 'autopaper',
  ]);
  const SAFE_STATES = new Set(['running', 'success', 'skip', 'failure', 'unknown']);
  const SAFE_HARNESS_STATES = new Set(['disabled', 'unreachable', 'service_reachable']);
  const SAFE_SOURCES = new Set([
    'external_sqlite', 'factor_artifacts', 'decision_artifact', 'sync_target', 'dry_run',
    'unavailable', 'csv', 'fallback', 'unknown',
  ]);

  const els = {
    state: document.getElementById('controlState'),
    notice: document.getElementById('controlNotice'),
    refresh: document.getElementById('controlRefresh'),
    copy: document.getElementById('controlCopy'),
    system: document.getElementById('systemBody'),
    pipeline: document.getElementById('pipelineBody'),
    universe: document.getElementById('universeBody'),
    opportunity: document.getElementById('opportunityBody'),
    factor: document.getElementById('factorBody'),
    harness: document.getElementById('harnessBody'),
  };

  const state = {
    data: {},
    controller: null,
    refreshPromise: null,
    requestId: 0,
    lastSuccessToken: null,
    timer: null,
  };

  function clear(element) {
    if (!element) return;
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function text(element, value, className) {
    const node = document.createElement('span');
    if (className) node.className = className;
    node.textContent = value;
    element.appendChild(node);
    return node;
  }

  function row(label, value, className = '') {
    const node = document.createElement('div');
    node.className = 'control-row';
    text(node, label, 'control-label');
    text(node, value, `control-value${className ? ` ${className}` : ''}`);
    return node;
  }

  function setBody(element, rows) {
    clear(element);
    rows.forEach((item) => element.appendChild(item));
  }

  function unavailable(element, message = '当前不可用，可点击刷新重试') {
    clear(element);
    text(element, message, 'control-muted');
  }

  function asObject(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
  }

  function safeState(value) {
    return typeof value === 'string' && SAFE_STATES.has(value) ? value : 'unknown';
  }

  function safeHarnessState(value) {
    return typeof value === 'string' && SAFE_HARNESS_STATES.has(value) ? value : 'unreachable';
  }

  function safeSource(value) {
    return typeof value === 'string' && SAFE_SOURCES.has(value) ? value : 'unknown';
  }

  function safeDate(value) {
    return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value.slice(0, 10))
      ? value.slice(0, 10) : '未确认';
  }

  function safeCount(value) {
    return Number.isInteger(value) && value >= 0 ? String(value) : '未确认';
  }

  function stateLabel(value) {
    return {
      running: '更新中', success: '已成功', skip: '已跳过', failure: '失败', unknown: '未确认',
    }[safeState(value)];
  }

  function harnessStateLabel(value) {
    return {
      disabled: '已禁用', unreachable: '服务不可达', service_reachable: '服务可达',
    }[safeHarnessState(value)];
  }

  function reasonLabel(value) {
    return {
      completed: '流水线完成', dry_run: '演练状态', pipeline_running: '流水线运行中',
      started: '已启动', calendar_unavailable: '交易日历不可确认', deck_missing: '底座不可用',
      step_failed: '步骤失败', update_failed: '更新失败', status_unavailable: '状态文件不可用',
      weekend: '非交易日', calendar_cache: '使用日历缓存', calendar_cache_closed: '日历显示休市',
      calendar_api: '日历接口确认交易日', calendar_api_closed: '日历接口显示休市',
    }[value] || '状态需核对';
  }

  function successToken(update) {
    if (!update || update.state !== 'success') return null;
    if (typeof update.trade_date !== 'string' || typeof update.finished_at !== 'string') return null;
    if (!update.trade_date || !update.finished_at) return null;
    return `${update.trade_date}|${update.finished_at}`;
  }

  async function fetchJson(path, signal) {
    const response = await fetch(path, { method: 'GET', headers: { Accept: 'application/json' }, signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    return asObject(payload) || payload;
  }

  async function readCards(signal) {
    const keys = Object.keys(API_PATHS);
    const results = await Promise.all(keys.map(async (key) => {
      try {
        return [key, { ok: true, value: await fetchJson(API_PATHS[key], signal) }];
      } catch (error) {
        if (error && error.name === 'AbortError') throw error;
        return [key, { ok: false }];
      }
    }));
    return Object.fromEntries(results);
  }

  function renderSystem(result) {
    if (!result || !result.ok) return unavailable(els.system);
    const health = asObject(result.value);
    if (!health) return unavailable(els.system);
    const status = health.status === 'ok' ? '后端正常' : '后端状态需核对';
    const statusClass = health.status === 'ok' ? 'good' : 'warn';
    setBody(els.system, [
      row('后端', status, statusClass),
      row('数据模式', health.mode === 'live' ? '实时模式' : '本地回退模式'),
      row('标的数量', safeCount(health.symbols)),
    ]);
  }

  function renderPipeline(result) {
    if (!result || !result.ok) return unavailable(els.pipeline);
    const update = asObject(result.value);
    if (!update) return unavailable(els.pipeline);
    const stateValue = safeState(update.state);
    const freshness = asObject(update.freshness) || asObject(update.output_meta) || {};
    const rows = [
      row('状态', stateLabel(stateValue), stateValue === 'success' ? 'good' : 'warn'),
      row('目标交易日', safeDate(update.trade_date)),
    ];
    ['portal', 'factors', 'decision', 'sync'].forEach((group) => {
      const item = asObject(freshness[group]);
      const verified = item && item.verified === true;
      rows.push(row(`${group} 核验`, verified ? `已核验 · ${safeDate(item.as_of)}` : '未核验', verified ? 'good' : 'warn'));
    });
    const retry = asObject(update.retry);
    if (retry) rows.push(row('重试', `${safeCount(retry.attempt)} / ${safeCount(retry.max_attempts)}`));
    rows.push(row('说明', reasonLabel(update.reason)));
    setBody(els.pipeline, rows);
  }

  function getUniverse(autoPaper) {
    const payload = asObject(autoPaper);
    return payload && asObject(payload.universe_summary);
  }

  function renderUniverse(result) {
    if (!result || !result.ok) return unavailable(els.universe);
    const summary = getUniverse(result.value);
    if (!summary) return unavailable(els.universe, '主板池统计暂不可用');
    const source = safeSource(summary.source);
    setBody(els.universe, [
      row('主板总池', safeCount(summary.total)),
      row('可计算池', safeCount(summary.computable)),
      row('可交易池', safeCount(summary.tradable)),
      row('信号候选池', safeCount(summary.candidate)),
      row('数据日期', safeDate(summary.as_of)),
      row('数据来源', source === 'fallback' ? '回退池' : source),
    ]);
  }

  function renderOpportunities(result) {
    if (!result || !result.ok) return unavailable(els.opportunity);
    const payload = asObject(result.value);
    if (!payload) return unavailable(els.opportunity);
    const summary = asObject(payload.universe_summary);
    const forwardPool = Array.isArray(payload.forward_pool) ? payload.forward_pool : [];
    const hasError = typeof payload.last_error === 'string' && payload.last_error.length > 0;
    const candidate = summary ? safeCount(summary.candidate) : '未确认';
    setBody(els.opportunity, [
      row('当前信号候选', candidate),
      row('远期验证记录', String(forwardPool.length)),
      row('模拟盘状态', payload.running === true ? '运行中' : '已暂停', payload.running === true ? 'good' : ''),
      row('最近状态', hasError ? '存在失败记录' : '无异常摘要', hasError ? 'warn' : 'good'),
    ]);
  }

  function renderFactors(result) {
    if (!result || !result.ok) return unavailable(els.factor);
    const payload = asObject(result.value);
    const items = payload && Array.isArray(payload.items) ? payload.items : [];
    const dates = [...new Set(items.map((item) => asObject(item)?.as_of).filter((date) => /^\d{4}-\d{2}-\d{2}$/.test(date || '')))].sort();
    const matched = items.reduce((total, item) => total + (Number.isInteger(item?.match_count) ? item.match_count : 0), 0);
    setBody(els.factor, [
      row('已保存方案', String(items.length)),
      row('匹配因子总数', String(matched)),
      row('可得数据日期', dates.length ? dates[dates.length - 1] : '未确认'),
      row('说明', items.length ? '方案仅供分析使用' : '暂无已保存方案'),
    ]);
  }

  function renderHarness(result) {
    if (!result || !result.ok) return unavailable(els.harness);
    const payload = asObject(result.value);
    if (!payload) return unavailable(els.harness);
    const serviceState = safeHarnessState(payload.state);
    const model = payload.model_ready === 'unknown' ? '模型未验证' : '模型状态未确认';
    setBody(els.harness, [
      row('服务状态', harnessStateLabel(serviceState), serviceState === 'service_reachable' ? 'good' : 'warn'),
      row('传输', payload.transport === 'http' ? 'HTTP 回环' : '未确认'),
      row('sessions', payload.sessions_reachable === true ? '可访问' : '不可访问'),
      row('模型', model, 'warn'),
      row('说明', serviceState === 'service_reachable' ? '服务可达，模型未验证' : '不会在此页面启动服务'),
    ]);
  }

  function render(results, requestId) {
    if (requestId !== state.requestId) return;
    state.data = results;
    renderSystem(results.health);
    renderPipeline(results.update);
    renderUniverse(results.autoPaper);
    renderOpportunities(results.autoPaper);
    renderFactors(results.factorLibrary);
    renderHarness(results.harness);
    const failed = Object.values(results).some((result) => !result.ok);
    els.state.textContent = failed ? '部分状态不可用' : '状态已读取';
    els.state.dataset.state = failed ? 'error' : 'ok';
    els.notice.hidden = true;
    const token = results.update && results.update.ok ? successToken(results.update.value) : null;
    if (token && token !== state.lastSuccessToken) {
      state.lastSuccessToken = token;
      els.notice.textContent = '检测到新的数据更新状态，控制台已重新读取。';
      els.notice.hidden = false;
    }
  }

  function refresh() {
    if (state.refreshPromise) return state.refreshPromise;
    const requestId = ++state.requestId;
    const controller = new AbortController();
    state.controller = controller;
    els.refresh.disabled = true;
    els.state.textContent = '正在读取状态…';
    state.refreshPromise = readCards(controller.signal)
      .then((results) => render(results, requestId))
      .catch((error) => {
        if (!error || error.name !== 'AbortError') {
          els.state.textContent = '状态读取失败';
          els.state.dataset.state = 'error';
          els.notice.textContent = '状态暂时不可用，请稍后重试。';
          els.notice.hidden = false;
        }
      })
      .finally(() => {
        if (requestId === state.requestId) {
          state.refreshPromise = null;
          state.controller = null;
          els.refresh.disabled = false;
        }
      });
    return state.refreshPromise;
  }

  function diagnosticPayload() {
    const health = state.data.health?.ok ? asObject(state.data.health.value) : null;
    const update = state.data.update?.ok ? asObject(state.data.update.value) : null;
    const autoPaper = state.data.autoPaper?.ok ? asObject(state.data.autoPaper.value) : null;
    const summary = getUniverse(autoPaper);
    const library = state.data.factorLibrary?.ok ? asObject(state.data.factorLibrary.value) : null;
    const harness = state.data.harness?.ok ? asObject(state.data.harness.value) : null;
    return {
      generated_at: new Date().toISOString(),
      system: {
        status: health?.status === 'ok' ? 'ok' : 'unavailable',
        mode: health?.mode === 'live' ? 'live' : 'csv',
        symbols: Number.isInteger(health?.symbols) ? health.symbols : null,
      },
      pipeline: {
        trade_date: /^\d{4}-\d{2}-\d{2}$/.test(update?.trade_date || '') ? update.trade_date : null,
        state: safeState(update?.state),
        reason: reasonLabel(update?.reason),
        outputs: asObject(update?.outputs) ? {
          portal: update.outputs.portal === true,
          factors: update.outputs.factors === true,
          decision: update.outputs.decision === true,
        } : null,
      },
      mainboard: summary ? {
        total: Number.isInteger(summary.total) ? summary.total : null,
        computable: Number.isInteger(summary.computable) ? summary.computable : null,
        tradable: Number.isInteger(summary.tradable) ? summary.tradable : null,
        candidate: Number.isInteger(summary.candidate) ? summary.candidate : null,
        as_of: safeDate(summary.as_of),
        source: safeSource(summary.source),
      } : null,
      factor_library: { count: Array.isArray(library?.items) ? library.items.length : null },
      harness: harness ? {
        enabled: harness.enabled === true,
        state: safeHarnessState(harness.state),
        transport: harness.transport === 'http' ? 'http' : 'unknown',
        sessions_reachable: harness.sessions_reachable === true,
        model_ready: 'unknown',
      } : null,
    };
  }

  async function copyDiagnostics() {
    const payload = JSON.stringify(diagnosticPayload(), null, 2);
    try {
      if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(payload);
      els.copy.textContent = '已复制脱敏诊断';
    } catch (error) {
      els.copy.textContent = '复制不可用';
    }
    window.setTimeout(() => { els.copy.textContent = '复制脱敏诊断'; }, 1800);
  }

  function navigate(page) {
    if (!NAVIGATION_PAGES.has(page)) return;
    if (window.parent === window) return;
    window.parent.postMessage({ type: 'qtrade:navigate', page }, window.location.origin);
  }

  els.refresh.addEventListener('click', () => { void refresh(); });
  els.copy.addEventListener('click', () => { void copyDiagnostics(); });
  document.querySelectorAll('[data-qtrade-page]').forEach((button) => {
    button.addEventListener('click', () => navigate(button.dataset.qtradePage));
  });
  state.timer = window.setInterval(() => { void refresh(); }, 30000);
  window.addEventListener('pagehide', () => {
    if (state.timer !== null) window.clearInterval(state.timer);
    if (state.controller) state.controller.abort();
  }, { once: true });
  void refresh();
})();
