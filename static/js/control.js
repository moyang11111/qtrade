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
  const MANUAL_UPDATE_PATH = '/api/update/run';
  const MANUAL_UPDATE_STATUS_PATH = '/api/update/run/status';
  const MANUAL_UPDATE_POLL_MS = 1000;
  // The service may retry three runs of five bounded 7200-second commands,
  // with two five-minute retry gaps. Keep polling slightly longer so a live
  // job is never shown as failed merely because the client deadline elapsed.
  const MANUAL_UPDATE_MAX_WAIT_MS = (3 * 5 * 7200 + 2 * 300 + 60) * 1000;
  const MANUAL_UPDATE_STATES = new Set([
    'idle', 'accepted', 'running', 'success', 'portal_success', 'skip', 'failure', 'aborted', 'timed_out',
  ]);
  const MANUAL_UPDATE_REASONS = new Set([
    'accepted', 'running', 'before_cutoff', 'already_running', 'already_success',
    'lock_busy', 'calendar_unavailable', 'calendar_cache', 'calendar_cache_closed',
    'calendar_api', 'calendar_api_closed', 'weekend', 'deck_missing', 'step_failed',
    'update_failed', 'status_unavailable', 'completed', 'aborted', 'application_shutdown',
    'manual_stop', 'stale_running', 'timeout', 'process_timeout',
    'freshness_capture_failed', 'portal_completed', 'portal_refresh_failed', 'calendar_closed',
    'universe_unavailable', 'provider_schema', 'provider_failed', 'provider_unreachable',
    'checkpoint_corrupt', 'checkpoint_io', 'lease_busy', 'stale_running', 'item_timeout',
    'batch_timeout', 'job_timeout', 'publish_timeout', 'publish_failed', 'reload_failed',
  ]);
  const MANUAL_UPDATE_ERROR_CODES = new Set([
    'before_cutoff', 'already_running', 'lock_busy', 'request_too_large',
    'unknown_field', 'invalid_request', 'unsupported_media_type', 'status_unavailable',
    'update_failed',
  ]);
  const MANUAL_UPDATE_OUTPUTS = ['portal', 'factors', 'decision', 'sync'];
  const NAVIGATION_PAGES = new Set([
    'market', 'portal', 'pitch', 'factorboard', 'factors', 'autopaper',
  ]);
  const SAFE_STATES = new Set(['running', 'success', 'portal_success', 'skip', 'failure', 'unknown']);
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
    manualUpdate: document.getElementById('manualUpdateButton'),
    manualUpdateHint: document.getElementById('manualUpdateHint'),
    manualUpdateStatus: document.getElementById('manualUpdateStatus'),
    manualUpdateProgress: document.getElementById('manualUpdateProgress'),
    manualUpdateOutputs: document.getElementById('manualUpdateOutputs'),
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
    manual: {
      value: null,
      requestInFlight: false,
      requestController: null,
      statusInFlight: false,
      statusController: null,
      statusPromise: null,
      pollTimer: null,
      pollDeadline: 0,
      pollToken: 0,
    },
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
      running: '更新中', success: '已成功', portal_success: '门户已刷新', skip: '已跳过', failure: '失败', unknown: '未确认',
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
      started: '已启动', calendar_unavailable: '交易日历不可确认', calendar_closed: '交易日历显示休市', deck_missing: '底座不可用',
      portal_completed: '门户已刷新；因子、决策和同步将在后续阶段运行。',
      portal_refresh_failed: '门户刷新失败，旧数据保持不变。',
      step_failed: '步骤失败', update_failed: '更新失败', status_unavailable: '状态文件不可用',
      aborted: '已中止', application_shutdown: '应用关闭，更新已中止',
      manual_stop: '已停止等待', stale_running: '发现过期任务，已安全中止',
      timeout: '步骤超时', process_timeout: '任务超时',
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

  function safeManualState(value) {
    return typeof value === 'string' && MANUAL_UPDATE_STATES.has(value) ? value : 'idle';
  }

  function manualStateLabel(value) {
    return {
      idle: '等待运行',
      accepted: '已接收',
      running: '更新中',
      success: '已成功',
      portal_success: '门户已刷新',
      skip: '已跳过',
      failure: '失败',
      aborted: '已中止',
      timed_out: '已超时',
    }[safeManualState(value)];
  }

  function manualReasonLabel(value) {
    return {
      accepted: '已接收，正在准备门户刷新。',
      running: '正在分批刷新门户数据。',
      portal_completed: '门户数据已刷新；因子、决策和同步将在后续阶段运行。',
      portal_refresh_failed: '门户刷新失败，旧数据保持不变。',
      calendar_closed: '交易日历显示今日休市。',
      before_cutoff: '18:30 后可运行。',
      already_running: '已有更新正在运行，请稍候。',
      already_success: '当天已成功更新，无需重复运行。',
      lock_busy: '更新锁被占用，请稍候重试。',
      calendar_unavailable: '无法确认交易日，已安全停止。',
      calendar_cache: '使用交易日历缓存。',
      calendar_cache_closed: '交易日历显示今日休市。',
      calendar_api: '交易日历确认今日为交易日。',
      calendar_api_closed: '交易日历显示今日休市。',
      weekend: '周末不运行更新。',
      deck_missing: '研究底座不可用。',
      step_failed: '流水线步骤失败，后续步骤已停止。',
      update_failed: '更新失败，请检查数据状态后重试。',
      status_unavailable: '更新状态暂不可用，请稍后重试。',
      completed: '完整流水线已完成。',
      aborted: '任务已中止，未完成的步骤不会继续。',
      application_shutdown: '应用正在关闭，任务已安全中止。',
      manual_stop: '已停止等待，未宣称上游任务已取消。',
      stale_running: '发现过期任务，已安全中止，可重新检查。',
      timeout: '步骤超过时限，后续步骤已停止。',
      process_timeout: '任务超过时限，后续步骤已停止。',
    }[value] || '状态需核对。';
  }

  function safeManualReason(value) {
    if (typeof value === 'string' && value.startsWith('calendar_unavailable:')) {
      return 'calendar_unavailable';
    }
    return typeof value === 'string' && MANUAL_UPDATE_REASONS.has(value)
      ? value : 'status_unavailable';
  }

  function manualErrorMessage(error) {
    const code = error && error.code;
    if (code === 'before_cutoff') return '18:30 后可运行。';
    if (code === 'already_running' || code === 'lock_busy') return '已有更新正在运行，请稍候。';
    if (code === 'timeout' || code === 'process_timeout') return '更新超时，后续步骤已停止。';
    if (code === 'request_too_large' || code === 'unknown_field' || code === 'invalid_request') {
      return '请求格式不受支持。';
    }
    if (error && error.status === 415) return '当前服务不支持手动更新请求。';
    return '手动更新暂不可用，请稍后重试。';
  }

  function safeManualPayload(value) {
    const payload = asObject(value) || {};
    const stateValue = safeManualState(payload.state);
    const outputs = asObject(payload.outputs) || {};
    const tradeDate = safeDate(payload.trade_date);
    const freshness = asObject(payload.freshness) || {};
    const retry = asObject(payload.retry) || {};
    const clean = {
      state: stateValue,
      trade_date: tradeDate === '未确认' ? null : tradeDate,
      started_at: typeof payload.started_at === 'string'
        && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(payload.started_at)
        ? payload.started_at.slice(0, 32) : null,
      finished_at: typeof payload.finished_at === 'string'
        && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(payload.finished_at)
        ? payload.finished_at.slice(0, 32) : null,
      reason: safeManualReason(payload.reason),
      mode: payload.mode === 'portal_only' ? 'portal_only' : 'full_pipeline',
      outputs: Object.fromEntries(MANUAL_UPDATE_OUTPUTS.map((key) => [key, outputs[key] === true])),
      freshness: Object.fromEntries(['portal', 'factors', 'decision', 'sync'].flatMap((key) => {
        const item = asObject(freshness[key]);
        if (!item) return [];
        return [[key, {
          verified: item.verified === true,
          as_of: safeDate(item.as_of) === '未确认' ? null : safeDate(item.as_of),
          source: typeof item.source === 'string' ? item.source.slice(0, 32) : 'unavailable',
          reason: typeof item.reason === 'string' ? item.reason.slice(0, 64) : 'unavailable',
        }]];
      })),
      retry: {
        attempt: Number.isInteger(retry.attempt) && retry.attempt >= 0 ? retry.attempt : 0,
        max_attempts: Number.isInteger(retry.max_attempts) && retry.max_attempts >= 0
          ? retry.max_attempts : 3,
      },
      step: typeof payload.step === 'string' && /^[a-z][a-z0-9_]{0,47}$/.test(payload.step)
        ? payload.step : null,
      elapsed_seconds: Number.isFinite(payload.elapsed_seconds) && payload.elapsed_seconds >= 0
        ? Math.min(Number(payload.elapsed_seconds), 86400) : 0,
      progress: {
        completed: Number.isInteger(payload.progress?.completed) && payload.progress.completed >= 0
          ? Math.min(payload.progress.completed, 100) : 0,
        total: Number.isInteger(payload.progress?.total) && payload.progress.total >= 0
          ? Math.min(payload.progress.total, 100) : 0,
        current: typeof payload.progress?.current === 'string'
          && /^[a-z][a-z0-9_]{0,47}$/.test(payload.progress.current)
          ? payload.progress.current : null,
      },
    };
    return clean;
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

  async function fetchManualJson(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { Accept: 'application/json', ...(options.headers || {}) },
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) {
      const error = new Error('manual update request failed');
      error.status = response.status;
      const object = asObject(payload);
      if (object && typeof object.error === 'string' && MANUAL_UPDATE_ERROR_CODES.has(object.error)) {
        error.code = object.error;
      }
      if (object && typeof object.reason === 'string' && MANUAL_UPDATE_REASONS.has(object.reason)) {
        error.code = object.reason;
      }
      throw error;
    }
    return asObject(payload) || {};
  }

  function renderManualUpdate(result) {
    if (!els.manualUpdateStatus || !els.manualUpdate) return;
    if (!result || !result.ok) {
      state.manual.value = null;
      els.manualUpdate.disabled = true;
      els.manualUpdateStatus.textContent = '手动更新暂不可用，请稍后刷新。';
      els.manualUpdateStatus.dataset.state = 'error';
      if (els.manualUpdateProgress) els.manualUpdateProgress.textContent = '进度：未确认';
      if (els.manualUpdateOutputs) {
        const labels = { portal: '门户', factors: '因子', decision: '决策', sync: '同步' };
        document.querySelectorAll('[data-update-output]').forEach((node) => {
          node.textContent = `${labels[node.dataset.updateOutput] || '结果'}：未确认`;
          node.dataset.state = '';
        });
      }
      return;
    }
    const payload = safeManualPayload(result.value);
    state.manual.value = payload;
    const stateValue = payload.state;
    const active = stateValue === 'accepted' || stateValue === 'running';
    els.manualUpdate.disabled = active;
    els.manualUpdateStatus.textContent = `状态：${manualStateLabel(stateValue)} · 目标日期：${payload.trade_date || '未确认'} · ${manualReasonLabel(payload.reason)}`;
    els.manualUpdateStatus.dataset.state = ['success', 'portal_success'].includes(stateValue) ? 'good'
      : ['failure', 'aborted', 'timed_out'].includes(stateValue) ? 'error' : '';
    if (els.manualUpdateProgress) {
      const progress = payload.progress || {};
      const current = progress.current ? ` · 当前步骤：${progress.current}` : '';
      const elapsed = Number.isFinite(payload.elapsed_seconds)
        ? ` · 已用 ${Math.floor(payload.elapsed_seconds)} 秒` : '';
      els.manualUpdateProgress.textContent =
        `进度：${progress.completed || 0}/${progress.total || 0}${current}${elapsed}`;
    }
    const outputs = asObject(payload.outputs) || {};
    const labels = { portal: '门户', factors: '因子', decision: '决策', sync: '同步' };
    if (els.manualUpdateOutputs) {
      document.querySelectorAll('[data-update-output]').forEach((node) => {
        const key = node.dataset.updateOutput;
        const done = outputs[key] === true;
        const deferred = payload.mode === 'portal_only' && key !== 'portal';
        node.textContent = `${labels[key] || '结果'}：${done ? '已完成' : deferred ? '待后续阶段' : '未完成'}`;
        node.dataset.state = done ? 'good' : stateValue === 'failure' ? 'error' : '';
      });
    }
  }

  function clearManualUpdatePollTimer() {
    if (state.manual.pollTimer !== null) {
      window.clearTimeout(state.manual.pollTimer);
      state.manual.pollTimer = null;
    }
  }

  function stopManualUpdatePolling() {
    state.manual.pollToken += 1;
    clearManualUpdatePollTimer();
    if (state.manual.statusController) state.manual.statusController.abort();
    state.manual.statusController = null;
    state.manual.statusPromise = null;
  }

  function requestManualStatus() {
    if (state.manual.statusPromise) return state.manual.statusPromise;
    const controller = new AbortController();
    state.manual.statusController = controller;
    state.manual.statusInFlight = true;
    state.manual.statusPromise = fetchManualJson(MANUAL_UPDATE_STATUS_PATH, { signal: controller.signal })
      .then((payload) => ({ ok: true, value: payload }))
      .catch((error) => {
        if (error && error.name === 'AbortError') return { ok: false, aborted: true, error };
        return { ok: false, error };
      })
      .finally(() => {
        state.manual.statusInFlight = false;
        state.manual.statusController = null;
        state.manual.statusPromise = null;
      });
    return state.manual.statusPromise;
  }

  function scheduleManualUpdatePoll() {
    clearManualUpdatePollTimer();
    if (Date.now() >= state.manual.pollDeadline) {
      const current = state.manual.value && state.manual.value.state;
      if (current === 'accepted' || current === 'running') {
        if (els.manualUpdateStatus) {
          els.manualUpdateStatus.textContent = '任务仍在运行，状态可通过刷新继续查看。';
          els.manualUpdateStatus.dataset.state = '';
        }
        return;
      }
      renderManualUpdate({ ok: false, error: new Error('manual update timeout') });
      return;
    }
    const token = state.manual.pollToken;
    state.manual.pollTimer = window.setTimeout(() => {
      state.manual.pollTimer = null;
      if (token !== state.manual.pollToken) return;
      void requestManualStatus().then((result) => {
        if (token !== state.manual.pollToken) return;
        if (!result.aborted) renderManualUpdate(result);
        const next = state.manual.value && state.manual.value.state;
        if (next === 'accepted' || next === 'running') scheduleManualUpdatePoll();
        else void refresh();
      });
    }, MANUAL_UPDATE_POLL_MS);
  }

  function beginManualUpdatePolling() {
    state.manual.pollToken += 1;
    state.manual.pollDeadline = Date.now() + MANUAL_UPDATE_MAX_WAIT_MS;
    scheduleManualUpdatePoll();
  }

  async function runManualUpdate() {
    if (!els.manualUpdate || els.manualUpdate.disabled || state.manual.requestInFlight) return;
    state.manual.requestInFlight = true;
    state.manual.requestController = new AbortController();
    els.manualUpdate.disabled = true;
    if (els.manualUpdateStatus) {
      els.manualUpdateStatus.textContent = '正在提交门户刷新…';
      els.manualUpdateStatus.dataset.state = '';
    }
    try {
      const payload = await fetchManualJson(MANUAL_UPDATE_PATH, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
        signal: state.manual.requestController.signal,
      });
      renderManualUpdate({ ok: true, value: payload });
      const next = safeManualState(payload.state);
      if (next === 'accepted' || next === 'running') beginManualUpdatePolling();
    } catch (error) {
      if (!error || error.name !== 'AbortError') {
        renderManualUpdate({ ok: false, error });
        if (els.manualUpdateStatus) els.manualUpdateStatus.textContent = manualErrorMessage(error);
      }
    } finally {
      state.manual.requestInFlight = false;
      state.manual.requestController = null;
      const next = state.manual.value && state.manual.value.state;
      if (next !== 'accepted' && next !== 'running' && els.manualUpdate) els.manualUpdate.disabled = false;
    }
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

  function render(results, requestId, manualResult) {
    if (requestId !== state.requestId) return;
    state.data = results;
    renderSystem(results.health);
    renderPipeline(results.update);
    renderUniverse(results.autoPaper);
    renderOpportunities(results.autoPaper);
    renderFactors(results.factorLibrary);
    renderHarness(results.harness);
    renderManualUpdate(manualResult);
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
    state.refreshPromise = Promise.all([
      readCards(controller.signal),
      requestManualStatus(),
    ])
      .then(([results, manualResult]) => render(results, requestId, manualResult))
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
  if (els.manualUpdate) els.manualUpdate.addEventListener('click', () => { void runManualUpdate(); });
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
    stopManualUpdatePolling();
    if (state.manual.requestController) state.manual.requestController.abort();
    stopChat();
  }, { once: true });
  if (typeof window !== 'undefined') window.QTradeDeepSeekChat = DeepSeekChat;
  if (typeof module !== 'undefined' && module.exports) module.exports = DeepSeekChat;
  void loadChatStatus();
  void refresh();
})();
