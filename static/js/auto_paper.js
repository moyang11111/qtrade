/**
 * QTrade Desktop — 自动模拟盘独立界面
 *
 * 与训练营一致，使用全屏独立覆盖层展示；不再占用底部标签页。
 * 提供资产总览卡片、当前持仓表、最近交易表、资金曲线与控制操作。
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const els = {
    overlay: $('autoPaperOverlay'),
    statusBadge: $('autoStatusBadge'),
    error: $('autoError'),
    metrics: $('autoMetrics'),
    mode: $('autoSignalMode'),
    executionMode: $('autoExecutionMode'),
    manualForm: $('autoManualForm'),
    btnToggle: $('autoBtnToggle'),
    btnRun: $('autoBtnRun'),
    btnReset: $('autoBtnReset'),
    btnClose: $('autoBtnClose'),
    posCount: $('autoPosCount'),
    posTable: $('autoPosTable').querySelector('tbody'),
    tradeCount: $('autoTradeCount'),
    tradeTable: $('autoTradeTable').querySelector('tbody'),
    equityChart: $('autoEquityChart'),
  };

  let autoPaperState = null;
  let timer = null;
  let busy = false;
  let operationVersion = 0;
  let manualRequestId = null;

  // ---------- 生命周期 ----------

  function open() {
    els.overlay.hidden = false;
    refresh();
    if (timer) clearInterval(timer);
    timer = setInterval(() => {
      if (!els.overlay.hidden) refresh();
    }, 10000);
  }

  function close() {
    els.overlay.hidden = true;
    if (timer) clearInterval(timer);
    timer = null;
  }

  // ---------- 数据 ----------

  async function refresh() {
    if (busy) return;
    const version = operationVersion;
    try {
      const state = await API.getAutoPaper('status');
      if (busy || version !== operationVersion) return;
      autoPaperState = state;
      render(autoPaperState);
    } catch (e) {
      showError(`自动模拟盘加载失败：${e.message}`);
    }
  }

  function showError(msg) {
    els.error.textContent = msg;
    els.error.hidden = false;
  }

  function renderUniverseMetric(st) {
    const summary = st.universe_summary;
    if (!summary || typeof summary !== 'object') {
      return st.universe_size
        ? `<span class="mono">${fmt(st.universe_size)} 只</span>`
        : '<span class="muted">--</span>';
    }
    const count = (value) => Number.isFinite(Number(value)) ? fmt(Number(value)) : '--';
    const source = summary.source === 'external_sqlite' ? '底座只读'
      : summary.source === 'fallback' ? '回退池' : (summary.source || '--');
    const asOf = summary.as_of || '--';
    return `<span class="mono">主板总池 ${count(summary.total)} / 可计算 ${count(summary.computable)} / 候选 ${count(summary.candidate)}</span>`
      + `<span class="sub">${escapeHtml(source)} · ${escapeHtml(asOf)}</span>`;
  }

  // ---------- 渲染 ----------

  function render(st) {
    if (!st) return;
    const up = st.pnl >= 0;
    const colorCls = up ? 'up' : 'down';

    // 状态徽标
    const manual = st.execution_mode === 'manual';
    const confirmMode = st.execution_mode === 'confirm';
    els.statusBadge.textContent = manual ? '手动交易' : st.running ? (confirmMode ? '● 正在生成建议' : '● 策略运行中') : '○ 策略已暂停';
    els.statusBadge.className = 'auto-status ' + (st.running ? 'running' : 'paused');
    if (st.engine_owner === false) {
      els.statusBadge.textContent += '（后台驱动）';
    }

    const phase = $('autoExecutionPhase');
    if (phase) phase.textContent = `${st.execution_phase || '收盘生成信号，下一交易时段执行'} · 信号日 ${st.signal_date || '—'} · 执行日 ${st.execution_date || '—'} · 当前回撤 ${st.max_drawdown_pct ?? '—'}%（账户收益已扣费用，持仓盈亏为价差）`;
    renderExecutionRecords(st);

    // 控制按钮
    els.executionMode.value = st.execution_mode || 'auto';
    $('autoManualCard').hidden = !manual;
    els.btnToggle.textContent = st.running ? '⏸ 暂停策略' : confirmMode ? '▶ 启动建议监测' : '▶ 启动自动交易';
    els.btnToggle.disabled = manual || busy || st.engine_owner === false;
    els.btnRun.disabled = manual || busy || st.engine_owner === false;
    els.btnRun.textContent = confirmMode ? '生成一轮建议' : '立即跑一轮';
    els.mode.disabled = manual || busy;
    $('autoManualSession').textContent = st.manual_session_open ? '当前处于交易时段，成交前还会检查行情有效性与可用资金。' : '当前为休市或非连续交易时段，无法提交手动订单。';
    $('autoManualSubmit').disabled = busy || !st.manual_session_open || st.engine_owner === false;
    $('autoPendingHelp').textContent = confirmMode
      ? '建议须逐条确认。买入按账户资产的策略仓位比例计算数量，卖出使用可卖数量；暂停策略后，已确认订单也暂停执行。收盘止盈止损建议同样需要确认。'
      : manual ? '手动买卖使用表单中输入的数量；本模式不生成或执行策略建议。'
        : '收盘信号在下一交易时段执行。旧行情、休市或行情断线时等待，目标日结束后失效。可在成交前取消订单。';
    els.btnToggle.classList.toggle('warn', st.running);

    // 指标卡片
    const metrics = [
      { label: '总资产', html: `<span class="mono">¥${fmt(st.total)}</span>`, cls: '' },
      { label: '累计收益', html: `<span class="mono ${colorCls}">${up ? '+' : ''}¥${fmt(st.pnl)}</span><span class="sub ${colorCls}">(${up ? '+' : ''}${st.pnl_pct}%)</span>`, cls: colorCls },
      { label: '现金', html: `<span class="mono">¥${fmt(st.cash)}</span>`, cls: '' },
      { label: '持仓市值', html: `<span class="mono">¥${fmt(st.market_value)}</span>`, cls: '' },
      { label: '仓位', html: `<span class="mono">${st.position_count}/${st.max_positions}</span>`, cls: '' },
      { label: '股票池', html: renderUniverseMetric(st), cls: '' },
      { label: '上次轮询', html: st.last_run ? `<span class="mono">${st.last_run.slice(11)}</span>` : '<span class="muted">--</span>', cls: '' },
      { label: '运行状态', html: manual ? '<span>手动交易</span>' : st.running ? '<span class="up">● 运行中</span>' : '<span class="muted">○ 已暂停</span>', cls: st.running ? 'up' : '' },
    ];
    renderMetrics(metrics);

    // 信号源下拉框
    renderModeSelect(st);

    // 持仓表
    const positions = st.positions || [];
    els.posCount.textContent = `(${positions.length}/${st.max_positions})`;
    if (positions.length === 0) {
      els.posTable.innerHTML = `<tr><td colspan="10" class="auto-empty">暂无持仓，最多持有 ${st.max_positions} 只</td></tr>`;
    } else {
      els.posTable.innerHTML = positions.map(p => {
        const pnlCls = p.pnl_pct >= 0 ? 'up' : 'down';
        const targetGap = p.last_price ? ((p.target_price / p.last_price - 1) * 100).toFixed(1) : '--';
        const title = escapeAttr(`买入时间: ${p.buy_time || ''}\n信号: ${p.buy_reason || ''}`);
        const src = p.source === '手动' ? '<span class="badge-src badge-manual">手动</span>' : p.source === '决策' ? '<span class="badge-src badge-dec">决策</span>'
          : '<span class="badge-src badge-strat">策略</span>';
        return `<tr title="${title}">
          <td class="mono">${p.symbol} ${src}</td>
          <td class="num">${p.qty}<span class="sub">可卖 ${p.sellable_qty ?? 0}</span></td>
          <td class="num">${p.buy_price}</td>
          <td class="num">${p.last_price}</td>
          <td class="num up">${p.source === '手动' ? '—' : `${p.target_price}<span class="sub">(+${targetGap}%)</span>`}</td>
          <td class="num down">${p.source === '手动' ? '—' : p.stop_price}</td>
          <td class="num ${pnlCls}">${p.pnl_pct >= 0 ? '+' : ''}${p.pnl_pct}%</td>
          <td class="num">¥${fmt(p.value)}</td>
          <td class="muted">${(p.buy_time || '').slice(5, 16)}</td>
          <td><button class="btn" data-sell-symbol="${escapeAttr(p.symbol)}" ${!manual || busy || !(p.sellable_qty > 0) ? 'disabled' : ''}>卖出</button></td>
        </tr>`;
      }).join('');
    }

    // 最近交易
    const trades = st.trades || [];
    els.tradeCount.textContent = `(${trades.length})`;
    if (trades.length === 0) {
      els.tradeTable.innerHTML = `<tr><td colspan="7" class="auto-empty">暂无交易</td></tr>`;
    } else {
      els.tradeTable.innerHTML = trades.slice(0, 200).map(t => {
        const side = t.side === 'BUY' ? '买' : '卖';
        const sideCls = t.side === 'BUY' ? 'up' : 'down';
        const pnl = t.pnl_pct != null
          ? `<span class="${t.pnl_pct >= 0 ? 'up' : 'down'}">${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct}%</span>`
          : '<span class="muted">--</span>';
        return `<tr>
          <td class="muted">${(t.time || '').slice(5, 16)}</td>
          <td class="${sideCls}">${side}</td>
          <td class="mono">${t.symbol}</td>
          <td class="num">${t.price}</td>
          <td class="num">${t.qty}</td>
          <td class="num">${pnl}</td>
          <td class="muted reason-cell" title="${escapeAttr(t.reason || '')}">${escapeHtml(t.reason || '')}</td>
        </tr>`;
      }).join('');
    }

    // 资金曲线
    renderEquityChart(st.equity_hist || []);
  }

  function renderExecutionRecords(st) {
    const pending = $('autoPendingTable')?.querySelector('tbody');
    const count = $('autoPendingCount');
    if (count) count.textContent = `(待确认 ${st.approval_count || 0} / 待执行 ${st.pending_count || 0})`;
    const labels = {awaiting_confirmation:'待人工确认',pending:'已进入待执行队列',filled:'已成交',expired:'已失效',cancelled:'已取消',rejected:'已拒绝',skipped:'已跳过'};
    const disabled = busy || st.engine_owner === false ? 'disabled' : '';
    if (pending) pending.innerHTML = (st.pending_orders || []).map(item => {
      const id = escapeAttr(item.intent_id);
      const actions = item.status === 'awaiting_confirmation'
        ? `<button class="btn" data-paper-action="approve" data-intent-id="${id}" ${disabled}>确认${item.side === 'buy' ? '买入' : '卖出'}</button><button class="btn" data-paper-action="reject" data-intent-id="${id}" ${disabled}>拒绝</button>`
        : `<button class="btn" data-paper-action="cancel" data-intent-id="${id}" ${disabled}>取消</button>`;
      return `<tr><td>${escapeHtml(item.symbol)}</td><td>${item.side === 'buy' ? '买入' : '卖出'}</td><td>${escapeHtml(item.signal_date)}</td><td>${escapeHtml(item.execution_date)}</td><td>${labels[item.status] || escapeHtml(item.status)}<span class="sub">${escapeHtml(item.last_error || item.reason)}</span></td><td><div class="paper-actions">${actions}</div></td></tr>`;
    }).join('') || '<tr><td colspan="6" class="auto-empty">暂无待确认建议或待执行订单</td></tr>';
    $('autoOrderHistory').querySelector('tbody').innerHTML = (st.order_history || []).map(item =>
      `<tr><td>${escapeHtml(item.symbol)}</td><td>${item.side === 'buy' ? '买入' : '卖出'}</td><td>${escapeHtml(item.execution_date)}</td><td>${labels[item.status] || escapeHtml(item.status)}</td><td>${escapeHtml(item.reason)}</td></tr>`
    ).join('') || '<tr><td colspan="5" class="auto-empty">暂无建议处理记录；手动成交见最近交易</td></tr>';
    const observations = $('autoResearchTable')?.querySelector('tbody');
    const fraction = (metric) => metric ? `${metric.up}/${metric.up+metric.down+metric.flat}` : '—';
    if (observations) observations.innerHTML = (st.research_observations || []).map(item =>
      `<tr><td>${escapeHtml(item.signal_date)}</td><td>${escapeHtml(item.target_date)}</td><td>${item.prospective ? '开盘前冻结' : '事后历史核对'}</td><td>${fraction(item.outcome?.top10)}</td><td>${fraction(item.outcome?.top100)}</td><td>${item.outcome ? '已核对' : '等待完整目标日收盘数据'}</td></tr>`
    ).join('') || '<tr><td colspan="6" class="auto-empty">运行上涨研究后，名单会自动保存并在目标日数据更新后核对</td></tr>';
  }

  function renderMetrics(items) {
    const cards = els.metrics.querySelectorAll('.auto-metric');
    items.forEach((item, i) => {
      const card = cards[i];
      if (!card) return;
      const label = card.querySelector('.auto-metric-label');
      const value = card.querySelector('.auto-metric-value');
      if (label) label.textContent = item.label;
      if (value) {
        value.innerHTML = item.html;
        value.classList.remove('up', 'down', 'muted');
        if (item.cls) value.classList.add(item.cls);
      }
      card.classList.remove('loading');
    });
  }

  function renderModeSelect(st) {
    const modes = st.signal_modes || [];
    const current = st.signal_mode || '';
    // 首次填充选项；之后仅在选项集合变化时重建，避免打断用户操作
    if (els.mode.options.length === 0 || els.mode.options.length !== modes.length) {
      els.mode.innerHTML = modes.map(m =>
        `<option value="${escapeAttr(m.mode)}" ${m.mode === current ? 'selected' : ''}>${escapeHtml(m.label)}</option>`
      ).join('');
    } else {
      els.mode.value = current;
    }
  }

  function renderEquityChart(hist) {
    const svg = els.equityChart;
    if (!hist || hist.length < 2) {
      svg.innerHTML = '';
      return;
    }
    const w = 720, h = 160;
    const vals = hist.map(p => Number(p.total) || 0);
    const min = Math.min(...vals) * 0.995;
    const max = Math.max(...vals) * 1.005;
    const range = (max - min) || 1;
    const px = (i) => (i / (vals.length - 1)) * w;
    const py = (v) => h - ((v - min) / range) * h;
    const points = vals.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(' ');
    const start = vals[0], end = vals[vals.length - 1];
    const color = end >= start ? '#EB5757' : '#27AE60';
    const area = `0,${h} ${points} ${w},${h}`;
    svg.innerHTML = `
      <defs>
        <linearGradient id="autoEquityGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <polygon points="${area}" fill="url(#autoEquityGrad)"/>
      <polyline points="${points}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/>
      <text x="8" y="18" fill="#A1A1AA" font-size="11" font-family="var(--font)">¥${fmt(start)}</text>
      <text x="${w - 8}" y="18" fill="#A1A1AA" font-size="11" text-anchor="end" font-family="var(--font)">¥${fmt(end)}</text>
    `;
  }

  // ---------- 操作 ----------

  async function operate(action, payload, legacy = false) {
    if (busy) return null;
    busy = true;
    operationVersion += 1;
    els.error.hidden = true;
    els.executionMode.disabled = true;
    els.btnReset.disabled = true;
    if (autoPaperState) render(autoPaperState);
    try {
      const state = legacy ? await API.getAutoPaper(action, payload) : await API.paperAction(action, payload);
      autoPaperState = state;
      return state;
    } catch (e) {
      showError(e.payload?.error || e.message);
      return null;
    } finally {
      busy = false;
      els.executionMode.disabled = false;
      els.btnReset.disabled = false;
      if (autoPaperState) render(autoPaperState);
    }
  }

  async function submitManual(event) {
    event.preventDefault();
    const symbol = $('autoManualSymbol').value.trim();
    const side = $('autoManualSide').value;
    const qty = Number($('autoManualQty').value);
    if (!Number.isSafeInteger(qty) || qty <= 0 || (side === 'buy' && qty % 100)) {
      showError(side === 'buy' ? '买入数量须为100股的整数倍' : '请输入有效的卖出数量');
      return;
    }
    manualRequestId ||= crypto.randomUUID();
    const state = await operate('manual_trade', {symbol,side,qty,request_id:manualRequestId});
    if (state?.submitted_order) {
      const order = state.submitted_order;
      $('autoManualFeedback').textContent = `已成交：${symbol} ${side === 'buy' ? '买入' : '卖出'} ${order.filled_qty}股，成交价 ¥${order.avg_fill_price}。`;
      $('autoManualQty').value = '';
      manualRequestId = null;
    }
  }

  async function toggle() {
    await operate('toggle', null, true);
  }

  async function runOnce() {
    await operate('run', null, true);
  }

  async function reset() {
    if (!confirm('确定清仓重置模拟盘？\n初始资金 ¥100,000 将恢复，持仓与交易记录清空。')) return;
    await operate('reset', null, true);
  }

  async function changeMode() {
    const mode = els.mode.value;
    await operate('setmode', mode, true);
  }

  // ---------- 工具 ----------

  function fmt(v) {
    return Number(v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function escapeAttr(s) {
    return escapeHtml(s);
  }

  // ---------- 事件 ----------

  function init() {
    els.btnClose.addEventListener('click', close);
    els.btnToggle.addEventListener('click', toggle);
    els.btnRun.addEventListener('click', runOnce);
    els.btnReset.addEventListener('click', reset);
    els.mode.addEventListener('change', changeMode);
    els.executionMode.addEventListener('change', () => operate('set_execution_mode', {mode:els.executionMode.value}));
    els.manualForm.addEventListener('submit', submitManual);
    els.manualForm.addEventListener('input', () => { manualRequestId = null; $('autoManualFeedback').textContent = ''; });
    $('autoPendingTable').addEventListener('click', (event) => {
      const button = event.target.closest('[data-paper-action]');
      if (button && !button.disabled) operate(button.dataset.paperAction, {intent_id:button.dataset.intentId});
    });
    els.posTable.addEventListener('click', (event) => {
      const button = event.target.closest('[data-sell-symbol]');
      if (!button || button.disabled) return;
      const position = autoPaperState?.positions.find(p => p.symbol === button.dataset.sellSymbol);
      if (!position) return;
      $('autoManualSymbol').value = position.symbol;
      $('autoManualSide').value = 'sell';
      $('autoManualQty').value = position.sellable_qty;
      manualRequestId = null;
      $('autoManualCard').scrollIntoView({block:'nearest',behavior:'smooth'});
      $('autoManualQty').focus();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !els.overlay.hidden) close();
    });
  }

  window.AutoPaper = { open, close };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
