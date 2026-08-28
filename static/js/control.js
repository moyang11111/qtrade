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
  const CHAT_PATHS = Object.freeze({
    status: '/api/deepseek-chat/status',
    send: '/api/deepseek-chat/send',
    poll: '/api/deepseek-chat/poll',
    history: '/api/deepseek-chat/history',
    cancel: '/api/deepseek-chat/cancel',
  });
  const CHAT_STATES = new Set([
    'disabled', 'idle', 'unconfigured', 'ready', 'accepted', 'waiting', 'replied',
    'failed', 'timed_out', 'service_unreachable',
  ]);
  const CHAT_ERROR_MAX_BYTES = 8192;
  const CHAT_ERROR_CODES = new Set([
    'feature_disabled', 'unconfigured', 'invalid_request', 'unknown_field',
    'request_too_large', 'invalid_session', 'unknown_request', 'local_rate_limited',
    'busy', 'service_closed', 'context_unavailable', 'context_too_large',
    'upstream_unreachable', 'upstream_timeout', 'invalid_credential',
    'upstream_rate_limited', 'upstream_rejected', 'upstream_error',
    'invalid_response', 'response_too_large', 'client_cancelled', 'timed_out',
    'internal_error',
  ]);
  const CHAT_MAX_TEXT_LENGTH = 2000;
  const CHAT_MAX_HISTORY = 20;
  const CHAT_POLL_MIN_MS = 250;
  const CHAT_POLL_MAX_MS = 5000;
  const CHAT_MAX_WAIT_MS = 35000;

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
  const chatEls = {
    panel: document.getElementById('deepseekChatPanel'),
    body: document.getElementById('deepseekChatBody'),
    state: document.getElementById('deepseekChatState'),
    toggle: document.getElementById('deepseekChatToggle'),
    notice: document.getElementById('deepseekChatNotice'),
    history: document.getElementById('deepseekChatHistory'),
    input: document.getElementById('deepseekChatInput'),
    counter: document.getElementById('deepseekChatCounter'),
    send: document.getElementById('deepseekChatSend'),
    cancel: document.getElementById('deepseekChatCancel'),
  };

  const state = {
    data: {},
    controller: null,
    refreshPromise: null,
    requestId: 0,
    lastSuccessToken: null,
    timer: null,
  };
  const chatState = {
    value: 'idle',
    sessionId: null,
    requestId: null,
    requestToken: 0,
    pollController: null,
    pollTimer: null,
    pollPromise: null,
    statusController: null,
    statusPromise: null,
    historyController: null,
    historyPromise: null,
    historyLoaded: false,
    sendInFlight: false,
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

  function clampPollAfterMs(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return 1000;
    return Math.min(CHAT_POLL_MAX_MS, Math.max(CHAT_POLL_MIN_MS, Math.round(numeric)));
  }

  function safeChatState(value) {
    return typeof value === 'string' && CHAT_STATES.has(value) ? value : 'unconfigured';
  }

  function safeChatErrorState(value) {
    return typeof value === 'string' && CHAT_STATES.has(value) ? value : null;
  }

  function safeChatErrorCode(value) {
    return typeof value === 'string' && CHAT_ERROR_CODES.has(value) ? value : null;
  }

  function chatErrorBytes(value) {
    try {
      return new TextEncoder().encode(value).length;
    } catch {
      return value.length;
    }
  }

  function parseChatErrorBody(raw) {
    if (typeof raw !== 'string' || chatErrorBytes(raw) > CHAT_ERROR_MAX_BYTES) return {};
    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return {};
    }
    const object = asObject(payload);
    if (!object) return {};
    const state = safeChatErrorState(object.state);
    let code = null;
    if (typeof object.error === 'string') {
      code = safeChatErrorCode(object.error);
    } else {
      const errorObject = asObject(object.error);
      if (errorObject) code = safeChatErrorCode(errorObject.code);
    }
    return { state, code };
  }

  function chatErrorState(error) {
    const code = error && error.code;
    const reportedState = error && error.state;
    const status = error && error.status;
    const name = error && error.name;
    if (code === 'feature_disabled' || reportedState === 'disabled') return 'disabled';
    if (code === 'unconfigured' || reportedState === 'unconfigured') {
      return 'unconfigured';
    }
    if (
      code === 'upstream_timeout'
      || code === 'timed_out'
      || reportedState === 'timed_out'
      || status === 504
      || name === 'TimeoutError'
    ) return 'timed_out';
    if (code === 'upstream_unreachable' || reportedState === 'service_unreachable' || status === 502) {
      return 'service_unreachable';
    }
    if (reportedState === 'failed') return 'failed';
    return status >= 400 ? 'failed' : 'service_unreachable';
  }

  function chatStateLabel(value) {
    return {
      disabled: '功能未启用',
      idle: '等待发送',
      unconfigured: '需要配置',
      ready: '可以发送',
      accepted: '本地已接收',
      waiting: '等待回复',
      replied: '已收到回复',
      failed: '请求失败',
      timed_out: '等待超时',
      service_unreachable: '服务不可达',
    }[safeChatState(value)];
  }

  function chatStateNotice(value) {
    return {
      disabled: '功能未启用；不会产生 DeepSeek 请求。',
      idle: '等待发送只读问题。',
      unconfigured: '请完成本机服务配置后重启 QTrade。',
      ready: '仅发送纯文本问题；输入不会执行交易、命令、脚本或配置修改。',
      accepted: '本地已接收，正在等待处理。',
      waiting: '等待 DeepSeek 回复。',
      replied: '已收到回复。',
      failed: 'DeepSeek 请求失败，请稍后重试。',
      timed_out: '等待 DeepSeek 回复超时，请稍后重试。',
      service_unreachable: 'DeepSeek 服务不可达，请检查服务状态后重试。',
    }[safeChatState(value)];
  }

  function chatErrorNotice(error) {
    if (error && (error.code === 'unconfigured' || error.state === 'unconfigured' || error.status === 401)) {
      return 'DeepSeek 尚未配置，请检查服务配置后重启 QTrade。';
    }
    if (error && (error.code === 'upstream_rate_limited' || error.status === 429)) {
      return 'DeepSeek 服务繁忙，请稍后重试。';
    }
    if (
      error
      && (
        error.code === 'upstream_timeout'
        || error.code === 'timed_out'
        || error.state === 'timed_out'
        || error.status === 504
        || error.name === 'TimeoutError'
      )
    ) return '等待 DeepSeek 回复超时，请稍后重试。';
    if (error && (error.code === 'upstream_unreachable' || error.state === 'service_unreachable' || error.status === 502)) {
      return 'DeepSeek 服务不可达，请检查服务状态后重试。';
    }
    if (error && error.status >= 500) return 'DeepSeek 服务暂不可用，请稍后重试。';
    if (error && error.name === 'AbortError') return '本地请求已停止，请按需重试。';
    return 'DeepSeek 服务暂不可达，请稍后重试。';
  }

  function safeChatId(value) {
    return typeof value === 'string' && value.length > 0 && value.length <= 256 ? value : null;
  }

  function setChatNotice(message, error = false) {
    if (!chatEls.notice) return;
    chatEls.notice.textContent = message || '';
    chatEls.notice.dataset.state = error ? 'error' : '';
  }

  function renderChatState(value, notice, error = false) {
    const next = safeChatState(value);
    chatState.value = next;
    if (chatEls.state) {
      chatEls.state.textContent = chatStateLabel(next);
      chatEls.state.dataset.state = next;
    }
    if (chatEls.input) {
      chatEls.input.disabled = next !== 'ready' || chatState.sendInFlight;
    }
    if (chatEls.send) {
      chatEls.send.disabled = next !== 'ready' || chatState.sendInFlight;
    }
    const canCancel = next === 'accepted' || next === 'waiting';
    if (chatEls.cancel) {
      chatEls.cancel.hidden = !canCancel;
      chatEls.cancel.disabled = !canCancel || chatState.sendInFlight;
    }
    if (chatEls.toggle) {
      chatEls.toggle.disabled = next === 'disabled';
    }
    if (next === 'disabled' && chatEls.body) {
      chatEls.body.hidden = true;
      if (chatEls.toggle) chatEls.toggle.setAttribute('aria-expanded', 'false');
    }
    setChatNotice(notice === undefined ? chatStateNotice(next) : notice, error);
  }

  function updateChatCounter() {
    if (!chatEls.counter) return;
    const length = chatEls.input ? chatEls.input.value.length : 0;
    chatEls.counter.textContent = `${length} / ${CHAT_MAX_TEXT_LENGTH}`;
    chatEls.counter.dataset.state = length > CHAT_MAX_TEXT_LENGTH ? 'error' : '';
  }

  async function chatFetchJson(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { Accept: 'application/json', ...(options.headers || {}) },
    });
    if (!response.ok) {
      let raw = '';
      try {
        raw = await response.text();
      } catch {
        raw = '';
      }
      const parsed = parseChatErrorBody(raw);
      const error = new Error('DeepSeek request failed');
      error.status = response.status;
      if (parsed.state) error.state = parsed.state;
      if (parsed.code) error.code = parsed.code;
      throw error;
    }
    const payload = await response.json();
    return asObject(payload) || {};
  }

  function clearChatHistory() {
    clear(chatEls.history);
  }

  function appendChatMessage(role, value) {
    if (!chatEls.history || !['user', 'assistant'].includes(role) || typeof value !== 'string') return;
    const item = document.createElement('li');
    item.className = 'deepseek-chat-message';
    item.dataset.role = role;
    const label = document.createElement('span');
    label.className = 'deepseek-chat-message-label';
    label.textContent = role === 'assistant' ? 'DeepSeek 回复' : '我的问题';
    const body = document.createElement('span');
    body.className = 'deepseek-chat-message-body';
    body.textContent = value;
    item.appendChild(label);
    item.appendChild(body);
    chatEls.history.appendChild(item);
    while (chatEls.history.children.length > CHAT_MAX_HISTORY) {
      chatEls.history.removeChild(chatEls.history.firstElementChild);
    }
  }

  function renderChatHistory(payload) {
    const items = Array.isArray(payload.items) ? payload.items : [];
    clearChatHistory();
    items.slice(-CHAT_MAX_HISTORY).forEach((item) => {
      const message = asObject(item);
      if (!message || !['user', 'assistant'].includes(message.role)) return;
      if (typeof message.text === 'string') appendChatMessage(message.role, message.text);
    });
    chatState.historyLoaded = true;
  }

  async function loadChatHistory() {
    if (chatState.historyLoaded || chatState.historyPromise || !chatState.sessionId) return;
    if (chatState.value === 'disabled' || chatState.value === 'unconfigured') return;
    const controller = new AbortController();
    chatState.historyController = controller;
    const session = encodeURIComponent(chatState.sessionId);
    const path = `${CHAT_PATHS.history}?session_id=${session}&limit=${CHAT_MAX_HISTORY}`;
    chatState.historyPromise = chatFetchJson(path, { signal: controller.signal })
      .then((payload) => {
        if (!controller.signal.aborted) renderChatHistory(payload);
      })
      .catch((error) => {
        if (!error || error.name === 'AbortError') return;
        const next = chatErrorState(error);
        if (next === 'disabled' || next === 'unconfigured') {
          renderChatState(next, chatErrorNotice(error), true);
        } else {
          setChatNotice(chatErrorNotice(error), true);
        }
      })
      .finally(() => {
        chatState.historyPromise = null;
        chatState.historyController = null;
      });
    return chatState.historyPromise;
  }

  function clearChatPollTimer() {
    if (chatState.pollTimer !== null) {
      window.clearTimeout(chatState.pollTimer);
      chatState.pollTimer = null;
    }
  }

  function stopChatPolling() {
    chatState.requestToken += 1;
    clearChatPollTimer();
    if (chatState.pollController) chatState.pollController.abort();
    chatState.pollController = null;
    chatState.pollPromise = null;
  }

  function waitForChatPoll(delay, token) {
    return new Promise((resolve) => {
      chatState.pollTimer = window.setTimeout(() => {
        chatState.pollTimer = null;
        resolve(token === chatState.requestToken);
      }, delay);
    });
  }

  function replyText(payload) {
    if (!payload || payload.state !== 'replied') return null;
    if (typeof payload.reply === 'string') return payload.reply;
    if (typeof payload.assistant_text === 'string') return payload.assistant_text;
    return null;
  }

  async function pollChat(requestId, token, firstDelay) {
    const startedAt = Date.now();
    let delay = clampPollAfterMs(firstDelay);
    while (token === chatState.requestToken && Date.now() - startedAt < CHAT_MAX_WAIT_MS) {
      if (!await waitForChatPoll(delay, token)) return;
      if (token !== chatState.requestToken) return;
      const controller = new AbortController();
      chatState.pollController = controller;
      const path = `${CHAT_PATHS.poll}?request_id=${encodeURIComponent(requestId)}`;
      try {
        const payload = await chatFetchJson(path, { signal: controller.signal });
        if (token !== chatState.requestToken) return;
        const next = safeChatState(payload.state);
        if (payload.session_id) chatState.sessionId = safeChatId(payload.session_id);
        if (next === 'replied') {
          renderChatState(next);
          const reply = replyText(payload);
          if (reply !== null) appendChatMessage('assistant', reply);
          return;
        }
        if (['failed', 'timed_out', 'service_unreachable'].includes(next)) {
          renderChatState(next);
          return;
        }
        renderChatState(next === 'accepted' ? 'accepted' : 'waiting');
        delay = clampPollAfterMs(payload.poll_after_ms);
      } catch (error) {
        if (token !== chatState.requestToken || (error && error.name === 'AbortError')) return;
        renderChatState(chatErrorState(error), chatErrorNotice(error), true);
        return;
      } finally {
        if (token === chatState.requestToken) chatState.pollController = null;
      }
    }
    if (token === chatState.requestToken) renderChatState('timed_out');
  }

  function beginChatPolling(requestId, firstDelay) {
    stopChatPolling();
    const token = chatState.requestToken;
    chatState.pollPromise = pollChat(requestId, token, firstDelay)
      .finally(() => {
        if (token === chatState.requestToken) chatState.pollPromise = null;
      });
  }

  function validateChatInput(value) {
    if (typeof value !== 'string' || !value.trim()) return '请输入纯文本问题。';
    if (value.length > CHAT_MAX_TEXT_LENGTH) return `问题不能超过 ${CHAT_MAX_TEXT_LENGTH} 个字符。`;
    return null;
  }

  async function sendChat() {
    if (chatState.value !== 'ready' || chatState.sendInFlight || !chatEls.input) return;
    const value = chatEls.input.value;
    const validationError = validateChatInput(value);
    if (validationError) {
      setChatNotice(validationError, true);
      return;
    }
    chatState.sendInFlight = true;
    stopChatPolling();
    renderChatState('ready', '正在发送只读问题…');
    try {
      const payload = await chatFetchJson(CHAT_PATHS.send, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: chatState.sessionId, text: value }),
      });
      const requestId = safeChatId(payload.request_id);
      if (!requestId) throw new Error('request id unavailable');
      if (payload.session_id) chatState.sessionId = safeChatId(payload.session_id);
      chatState.requestId = requestId;
      chatState.sendInFlight = false;
      appendChatMessage('user', value);
      chatEls.input.value = '';
      updateChatCounter();
      renderChatState('accepted');
      beginChatPolling(requestId, payload.poll_after_ms);
    } catch (error) {
      chatState.sendInFlight = false;
      renderChatState(chatErrorState(error), chatErrorNotice(error), true);
    }
  }

  async function cancelChat() {
    if (!chatState.requestId && !chatState.pollPromise) return;
    const sessionId = chatState.sessionId;
    const requestId = chatState.requestId;
    stopChatPolling();
    chatState.requestId = null;
    chatState.sendInFlight = false;
    renderChatState('idle', '已停止本地等待；上游取消不受支持。');
    try {
      await chatFetchJson(CHAT_PATHS.cancel, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, request_id: requestId }),
      });
    } catch (error) {
      // 本地停止等待的结果不依赖上游是否接受取消。
    }
  }

  async function loadChatStatus() {
    if (chatState.statusPromise) return chatState.statusPromise;
    const controller = new AbortController();
    chatState.statusController = controller;
    chatState.statusPromise = chatFetchJson(CHAT_PATHS.status, { signal: controller.signal })
      .then((payload) => {
        if (controller.signal.aborted) return;
        chatState.sessionId = safeChatId(payload.session_id);
        chatState.requestId = safeChatId(payload.request_id);
        chatState.historyLoaded = false;
        const next = safeChatState(payload.state);
        renderChatState(next);
        if (next === 'replied') {
          const reply = replyText(payload);
          if (reply !== null) appendChatMessage('assistant', reply);
        } else if (['accepted', 'waiting'].includes(next) && chatState.requestId) {
          beginChatPolling(chatState.requestId, payload.poll_after_ms);
        }
      })
      .catch((error) => {
        if (!error || error.name === 'AbortError') return;
        renderChatState(chatErrorState(error), chatErrorNotice(error), true);
      })
      .finally(() => {
        chatState.statusPromise = null;
        chatState.statusController = null;
      });
    return chatState.statusPromise;
  }

  function toggleChatPanel() {
    if (!chatEls.body || !chatEls.toggle || chatState.value === 'disabled') return;
    const expanded = chatEls.toggle.getAttribute('aria-expanded') === 'true';
    chatEls.body.hidden = expanded;
    chatEls.toggle.setAttribute('aria-expanded', String(!expanded));
    chatEls.toggle.textContent = expanded ? '展开面板' : '收起面板';
    if (!expanded) void loadChatHistory();
  }

  function stopChat() {
    stopChatPolling();
    if (chatState.statusController) chatState.statusController.abort();
    if (chatState.historyController) chatState.historyController.abort();
  }

  const DeepSeekChat = Object.freeze({
    PATHS: CHAT_PATHS,
    STATES: Object.freeze([...CHAT_STATES]),
    MAX_TEXT_LENGTH: CHAT_MAX_TEXT_LENGTH,
    MAX_HISTORY: CHAT_MAX_HISTORY,
    POLL_MIN_MS: CHAT_POLL_MIN_MS,
    POLL_MAX_MS: CHAT_POLL_MAX_MS,
    MAX_WAIT_MS: CHAT_MAX_WAIT_MS,
    clampPollAfterMs,
    validateChatInput,
    controller: Object.freeze({ loadStatus: loadChatStatus, send: sendChat, cancel: cancelChat, stop: stopChat }),
  });

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
  if (chatEls.toggle) chatEls.toggle.addEventListener('click', toggleChatPanel);
  if (chatEls.input) {
    chatEls.input.addEventListener('input', updateChatCounter);
    updateChatCounter();
  }
  if (chatEls.send) chatEls.send.addEventListener('click', () => { void sendChat(); });
  if (chatEls.cancel) chatEls.cancel.addEventListener('click', () => { void cancelChat(); });
  state.timer = window.setInterval(() => { void refresh(); }, 30000);
  window.addEventListener('pagehide', () => {
    if (state.timer !== null) window.clearInterval(state.timer);
    if (state.controller) state.controller.abort();
    stopChat();
  }, { once: true });
  if (typeof window !== 'undefined') window.QTradeDeepSeekChat = DeepSeekChat;
  if (typeof module !== 'undefined' && module.exports) module.exports = DeepSeekChat;
  void loadChatStatus();
  void refresh();
})();
