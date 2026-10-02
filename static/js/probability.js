(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const statusUrl = '/api/research/next-day-probability/status';
  const runUrl = '/api/research/next-day-probability/run';
  const results = $('results');
  const runButton = $('run-button');
  let latestState = 'idle';
  let pollTimer = null;

  function number(value, digits = 3) {
    if (value === null || value === undefined || value === '') return '—';
    return Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
  }
  function percent(value, digits = 1) {
    if (value === null || value === undefined || value === '') return '—';
    return Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(digits)}%` : '—';
  }
  window.QTradeProbabilityFormat = Object.freeze({number, percent});
  function addCell(row, value, className) {
    const cell = document.createElement('td');
    cell.textContent = value == null || value === '' ? '—' : String(value);
    if (className) cell.className = className;
    row.appendChild(cell);
  }
  function emptyRow(body, columns, message) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = columns;
    cell.className = 'empty-row';
    cell.textContent = message;
    row.appendChild(cell);
    body.replaceChildren(row);
  }
  function stateLabel(state) {
    return ({
      idle: '尚未运行', queued: '排队中', running: '研究计算中', complete: '研究计算完成',
      stale: '快照已更新', blocked: '运行受阻', failed: '运行失败', unavailable: '快照不可用',
    })[state] || '研究状态未知';
  }
  function stepLabel(step) {
    return ({
      queued: '等待开始', loading: '读取本地行情', calendar: '整理交易日历',
      features: '计算历史特征', rolling_validation: '滚动验证与校准',
      frozen_test: '冻结测试集评估', current_scoring: '拟合当前研究模型', complete: '完成',
    })[step] || step || '处理中';
  }
  function setRunButton(state) {
    const busy = state === 'queued' || state === 'running';
    runButton.disabled = busy;
    runButton.textContent = busy ? '研究进行中…' : state === 'complete' ? '数据未变，查看结果' : '开始研究';
  }
  function setState(status) {
    latestState = status.state || 'unknown';
    $('run-state').textContent = status.progress
      ? `${stateLabel(latestState)} · ${stepLabel(status.progress.step)} ${status.progress.completed}/${status.progress.total}`
      : stateLabel(latestState);
    setRunButton(latestState);
    if (latestState === 'complete' && status.report) {
      renderReport(status.report, status.report_saved === true, status.persistence_warning || '');
      return;
    }
    if (latestState === 'stale') {
      results.hidden = true;
      $('research-label').textContent = 'BLOCKED · 需要重新研究';
      $('research-label').classList.add('blocked');
      $('status-message').textContent = status.message || '快照已更新，旧结果不再匹配当前股票池。';
      $('period').textContent = `当前信号日：${status.current_as_of || '—'}　旧结果信号日：${status.report_as_of || '—'}`;
      $('version').textContent = '旧模型结果已隐藏';
      $('model-window').textContent = '';
      return;
    }
    results.hidden = true;
    $('research-label').textContent = latestState === 'complete' ? 'BLOCKED · 研究中 / 未验证' : stateLabel(latestState);
    $('research-label').classList.toggle('blocked', latestState !== 'idle');
    $('status-message').textContent = status.message || '研究不会自动启动；准备好后点击“开始研究”。';
    if (status.current_as_of || status.as_of) {
      $('period').textContent = `信号日：${status.as_of || status.current_as_of}　目标日：下一交易日（研究开始后核对日历）`;
    }
    if (status.data_version) $('version').textContent = `数据版本：${status.data_version}`;
    $('model-window').textContent = '';
  }
  function renderTable(body, rows, columns, renderRow, emptyMessage) {
    if (!Array.isArray(rows) || !rows.length) {
      emptyRow(body, columns, emptyMessage);
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const data of rows) {
      const row = document.createElement('tr');
      renderRow(row, data);
      fragment.appendChild(row);
    }
    body.replaceChildren(fragment);
  }
  function renderModels(models, counts, selection) {
    const host = $('model-summary');
    const fragment = document.createDocumentFragment();
    for (const [key, value] of Object.entries(models || {})) {
      const card = document.createElement('article');
      card.className = 'model-item';
      const title = document.createElement('strong');
      title.textContent = `${key === 'pooled_logistic' ? 'Logistic 量价模型' : 'LightGBM 非线性模型'}${key === selection?.selected_model ? ' · 当前选用' : ''}`;
      card.appendChild(title);
      const body = document.createElement('div');
      if (!value.available) {
        body.textContent = value.reason || '此模型不可用。';
      } else {
        const metrics = value.final_test || {};
        const validation = value.rolling_oos || {};
        const folds = Array.isArray(value.rolling_folds) ? value.rolling_folds.length : 0;
        body.textContent = `滚动验证 ${folds} 段 · Brier ${number(validation.brier_score, 4)} · Log loss ${number(validation.log_loss, 4)} · AUC ${number(validation.roc_auc, 3)}。${value.final_test ? `历史测试 ${metrics.sample_count || 0} 条。` : '未查看此候选的本轮历史测试。'} 校准：${value.calibration || '—'}`;
      }
      card.appendChild(body);
      fragment.appendChild(card);
    }
    const countsCard = document.createElement('article');
    countsCard.className = 'model-item';
    const countTitle = document.createElement('strong');
    countTitle.textContent = '数据覆盖';
    countsCard.appendChild(countTitle);
    const countBody = document.createElement('div');
    const sourceCount = Number(counts.source_count || 0).toLocaleString('zh-CN');
    const sourceLabel = counts.source_kind === 'portal_qfq_sqlite'
      ? `当前快照股票 ${sourceCount} 只`
      : `CSV ${sourceCount} 个`;
    countBody.textContent = `${sourceLabel} · 可用股票 ${Number(counts.usable_symbols || 0).toLocaleString('zh-CN')} 只 · 训练 ${Number(counts.training || 0).toLocaleString('zh-CN')} · 校准 ${Number(counts.calibration || 0).toLocaleString('zh-CN')} · 最终测试 ${Number(counts.final_test || 0).toLocaleString('zh-CN')} · 当前评分池 ${Number(counts.current_scoring_universe || 0).toLocaleString('zh-CN')} 只 · 研究候选 ${Number(counts.research_candidate_count ?? counts.current_predictions ?? 0).toLocaleString('zh-CN')} 只`;
    countsCard.appendChild(countBody);
    fragment.appendChild(countsCard);
    if (selection) {
      const selectionCard = document.createElement('article');
      selectionCard.className = 'model-item';
      const selectionTitle = document.createElement('strong');
      selectionTitle.textContent = '滚动验证模型选择';
      selectionCard.appendChild(selectionTitle);
      const selectionBody = document.createElement('div');
      selectionBody.textContent = `${selection.reason || '仅报告滚动验证结果。'} 本轮历史测试不用于选择或调参；该区间此前已查看，仍需新数据前瞻验证。`;
      selectionCard.appendChild(selectionBody);
      fragment.appendChild(selectionCard);
    }
    host.replaceChildren(fragment);
  }
  function renderReport(report, reportSaved, persistenceWarning) {
    results.hidden = false;
    const qualityBlocked = report.model_quality?.status !== 'PASS';
    const freshness = report.snapshot_freshness || {};
    const expired = freshness.expired === true || freshness.status === 'EXPIRED';
    const blocked = qualityBlocked || expired || freshness.status === 'UNCONFIRMED'
      || report.historical_universe?.verified !== true || report.price_adjustment?.verified !== true
      || report.validation_integrity?.verified === false;
    $('research-label').textContent = qualityBlocked
      ? expired ? 'BLOCKED · 低于基线 · 历史研究候选已过期' : 'BLOCKED · 低于基线 · 仅供研究排序'
      : expired ? 'BLOCKED · 数据已过期 · 历史研究候选'
        : blocked ? 'BLOCKED · 研究中 / 未验证' : '研究中 · 等待独立审查';
    $('research-label').classList.toggle('blocked', blocked);
    $('prediction-note').textContent = qualityBlocked
      ? '模型未优于事前常数基线；下表仍显示模型估计值的研究排序，未经验证，不宜据此交易。该名单不改变 BLOCKED 质量门禁。'
      : '下表按当前评分模型估计值从高到低排序；当前模型没有独立未来校准验证，未经独立验证，不宜据此交易。';
    $('freshness-banner').hidden = !expired && freshness.status !== 'UNCONFIRMED';
    $('freshness-banner').textContent = expired
      ? `数据已过期：信号日 ${report.as_of || '—'}，目标日 ${report.target_date || '—'} 已结束。下表仅供回看。`
      : freshness.status === 'UNCONFIRMED' ? '目标交易日未由交易日历确认，列表不能视作当前下一交易日名单。' : '';
    $('status-message').textContent = [
      reportSaved ? '研究报告已保存在本机，可在本次 QTrade 中继续查看。' : persistenceWarning || '本地研究报告未能保存。',
      report.historical_universe?.reason,
      report.price_adjustment?.reason,
      report.model_quality?.reason,
      report.validation_integrity?.reason,
      report.execution_validation?.reason,
      expired ? freshness.reason : null,
      report.resource_controls?.blas_thread_cap_applied === false
        ? '当前运行环境没有计算线程限制组件；研究仍受文件、样本和迭代次数上限约束，耗时取决于本机。'
        : null,
    ].filter(Boolean).join(' ');
    $('period').textContent = `信号日：${report.as_of || '—'}　目标交易日：${report.target_date || '—'}${report.target_date_confirmed ? '' : '（未由交易日历确认）'}`;
    const sourceText = report.sample_counts?.source_kind === 'portal_qfq_sqlite'
      ? '已验证 qfq SQLite 快照（PIT 未验证）'
      : report.data_source || '本地 CSV（口径未验证）';
    const snapshotShort = typeof report.snapshot_generation === 'string'
      ? report.snapshot_generation.slice(0, 12)
      : '—';
    $('version').textContent = `模型版本：${report.model_id || '—'}　数据源：${sourceText}　快照：${snapshotShort}　名称：${report.symbol_name_source || '名称未知'}　数据版本：${report.data_version || '—'}　估计值仅为研究试算`;
    const currentModel = report.current_scoring_model || {};
    const evalWindow = report.models?.[report.selected_prediction_model || 'pooled_logistic']?.evaluation_window || {};
    const dateRange = (label, window) => `${label}${window?.signal_from || '—'} 至 ${window?.signal_through || '—'}`;
    $('model-window').textContent = [
      `当前评分模型 ${report.selected_prediction_model || 'pooled_logistic'}：${dateRange('训练 ', currentModel.training)}；${dateRange('独立校准 ', currentModel.calibration_window)}；已知标签截至 ${currentModel.latest_labeled_signal_date || '—'}。`,
      `历史诊断评估模型：${dateRange('训练 ', evalWindow.train)}；${dateRange('独立校准 ', evalWindow.calibration)}；${dateRange('测试 ', evalWindow.test)}。其后成熟的测试期标签可能已进入当前模型校准，因此这组测试指标不验证当前模型。`,
    ].join(' ');
    const metrics = report.frozen_final_test || {};
    const counts = report.sample_counts || {};
    $('sample-count').textContent = `${Number(metrics.sample_count || 0).toLocaleString('zh-CN')} 条 · ${Number(metrics.date_count || 0).toLocaleString('zh-CN')} 个交易日`;
    $('brier').textContent = number(metrics.brier_score, 4);
    $('log-loss').textContent = number(metrics.log_loss, 4);
    $('auc').textContent = number(metrics.roc_auc, 3);
    $('pr-auc').textContent = number(metrics.pr_auc, 3);
    const baseline = report.constant_up_probability_baseline || {};
    $('benchmark').textContent = `事前常数上涨概率 ${percent(baseline.probability)}（仅用训练与校准区估计）；历史留出区常数基线 Brier ${number(baseline.test_brier_score, 4)}、Log loss ${number(baseline.test_log_loss, 4)}。测试集事后实测上涨频率 ${percent(metrics.test_observed_up_rate)}；每日 Top-${metrics.top_k || 10} 平均实测上涨频率 ${percent(metrics.top_k_mean_daily_up_rate)}（每日平均选中 ${number(metrics.top_k_selected_per_day, 1)} 只）。AUC / PR-AUC 只表示排序能力。`;
    renderTable($('calibration-rows'), metrics.reliability_bins, 4, (row, item) => {
      addCell(row, `${percent(item.lower, 0)}–${percent(item.upper, 0)}`);
      addCell(row, percent(item.mean_predicted));
      addCell(row, percent(item.observed_up_rate));
      addCell(row, Number(item.sample_count || 0).toLocaleString('zh-CN'));
    }, '历史留出区没有足够样本形成分箱。');
    const predictions = report.predictions || [];
    $('prediction-count').textContent = `研究候选 ${predictions.length} 只 / 当前池 ${counts.current_scoring_universe || 0} 只`;
    $('prediction-context').textContent = `数据日 ${report.as_of || '—'} · 目标日 ${report.target_date || '—'} · 按 ${report.model_id || '研究模型'} 估计值由高到低；名称来源：${report.symbol_name_source || '名称未知'}。`;
    renderTable($('prediction-rows'), predictions, 4, (row, item) => {
      addCell(row, item.rank);
      addCell(row, item.symbol, 'symbol-cell');
      addCell(row, item.name || '名称未知');
      addCell(row, percent(item.research_probability));
    }, '当前快照没有可计算的研究估计值；质量门槛保持原判定。');
    renderModels(report.models, counts, report.validation_model_selection);
  }
  async function loadStatus() {
    try {
      const response = await fetch(statusUrl, {cache: 'no-store'});
      const payload = await response.json();
      setState(payload);
      if (!response.ok && response.status !== 503 && payload.message == null) {
        $('status-message').textContent = `读取研究状态失败（HTTP ${response.status}）。`;
      }
    } catch (error) {
      $('run-state').textContent = '连接失败';
      $('status-message').textContent = '无法读取本地研究服务，请确认 QTrade 正在运行。';
      runButton.disabled = false;
    }
  }
  function schedulePoll(delay) {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(async () => {
      await loadStatus();
      schedulePoll(latestState === 'queued' || latestState === 'running' ? 1800 : 15000);
    }, delay);
  }
  async function startResearch() {
    runButton.disabled = true;
    $('run-state').textContent = '正在请求本地研究服务';
    $('status-message').textContent = '研究只在当前本地行情快照和股票池上运行。';
    try {
      const response = await fetch(runUrl, {method: 'POST', cache: 'no-store'});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || (response.status === 403 ? '浏览器来源校验失败，请从 QTrade 内打开此页面。' : `研究启动失败（HTTP ${response.status}）。`));
      setState(payload);
      schedulePoll(800);
    } catch (error) {
      latestState = 'failed';
      $('run-state').textContent = '启动失败';
      $('status-message').textContent = error.message || '无法启动研究。';
      runButton.disabled = false;
      runButton.textContent = '重新开始';
    }
  }

  runButton.addEventListener('click', () => { void startResearch(); });
  void loadStatus().then(() => schedulePoll(latestState === 'queued' || latestState === 'running' ? 1800 : 15000));
})();
