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
    els.error.hidden = true;
    try {
      autoPaperState = await API.getAutoPaper('status');
      render(autoPaperState);
    } catch (e) {
      showError(`自动模拟盘加载失败：${e.message}`);
    }
  }

  function showError(msg) {
    els.error.textContent = msg;
    els.error.hidden = false;
  }

  // ---------- 渲染 ----------

  function render(st) {
    if (!st) return;
    const up = st.pnl >= 0;
    const colorCls = up ? 'up' : 'down';

    // 状态徽标
    els.statusBadge.textContent = st.running ? '● 自动交易中' : '○ 已暂停';
    els.statusBadge.className = 'auto-status ' + (st.running ? 'running' : 'paused');
    if (st.engine_owner === false) {
      els.statusBadge.textContent += '（后台驱动）';
    }

    // 控制按钮
    els.btnToggle.textContent = st.running ? '⏸ 暂停自动交易' : '▶ 启动自动交易';
    els.btnToggle.classList.toggle('warn', st.running);

    // 指标卡片
    const metrics = [
      { label: '总资产', html: `<span class="mono">¥${fmt(st.total)}</span>`, cls: '' },
      { label: '累计收益', html: `<span class="mono ${colorCls}">${up ? '+' : ''}¥${fmt(st.pnl)}</span><span class="sub ${colorCls}">(${up ? '+' : ''}${st.pnl_pct}%)</span>`, cls: colorCls },
      { label: '现金', html: `<span class="mono">¥${fmt(st.cash)}</span>`, cls: '' },
      { label: '持仓市值', html: `<span class="mono">¥${fmt(st.market_value)}</span>`, cls: '' },
      { label: '仓位', html: `<span class="mono">${st.position_count}/${st.max_positions}</span>`, cls: '' },
      { label: '股票池', html: st.universe_size ? `<span class="mono">${fmt(st.universe_size)} 只</span>` : '<span class="muted">--</span>', cls: '' },
      { label: '上次轮询', html: st.last_run ? `<span class="mono">${st.last_run.slice(11)}</span>` : '<span class="muted">--</span>', cls: '' },
      { label: '运行状态', html: st.running ? '<span class="up">● 运行中</span>' : '<span class="muted">○ 已暂停</span>', cls: st.running ? 'up' : '' },
    ];
    renderMetrics(metrics);

    // 信号源下拉框
    renderModeSelect(st);

    // 持仓表
    const positions = st.positions || [];
    els.posCount.textContent = `(${positions.length}/${st.max_positions})`;
    if (positions.length === 0) {
      els.posTable.innerHTML = `<tr><td colspan="9" class="auto-empty">暂无持仓（信号触发后自动买入，最多 ${st.max_positions} 只）</td></tr>`;
    } else {
      els.posTable.innerHTML = positions.map(p => {
        const pnlCls = p.pnl_pct >= 0 ? 'up' : 'down';
        const targetGap = p.last_price ? ((p.target_price / p.last_price - 1) * 100).toFixed(1) : '--';
        const title = escapeAttr(`买入时间: ${p.buy_time || ''}\n信号: ${p.buy_reason || ''}`);
        const src = p.source === '决策' ? '<span class="badge-src badge-dec">决策</span>'
          : '<span class="badge-src badge-strat">策略</span>';
        return `<tr title="${title}">
          <td class="mono">${p.symbol} ${src}</td>
          <td class="num">${p.qty}</td>
          <td class="num">${p.buy_price}</td>
          <td class="num">${p.last_price}</td>
          <td class="num up">${p.target_price}<span class="sub">(+${targetGap}%)</span></td>
          <td class="num down">${p.stop_price}</td>
          <td class="num ${pnlCls}">${p.pnl_pct >= 0 ? '+' : ''}${p.pnl_pct}%</td>
          <td class="num">¥${fmt(p.value)}</td>
          <td class="muted">${(p.buy_time || '').slice(5, 16)}</td>
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

  async function toggle() {
    try {
      autoPaperState = await API.getAutoPaper('toggle');
      render(autoPaperState);
    } catch (e) {
      showError(`操作失败：${e.message}`);
    }
  }

  async function runOnce() {
    els.btnRun.disabled = true;
    els.btnRun.textContent = '⏳ 扫描中...';
    try {
      autoPaperState = await API.getAutoPaper('run');
      render(autoPaperState);
    } catch (e) {
      showError(`运行失败：${e.message}`);
    } finally {
      els.btnRun.disabled = false;
      els.btnRun.textContent = '⚡ 立即跑一轮';
    }
  }

  async function reset() {
    if (!confirm('确定清仓重置模拟盘？\n初始资金 ¥100,000 将恢复，持仓与交易记录清空。')) return;
    try {
      autoPaperState = await API.getAutoPaper('reset');
      render(autoPaperState);
    } catch (e) {
      showError(`重置失败：${e.message}`);
    }
  }

  async function changeMode() {
    const mode = els.mode.value;
    try {
      autoPaperState = await API.getAutoPaper('setmode', mode);
      render(autoPaperState);
    } catch (e) {
      showError(`切换信号源失败：${e.message}`);
      if (autoPaperState) renderModeSelect(autoPaperState);
    }
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
