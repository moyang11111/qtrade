/**
 * QTrade Desktop — 主应用逻辑
 *
 * 职责：UI 事件绑定、数据流编排、回测交互、底部标签页、日志。
 */
(() => {
  'use strict';

  // ======================== 状态 ========================
  const state = {
    allSymbols: [],
    activeSymbol: null,
    lastBacktest: null,   // 最近一次回测结果
    activePage: 'market',
    updateToken: null,
    factorCapabilities: null,
    factorPreview: null,
    factorLibraryItems: [],
  };

  const recentStocks = JSON.parse(localStorage.getItem('qtrade_recent') || '[]');
  const watchlist = JSON.parse(localStorage.getItem('qtrade_watchlist') || '[]');
  const logs = [];

  let chartManager = null;
  let updateMonitor = null;
  const EMBED_IFRAME_IDS = Object.freeze({
    portal: 'iframePortal',
    pitch: 'iframePitch',
    factorboard: 'iframeFactorBoard',
  });

  // ======================== DOM 引用 ========================
  const $ = (id) => document.getElementById(id);
  const els = {
    searchInput: $('searchInput'),
    stockList: $('stockList'),
    recentStocks: $('recentStocks'),
    watchlistStocks: $('watchlistStocks'),
    watchlistCount: $('watchlistCount'),
    stockCount: $('stockCount'),
    symName: $('symName'),
    symPrice: $('symPrice'),
    symChg: $('symChg'),
    indicatorToggles: $('indicatorToggles'),
    clock: $('clock'),
    signalLines: $('signalLines'),
    backtestModal: $('backtestModal'),
    btResult: $('btResult'),
    // 报价
    qName: $('qName'), qOpen: $('qOpen'), qHigh: $('qHigh'), qLow: $('qLow'),
    qH60: $('qH60'), qL60: $('qL60'), qVol: $('qVol'),
    qTurnover: $('qTurnover'), qTime: $('qTime'),
    // 决策台页面
    pageFactors: $('pageFactors'), pageRisk: $('pageRisk'), pageFactorBoard: $('pageFactorBoard'),
    factorSym: $('factorSym'), factorError: $('factorError'),
    factorLibraryList: $('factorLibraryList'), factorLibraryEmpty: $('factorLibraryEmpty'),
    btnOpenFactorBoard: $('btnOpenFactorBoard'),
    factorFilterPanel: $('factorFilterPanel'), factorFilterToggle: $('factorFilterToggle'),
    factorFilterBody: $('factorFilterBody'), factorStatusFilter: $('factorStatusFilter'),
    factorUsageFilter: $('factorUsageFilter'), factorLifecycleFilter: $('factorLifecycleFilter'),
    factorIcirMin: $('factorIcirMin'), factorIcirMax: $('factorIcirMax'),
    factorCrowdingMax: $('factorCrowdingMax'), factorKeyword: $('factorKeyword'),
    factorPlanName: $('factorPlanName'), factorPlanDescription: $('factorPlanDescription'),
    btnFactorFilterClear: $('btnFactorFilterClear'), btnFactorPreview: $('btnFactorPreview'),
    btnFactorSave: $('btnFactorSave'), factorCapabilitiesHint: $('factorCapabilitiesHint'),
    factorFilterMessage: $('factorFilterMessage'), factorPreview: $('factorPreview'),
    factorPreviewCount: $('factorPreviewCount'), factorPreviewDate: $('factorPreviewDate'),
    factorPreviewFactors: $('factorPreviewFactors'),
    riskBody: $('riskBody'), riskError: $('riskError'),
    updateStatus: $('updateStatus'),
  };

  // ======================== 日志 ========================
  function log(msg) {
    const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    logs.unshift(`[${ts}] ${msg}`);
    if (logs.length > 200) logs.pop();
  }

  // ======================== 股票列表 ========================
  async function loadSymbols() {
    try {
      state.allSymbols = await API.listSymbols();
      els.stockCount.textContent = `(${state.allSymbols.length})`;
      renderStockList();
      renderRecent();
      log(`加载完成：${state.allSymbols.length} 只股票`);
    } catch (e) {
      els.stockList.innerHTML = '<div class="stock-item muted">加载失败，请检查后端服务</div>';
      log(`⚠ 股票列表加载失败: ${e.message}`);
    }
  }

  function currentFilter() {
    return els.searchInput.value.trim();
  }

  function renderStockList() {
    const q = currentFilter();
    const list = q ? state.allSymbols.filter(s => s.includes(q)) : state.allSymbols;
    const show = list.slice(0, 300);

    els.stockList.innerHTML = '';

    // 输入 6 位数字且不在列表时，提供"实时查询"入口（任意代码）
    if (/^\d{6}$/.test(q) && !list.includes(q)) {
      const live = document.createElement('div');
      live.className = 'stock-item';
      live.innerHTML = `<span class="sym">🔍 实时查询 ${q}</span>`;
      live.onclick = () => selectStock(q);
      els.stockList.appendChild(live);
    }

    for (const sym of show) {
      const div = document.createElement('div');
      const inWL = watchlist.includes(sym);
      div.className = 'stock-item' + (sym === state.activeSymbol ? ' active' : '');
      div.innerHTML = `<span class="sym">${sym}</span>
        <span class="wl-star" data-sym="${sym}" style="cursor:pointer;font-size:13px;color:${inWL ? 'var(--action)' : 'var(--text)'};padding:0 6px;font-weight:bold">${inWL ? '★' : '☆'}</span>`;
      div.onclick = (e) => {
        if (e.target.classList.contains('wl-star')) {
          e.stopPropagation();
          toggleWatchlist(sym);
          return;
        }
        selectStock(sym);
      };
      els.stockList.appendChild(div);
    }
  }

  function renderWatchlist() {
    els.watchlistStocks.innerHTML = '';
    els.watchlistCount.textContent = `(${watchlist.length})`;
    for (const sym of watchlist.slice(0, 10)) {
      const div = document.createElement('div');
      div.className = 'stock-item' + (sym === state.activeSymbol ? ' active' : '');
      div.innerHTML = `<span class="sym">${sym}</span><span class="chg" style="cursor:pointer;color:var(--text-dim)" data-rm="${sym}">&times;</span>`;
      div.onclick = (e) => { if (e.target.dataset.rm) { removeFromWatchlist(sym); return; } selectStock(sym); };
      els.watchlistStocks.appendChild(div);
    }
  }

  function renderRecent() {
    els.recentStocks.innerHTML = '';
    for (const sym of recentStocks.slice(0, 6)) {
      const div = document.createElement('div');
      div.className = 'stock-item' + (sym === state.activeSymbol ? ' active' : '');
      div.innerHTML = `<span class="sym">${sym}</span>`;
      div.onclick = () => selectStock(sym);
      els.recentStocks.appendChild(div);
    }
  }

  // ======================== 选股 ========================
  function toggleWatchlist(symbol) {
    const idx = watchlist.indexOf(symbol);
    if (idx >= 0) { watchlist.splice(idx, 1); }
    else { watchlist.unshift(symbol); while (watchlist.length > 50) watchlist.pop(); }
    localStorage.setItem('qtrade_watchlist', JSON.stringify(watchlist));
    renderWatchlist();
    renderStockList();   // 刷新星号状态
    updateWatchlistBtn();
  }

  function removeFromWatchlist(symbol) {
    const idx = watchlist.indexOf(symbol);
    if (idx >= 0) { watchlist.splice(idx, 1); }
    localStorage.setItem('qtrade_watchlist', JSON.stringify(watchlist));
    renderWatchlist();
    renderStockList();
    updateWatchlistBtn();
  }

  function updateWatchlistBtn() {
    const btn = $('btnWatchlist');
    if (!state.activeSymbol) { btn.textContent = '加自选'; return; }
    const inList = watchlist.includes(state.activeSymbol);
    btn.textContent = inList ? '★ 已自选' : '☆ 加自选';
    btn.style.color = inList ? 'var(--action)' : '';
  }

  async function selectStock(symbol) {
    state.activeSymbol = symbol;

    // 更新最近浏览
    const idx = recentStocks.indexOf(symbol);
    if (idx >= 0) recentStocks.splice(idx, 1);
    recentStocks.unshift(symbol);
    while (recentStocks.length > 10) recentStocks.pop();
    localStorage.setItem('qtrade_recent', JSON.stringify(recentStocks));

    renderStockList();
    renderRecent();
    renderWatchlist();
    updateWatchlistBtn();
    $('btSymbol').value = symbol;
    log(`📌 切换到 ${symbol}`);

    try {
      const [info, kline, ind] = await Promise.all([
        API.getInfo(symbol),
        API.getKline(symbol, 400),
        API.getIndicators(symbol),
      ]);

      chartManager.setKline(kline);
      chartManager.setIndicators(ind);
      chartIndicatorCache = ind;   // 供信号面板读取
      // 标题显示名称 + 代码（实时模式有 name）
      els.symName.textContent = (info && info.name) ? `${info.name} ${symbol}` : symbol;
      updateQuote(info);
      updateSignals(info);
    } catch (e) {
      log(`⚠ 加载 ${symbol} 失败: ${e.message}`);
    }
  }

  // ======================== 报价 ========================
  function updateQuote(info) {
    if (!info || Object.keys(info).length === 0) return;

    const chg = info.change || 0;
    const chgPct = info.change_pct || 0;
    const isUp = chg >= 0;
    const color = isUp ? 'var(--green)' : 'var(--red)';

    els.symPrice.textContent = (info.latest ?? '——').toFixed(2);
    els.symPrice.style.color = color;
    els.symChg.textContent =
      `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}  ${chgPct >= 0 ? '+' : ''}${chgPct.toFixed(2)}%`;
    els.symChg.style.background = isUp ? 'rgba(235,87,87,0.16)' : 'rgba(39,174,96,0.16)';
    els.symChg.style.color = color;

    els.qName.textContent = info.name || '——';
    els.qOpen.textContent = info.open ?? '——';
    els.qHigh.textContent = info.high ?? '——';
    els.qLow.textContent = info.low ?? '——';
    els.qH60.textContent = info.high_60d ?? '——';
    els.qL60.textContent = info.low_60d ?? '——';
    els.qVol.textContent = info.vol_avg_20d ? (info.vol_avg_20d / 1e6).toFixed(1) + 'M' : '——';
    els.qTurnover.textContent = info.turnover != null ? info.turnover + '%' : '——';
    els.qTime.textContent = formatQuoteTime(info.time);
  }

  /** 格式化腾讯时间戳 20260804161455 → 2026-08-04 16:14 */
  function formatQuoteTime(t) {
    if (!t) return '——';
    const s = String(t);
    if (s.length !== 14) return s;
    return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)} ${s.slice(8, 10)}:${s.slice(10, 12)}`;
  }

  // ======================== 信号 ========================
  function updateSignals(info) {
    if (!info || Object.keys(info).length === 0) {
      els.signalLines.innerHTML = '<div class="signal-line muted">请选择股票</div>';
      return;
    }

    const lines = [];
    const latest = info.latest;

    // 双均线信号（基于报价快照的近似判断，精确值由后端指标提供）
    const ind = chartIndicatorCache;
    if (ind && ind.mas && ind.mas.ma5 && ind.mas.ma20) {
      const last5 = lastValue(ind.mas.ma5);
      const last20 = lastValue(ind.mas.ma20);
      const prev5 = prevValue(ind.mas.ma5);
      const prev20 = prevValue(ind.mas.ma20);

      if (last5 != null && last20 != null) {
        if (last5 > last20) {
          lines.push('<span class="bull">🟢 双均线: 多头排列 (MA5 &gt; MA20)</span>');
          if (prev5 <= prev20) lines.push('↳ <span class="bull">金叉信号!</span>');
        } else {
          lines.push('<span class="bear">🔴 双均线: 空头排列 (MA5 &lt; MA20)</span>');
          if (prev5 >= prev20) lines.push('↳ <span class="bear">死叉信号!</span>');
        }
      }
    }

    // RSI
    if (ind && ind.rsi) {
      const rsiVal = lastValue(ind.rsi);
      if (rsiVal != null) {
        if (rsiVal > 70) lines.push(`<span class="bear">🔴 RSI(14)=${rsiVal.toFixed(1)} → 超买</span>`);
        else if (rsiVal < 30) lines.push(`<span class="bull">🟢 RSI(14)=${rsiVal.toFixed(1)} → 超卖</span>`);
        else lines.push(`⚪ RSI(14)=${rsiVal.toFixed(1)} → 中性`);
      }
    }

    // 距60日高点
    if (info.high_60d && latest) {
      const pullback = (latest / info.high_60d - 1) * 100;
      lines.push(`📉 距60日高点: ${pullback >= 0 ? '+' : ''}${pullback.toFixed(1)}%`);
    }

    els.signalLines.innerHTML = lines.map(l => `<div class="signal-line">${l}</div>`).join('');
  }

  // 指标缓存（selectStock 时写入，供信号面板读取）
  let chartIndicatorCache = null;

  /** 从 {time, value} 对象数组中取最后一个非空 value */
  function lastValue(objArr) {
    if (!objArr) return null;
    for (let i = objArr.length - 1; i >= 0; i--) {
      if (objArr[i] && objArr[i].value != null) return objArr[i].value;
    }
    return null;
  }
  /** 从 {time, value} 对象数组中取倒数第二个非空 value */
  function prevValue(objArr) {
    if (!objArr) return null;
    let found = 0;
    for (let i = objArr.length - 1; i >= 0; i--) {
      if (objArr[i] && objArr[i].value != null) {
        found++;
        if (found === 2) return objArr[i].value;
      }
    }
    return null;
  }

  // ======================== 回测 ========================
  function openBacktest() {
    $('btSymbol').value = state.activeSymbol || '';
    els.btResult.hidden = true;
    els.backtestModal.hidden = false;
    const cfRow = $('customFactorsRow');
    if (cfRow && $('btStrategy')) cfRow.hidden = $('btStrategy').value !== 'factor_score_custom';
  }

  function closeBacktest() {
    els.backtestModal.hidden = true;
  }

  async function runBacktest() {
    const symbol = $('btSymbol').value.trim();
    const strategy = $('btStrategy').value;
    const capital = parseFloat($('btCapital').value) || 100000;
    const commission = (parseFloat($('btCommission').value) || 0.03) / 100;
    const stopLoss = (parseFloat($('btStopLoss').value) || 5) / 100;
    const takeProfit = (parseFloat($('btTakeProfit').value) || 15) / 100;

    if (!symbol) { alert('请输入股票代码'); return; }

    els.btResult.hidden = false;
    els.btResult.innerHTML = '<div class="muted" style="text-align:center;padding:20px;">⏳ 正在运行回测...</div>';

    try {
      const params = { symbol, strategy, capital, commission, stopLoss, takeProfit };
      if (strategy === 'factor_score_custom') {
        params.factors = $('btFactors').value.trim();
        params.weights = $('btWeights').value.trim();
      }
      const data = await API.runBacktest(params);

      if (data.error) {
        els.btResult.innerHTML = `<div style="color:var(--red);">${data.error}</div>`;
        return;
      }

      state.lastBacktest = data;
      renderBacktestResult(data);
      switchTab('trades');   // 自动切到交易记录
      log(`✅ 回测完成: ${strategy} @ ${symbol} 收益 ${data.total_return}%`);
    } catch (e) {
      els.btResult.innerHTML = `<div style="color:var(--red);">回测失败: ${e.message}</div>`;
    }
  }

  function renderBacktestResult(data) {
    const isPos = data.total_return >= 0;
    const color = isPos ? 'var(--green)' : 'var(--red)';

    els.btResult.innerHTML = `
      <div style="font-weight:700;margin-bottom:8px;color:${color};">
        ${data.strategy} — ${data.symbol}
      </div>
      <div class="result-metric"><span>总收益率</span>
        <span class="val" style="color:${color};">${data.total_return >= 0 ? '+' : ''}${data.total_return}%</span></div>
      <div class="result-metric"><span>年化收益</span>
        <span class="val">${data.annual_return >= 0 ? '+' : ''}${data.annual_return}%</span></div>
      <div class="result-metric"><span>最大回撤</span>
        <span class="val" style="color:var(--red);">${data.max_drawdown}%</span></div>
      <div class="result-metric"><span>胜率</span>
        <span class="val">${data.win_rate}% (${data.wins}/${data.total_trades})</span></div>
      <div class="result-metric"><span>最终资金</span>
        <span class="val">¥${data.final_value.toLocaleString()}</span></div>
      <div class="muted" style="margin-top:8px;">✅ 共 ${data.total_trades} 笔交易</div>
    `;
  }

  // ======================== 底部标签页（已移除底部终端窗）=======================
  function switchTab(name) { /* 底部面板已删除，保留空实现避免报错 */ }
  function renderTab(name) { /* 底部面板已删除 */ }

  // ======================== AI 模拟盘（旧底部标签遗留，保留定义但不渲染）=======================
  let aiPaperState = null;

  async function renderAiTab() {
    els.tabContent.innerHTML = '<div class="muted">加载 AI 模拟盘...</div>';
    try {
      const st = await API.getAiPaper('status');
      aiPaperState = st;
      renderAiPaper();
    } catch (e) {
      els.tabContent.innerHTML = `<div class="muted">AI 模拟盘加载失败: ${e.message}</div>`;
    }
  }

  function renderAiPaper() {
    const st = aiPaperState;
    if (!st) return;
    const up = st.pnl >= 0;
    const color = up ? 'var(--green)' : 'var(--red)';

    const posRows = (st.positions || []).map(p => {
      const c = p.pnl_pct >= 0 ? 'var(--green)' : 'var(--red)';
      return `<div class="trade-row">
        <span style="width:90px;">${p.symbol}</span>
        <span style="width:70px;text-align:right;">${p.qty}股</span>
        <span style="width:80px;text-align:right;">成本 ${p.avg_cost}</span>
        <span style="width:80px;text-align:right;">现价 ${p.last_price}</span>
        <span style="width:80px;text-align:right;color:${c};">${p.pnl_pct >= 0 ? '+' : ''}${p.pnl_pct}%</span>
      </div>`;
    }).join('') || '<div class="muted">暂无持仓</div>';

    const tradeRows = (st.trades || []).slice(0, 20).map(t => {
      const c = t.side === 'BUY' ? 'var(--green)' : 'var(--red)';
      const pnl = t.pnl_pct != null ? ` | <span class="${t.pnl_pct >= 0 ? 'pnl-pos' : 'pnl-neg'}">${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct}%</span>` : '';
      return `<div class="trade-row">
        <span class="${t.side === 'BUY' ? 'buy' : 'sell'}" style="width:50px;">${t.side}</span>
        <span style="width:80px;">${t.symbol}</span>
        <span style="width:80px;text-align:right;">@${t.price}</span>
        <span style="width:70px;text-align:right;">${t.qty}股</span>
        <span style="flex:1;color:var(--text-muted);">${t.reason || ''}</span>${pnl}
      </div>`;
    }).join('') || '<div class="muted">暂无交易（点击「同步 AI 信号」按 AI 信号建仓）</div>';

    els.tabContent.innerHTML = `
      <div style="margin-bottom:6px;">
        总资产 <b style="color:${color};">¥${st.total.toLocaleString()}</b>
        | 现金 ¥${st.cash.toLocaleString()}
        | 市值 ¥${st.market_value.toLocaleString()}
        | 收益 <span style="color:${color};">${st.pnl >= 0 ? '+' : ''}¥${st.pnl.toLocaleString()} (${st.pnl_pct >= 0 ? '+' : ''}${st.pnl_pct}%)</span>
        <button class="btn" style="height:22px;margin-left:8px;padding:0 10px;" onclick="window.__aiPaperSync()">🔄 同步 AI 信号</button>
        <button class="btn" style="height:22px;padding:0 10px;" onclick="window.__aiPaperMark()">💰 刷新估值</button>
      </div>
      <div style="margin-bottom:4px;color:var(--text-secondary);">持仓 (${(st.positions||[]).length}):</div>
      ${posRows}
      <div style="margin-top:8px;color:var(--text-secondary);">最近交易:</div>
      ${tradeRows}
    `;
    window.__aiPaperSync = async () => {
      try { aiPaperState = await API.getAiPaper('sync'); renderAiPaper(); log('✅ 已按 AI 信号调仓'); }
      catch (e) { log(`⚠ 同步失败: ${e.message}`); }
    };
    window.__aiPaperMark = async () => {
      try { aiPaperState = await API.getAiPaper('mark'); renderAiPaper(); }
      catch (e) { log(`⚠ 刷新失败: ${e.message}`); }
    };
  }

  function renderTradesTab() {
    const data = state.lastBacktest;
    if (!data || !data.trades || data.trades.length === 0) {
      els.tabContent.innerHTML = '<div class="muted">运行回测后将在此显示交易记录。</div>';
      return;
    }

    const typeMap = {
      buy: ['buy', '买入'],
      sell: ['sell', '卖出'],
      stop_loss: ['stop', '止损'],
      take_profit: ['take', '止盈'],
      close: ['sell', '清仓'],
    };

    const rows = data.trades.map(t => {
      const [cls, label] = typeMap[t.type] || ['sell', t.type];
      const pnlClass = t.pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
      return `<div class="trade-row">
        <span class="muted" style="width:90px;">${t.date}</span>
        <span class="${cls}" style="width:60px;">${label}</span>
        <span style="width:90px;text-align:right;">@${t.price}</span>
        <span class="${pnlClass}" style="width:90px;text-align:right;">${t.pnl >= 0 ? '+' : ''}${t.pnl}%</span>
      </div>`;
    }).join('');

    els.tabContent.innerHTML = rows;
  }

  function renderEquityTab() {
    const data = state.lastBacktest;
    if (!data || !data.equity || data.equity.length === 0) {
      els.tabContent.innerHTML = '<div class="muted">运行回测后将在此显示资金曲线。</div>';
      return;
    }

    const eq = data.equity;
    const start = eq[0];
    const end = eq[eq.length - 1];
    const peak = Math.max(...eq);
    const color = end >= start ? 'var(--green)' : 'var(--red)';

    // SVG 资金曲线
    const w = 720, h = 100;
    const min = Math.min(...eq) * 0.99;
    const max = Math.max(...eq) * 1.01;
    const px = (i) => (i / (eq.length - 1)) * w;
    const py = (v) => h - ((v - min) / (max - min)) * h;
    const points = eq.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(' ');

    els.tabContent.innerHTML = `
      <div class="muted" style="margin-bottom:6px;">
        资金曲线: ¥${start.toLocaleString()} → ¥${end.toLocaleString()}
        (<span style="color:${color};">${data.total_return >= 0 ? '+' : ''}${data.total_return}%</span>)
        &nbsp;|&nbsp; 峰值: ¥${peak.toLocaleString()}
      </div>
      <svg width="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="max-width:720px;">
        <polyline points="${points}" fill="none"
          stroke="${end >= start ? '#EB5757' : '#27AE60'}" stroke-width="1.5" />
      </svg>
    `;
  }

  function renderLogTab() {
    els.tabContent.innerHTML = logs.map(l =>
      `<div class="trade-row"><span class="muted" style="width:70px;">${l.split(']')[0]}]</span><span>${l.split(']').slice(1).join(']')}</span></div>`
    ).join('') || '<div class="muted">暂无日志</div>';
  }

  // ======================== 决策台页面 ========================
  function setRailActive(page) {
    state.activePage = page;
    document.querySelectorAll('.deck-item').forEach(b =>
      b.classList.toggle('active', b.dataset.page === page));
  }

  const FACTOR_CONDITION_LABELS = Object.freeze({
    status: '状态', usage: '使用层', lifecycle: '生命周期',
    icir120_min: 'ICIR120 ≥', icir120_max: 'ICIR120 ≤', crowding_max: '拥挤度 ≤',
    keyword: '关键词',
  });
  let factorPreviewRequestId = 0;
  let factorLibraryRequestId = 0;
  let factorCapabilitiesRequestId = 0;
  let factorPreviewController = null;
  let factorLibraryController = null;
  let factorCapabilitiesController = null;

  function clearElement(element) {
    if (!element) return;
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function setFactorMessage(message, kind = 'info') {
    if (!els.factorFilterMessage) return;
    els.factorFilterMessage.textContent = message || '';
    els.factorFilterMessage.dataset.kind = kind;
    els.factorFilterMessage.hidden = !message;
  }

  function factorErrorMessage(error, fallback = '请求失败，请稍后重试') {
    if (error && error.name === 'AbortError') return '';
    if (error && error.payload && typeof error.payload.message === 'string') {
      return error.payload.message;
    }
    if (error && error.status === 503) return '当前因子数据不可用，请确认同日数据完整后重试';
    if (error && error.status === 422) return '筛选条件或方案内容不符合要求';
    return (error && error.message) || fallback;
  }

  function selectedFactorValues(select) {
    return select ? Array.from(select.selectedOptions).map(option => option.value) : [];
  }

  function readFactorConditions() {
    const raw = {
      status: selectedFactorValues(els.factorStatusFilter),
      usage: selectedFactorValues(els.factorUsageFilter),
      lifecycle: selectedFactorValues(els.factorLifecycleFilter),
      icir120_min: els.factorIcirMin && els.factorIcirMin.value,
      icir120_max: els.factorIcirMax && els.factorIcirMax.value,
      crowding_max: els.factorCrowdingMax && els.factorCrowdingMax.value,
      keyword: els.factorKeyword && els.factorKeyword.value,
    };
    return window.QTradeFactorLibrary
      ? window.QTradeFactorLibrary.serializeConditions(raw)
      : raw;
  }

  function setMultiSelectValues(select, values) {
    if (!select) return;
    const wanted = new Set(Array.isArray(values) ? values : values == null ? [] : [values]);
    Array.from(select.options).forEach(option => { option.selected = wanted.has(option.value); });
  }

  function setFactorConditions(conditions = {}) {
    setMultiSelectValues(els.factorStatusFilter, conditions.status);
    setMultiSelectValues(els.factorUsageFilter, conditions.usage);
    setMultiSelectValues(els.factorLifecycleFilter, conditions.lifecycle);
    if (els.factorIcirMin) els.factorIcirMin.value = conditions.icir120_min ?? '';
    if (els.factorIcirMax) els.factorIcirMax.value = conditions.icir120_max ?? '';
    if (els.factorCrowdingMax) els.factorCrowdingMax.value = conditions.crowding_max ?? '';
    if (els.factorKeyword) els.factorKeyword.value = conditions.keyword || '';
  }

  function renderFactorFacet(select, values, selected) {
    if (!select) return;
    clearElement(select);
    for (const value of Array.isArray(values) ? values : []) {
      if (typeof value !== 'string' || !value) continue;
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      option.selected = Array.isArray(selected) && selected.includes(value);
      select.appendChild(option);
    }
  }

  function renderFactorCapabilities(capabilities) {
    const facets = capabilities && capabilities.facets;
    if (!facets || typeof facets !== 'object') throw new Error('因子筛选条件不可用');
    const previous = readFactorConditions();
    renderFactorFacet(els.factorStatusFilter, facets.status, previous.status);
    renderFactorFacet(els.factorUsageFilter, facets.usage, previous.usage);
    renderFactorFacet(els.factorLifecycleFilter, facets.lifecycle, previous.lifecycle);
    const numeric = capabilities.numeric || {};
    const icir = numeric.icir120 || {};
    const crowding = numeric.crowding || {};
    if (els.factorIcirMin) {
      if (Number.isFinite(icir.min)) els.factorIcirMin.min = icir.min;
      if (Number.isFinite(icir.max)) els.factorIcirMin.max = icir.max;
    }
    if (els.factorIcirMax) {
      if (Number.isFinite(icir.min)) els.factorIcirMax.min = icir.min;
      if (Number.isFinite(icir.max)) els.factorIcirMax.max = icir.max;
    }
    if (els.factorCrowdingMax) {
      if (Number.isFinite(crowding.min)) els.factorCrowdingMax.min = crowding.min;
      if (Number.isFinite(crowding.max)) els.factorCrowdingMax.max = crowding.max;
    }
    const date = typeof capabilities.as_of === 'string' ? capabilities.as_of : '--';
    els.factorCapabilitiesHint.textContent = `同日因子数据：${date} · 条件选项来自当前数据`;
  }

  async function loadFactorCapabilities(force = false) {
    if (state.factorCapabilities && !force) return state.factorCapabilities;
    const requestId = ++factorCapabilitiesRequestId;
    if (factorCapabilitiesController) factorCapabilitiesController.abort();
    factorCapabilitiesController = typeof AbortController !== 'undefined' ? new AbortController() : null;
    try {
      const result = await API.getFactorLibraryCapabilities(
        factorCapabilitiesController ? { signal: factorCapabilitiesController.signal } : {}
      );
      if (requestId !== factorCapabilitiesRequestId) return null;
      state.factorCapabilities = result;
      renderFactorCapabilities(result);
      return result;
    } catch (error) {
      if (requestId === factorCapabilitiesRequestId && error.name !== 'AbortError') {
        els.factorCapabilitiesHint.textContent = factorErrorMessage(error, '无法读取因子筛选条件');
        setFactorMessage(factorErrorMessage(error), 'error');
      }
      return null;
    } finally {
      if (requestId === factorCapabilitiesRequestId) factorCapabilitiesController = null;
    }
  }

  function setFactorFilterBusy(busy) {
    [els.btnFactorFilterClear, els.btnFactorPreview, els.btnFactorSave].forEach(button => {
      if (button) button.disabled = busy;
    });
  }

  function addFactorChip(container, text, className = '') {
    const chip = document.createElement('span');
    chip.className = `factor-chip${className ? ` ${className}` : ''}`;
    chip.textContent = text;
    container.appendChild(chip);
  }

  function renderFactorPreview(result) {
    if (!els.factorPreview) return;
    els.factorPreview.hidden = false;
    els.factorPreviewCount.textContent = `命中 ${Number.isFinite(result.match_count) ? result.match_count : 0} 个因子`;
    els.factorPreviewDate.textContent = `数据日期：${result.as_of || '--'}`;
    clearElement(els.factorPreviewFactors);
    for (const factor of Array.isArray(result.matched_factors) ? result.matched_factors : []) {
      addFactorChip(els.factorPreviewFactors, factor);
    }
  }

  async function previewFactorSelection() {
    const requestId = ++factorPreviewRequestId;
    if (factorPreviewController) factorPreviewController.abort();
    factorPreviewController = typeof AbortController !== 'undefined' ? new AbortController() : null;
    setFactorFilterBusy(true);
    setFactorMessage('正在预览当前同日因子…');
    try {
      const result = await API.previewFactorLibrary(
        readFactorConditions(),
        factorPreviewController ? { signal: factorPreviewController.signal } : {}
      );
      if (requestId !== factorPreviewRequestId) return null;
      state.factorPreview = result;
      renderFactorPreview(result);
      setFactorMessage(`预览完成：命中 ${result.match_count || 0} 个因子`, 'success');
      return result;
    } catch (error) {
      if (requestId === factorPreviewRequestId && error.name !== 'AbortError') {
        state.factorPreview = null;
        if (els.factorPreview) els.factorPreview.hidden = true;
        setFactorMessage(factorErrorMessage(error, '预览失败，请稍后重试'), 'error');
      }
      return null;
    } finally {
      if (requestId === factorPreviewRequestId) {
        factorPreviewController = null;
        setFactorFilterBusy(false);
      }
    }
  }

  async function saveFactorPlan() {
    const name = els.factorPlanName && els.factorPlanName.value.trim();
    if (!name) {
      setFactorMessage('请先填写方案名称', 'error');
      if (els.factorPlanName) els.factorPlanName.focus();
      return null;
    }
    setFactorFilterBusy(true);
    setFactorMessage('正在保存方案…');
    try {
      const result = await API.createFactorLibrary({
        name,
        description: els.factorPlanDescription ? els.factorPlanDescription.value.trim() : '',
        conditions: readFactorConditions(),
      });
      setFactorMessage(`方案“${result.name || name}”已保存`, 'success');
      if (els.factorPlanName) els.factorPlanName.value = '';
      if (els.factorPlanDescription) els.factorPlanDescription.value = '';
      void loadFactorLibrary(true);
      return result;
    } catch (error) {
      setFactorMessage(factorErrorMessage(error, '方案保存失败，请稍后重试'), 'error');
      return null;
    } finally {
      setFactorFilterBusy(false);
    }
  }

  function formatFactorCondition(key, value) {
    const label = FACTOR_CONDITION_LABELS[key] || key;
    if (Array.isArray(value)) return `${label}：${value.join('、')}`;
    return `${label}：${value}`;
  }

  function renderPlanConditions(container, conditions) {
    for (const [key, value] of Object.entries(conditions || {})) {
      addFactorChip(container, formatFactorCondition(key, value), 'condition-chip');
    }
    if (!container.childElementCount) addFactorChip(container, '未设置条件', 'condition-chip');
  }

  function renderPlanFactors(details, factors) {
    const summary = document.createElement('summary');
    summary.textContent = `命中因子（${Array.isArray(factors) ? factors.length : 0}）`;
    details.appendChild(summary);
    const content = document.createElement('div');
    content.className = 'factor-chip-list';
    for (const factor of Array.isArray(factors) ? factors : []) addFactorChip(content, factor);
    if (!content.childElementCount) {
      const empty = document.createElement('span');
      empty.className = 'muted';
      empty.textContent = '暂无命中因子';
      content.appendChild(empty);
    }
    details.appendChild(content);
  }

  function setPlanCardMessage(card, message, kind = 'info') {
    const messageElement = card.querySelector('.factor-plan-message');
    if (!messageElement) return;
    messageElement.textContent = message || '';
    messageElement.dataset.kind = kind;
    messageElement.hidden = !message;
  }

  function renderFactorPlanCard(item) {
    const card = document.createElement('article');
    card.className = 'auto-card factor-plan-card';
    card.dataset.planId = item.id;

    const heading = document.createElement('div');
    heading.className = 'factor-plan-heading';
    const title = document.createElement('h3');
    title.textContent = item.name || '未命名方案';
    heading.appendChild(title);
    const meta = document.createElement('div');
    meta.className = 'factor-plan-meta muted';
    meta.textContent = `数据日期：${item.as_of || '--'} · 更新时间：${item.updated_at || '--'} · 命中：${item.match_count ?? 0}`;
    heading.appendChild(meta);
    card.appendChild(heading);

    if (item.description) {
      const description = document.createElement('p');
      description.className = 'factor-plan-description';
      description.textContent = item.description;
      card.appendChild(description);
    }
    const conditionBlock = document.createElement('div');
    conditionBlock.className = 'factor-plan-section';
    const conditionLabel = document.createElement('span');
    conditionLabel.className = 'factor-plan-label';
    conditionLabel.textContent = '筛选条件';
    conditionBlock.appendChild(conditionLabel);
    const conditionChips = document.createElement('div');
    conditionChips.className = 'factor-chip-list';
    renderPlanConditions(conditionChips, item.conditions);
    conditionBlock.appendChild(conditionChips);
    card.appendChild(conditionBlock);

    const factorBlock = document.createElement('details');
    factorBlock.className = 'factor-plan-section factor-plan-factors';
    renderPlanFactors(factorBlock, item.matched_factors);
    card.appendChild(factorBlock);

    const message = document.createElement('div');
    message.className = 'factor-plan-message';
    message.hidden = true;
    message.setAttribute('role', 'status');
    card.appendChild(message);

    const actions = document.createElement('div');
    actions.className = 'factor-plan-actions';
    const apply = document.createElement('button');
    apply.className = 'btn primary';
    apply.type = 'button';
    apply.textContent = '应用到仪表';
    apply.addEventListener('click', () => openFactorBoard({ conditions: item.conditions, autoPreview: true }));
    actions.appendChild(apply);
    const refresh = document.createElement('button');
    refresh.className = 'btn';
    refresh.type = 'button';
    refresh.textContent = '刷新匹配';
    refresh.addEventListener('click', async () => {
      refresh.disabled = true;
      setPlanCardMessage(card, '正在刷新匹配…');
      try {
        const updated = await API.refreshFactorLibrary(item.id);
        if (updated) {
          card.replaceWith(renderFactorPlanCard(updated));
        }
      } catch (error) {
        setPlanCardMessage(card, factorErrorMessage(error, '刷新失败，请稍后重试'), 'error');
      } finally {
        refresh.disabled = false;
      }
    });
    actions.appendChild(refresh);
    const edit = document.createElement('button');
    edit.className = 'btn';
    edit.type = 'button';
    edit.textContent = '编辑说明';
    const editForm = document.createElement('form');
    editForm.className = 'factor-plan-edit';
    editForm.hidden = true;
    const editName = document.createElement('input');
    editName.type = 'text'; editName.maxLength = 120; editName.value = item.name || '';
    editName.setAttribute('aria-label', '方案名称');
    const editDescription = document.createElement('input');
    editDescription.type = 'text'; editDescription.maxLength = 500; editDescription.value = item.description || '';
    editDescription.setAttribute('aria-label', '方案说明');
    const editSave = document.createElement('button');
    editSave.className = 'btn primary'; editSave.type = 'submit'; editSave.textContent = '保存';
    const editCancel = document.createElement('button');
    editCancel.className = 'btn'; editCancel.type = 'button'; editCancel.textContent = '取消';
    editForm.append(editName, editDescription, editSave, editCancel);
    edit.addEventListener('click', () => {
      editForm.hidden = false;
      edit.hidden = true;
      editName.focus();
    });
    editCancel.addEventListener('click', () => { editForm.hidden = true; edit.hidden = false; });
    editForm.addEventListener('submit', async event => {
      event.preventDefault();
      editSave.disabled = true;
      try {
        const updated = await API.updateFactorLibrary(item.id, {
          name: editName.value.trim(), description: editDescription.value.trim(),
        });
        if (updated) card.replaceWith(renderFactorPlanCard(updated));
      } catch (error) {
        setPlanCardMessage(card, factorErrorMessage(error, '编辑失败，请稍后重试'), 'error');
      } finally {
        editSave.disabled = false;
      }
    });
    actions.appendChild(edit);
    const deleteButton = document.createElement('button');
    deleteButton.className = 'btn danger';
    deleteButton.type = 'button';
    deleteButton.textContent = '删除';
    const cancelDelete = document.createElement('button');
    cancelDelete.className = 'btn';
    cancelDelete.type = 'button';
    cancelDelete.textContent = '取消';
    cancelDelete.hidden = true;
    deleteButton.addEventListener('click', async () => {
      if (deleteButton.dataset.confirm !== '1') {
        deleteButton.dataset.confirm = '1';
        deleteButton.textContent = '确认删除';
        cancelDelete.hidden = false;
        return;
      }
      deleteButton.disabled = true;
      cancelDelete.hidden = true;
      try {
        await API.deleteFactorLibrary(item.id);
        card.remove();
        updateFactorLibraryEmptyState();
      } catch (error) {
        deleteButton.disabled = false;
        setPlanCardMessage(card, factorErrorMessage(error, '删除失败，请稍后重试'), 'error');
      }
    });
    cancelDelete.addEventListener('click', () => {
      deleteButton.dataset.confirm = '';
      deleteButton.textContent = '删除';
      cancelDelete.hidden = true;
    });
    actions.append(deleteButton, cancelDelete);
    card.appendChild(actions);
    card.appendChild(editForm);
    return card;
  }

  function updateFactorLibraryEmptyState() {
    if (!els.factorLibraryEmpty || !els.factorLibraryList) return;
    els.factorLibraryEmpty.hidden = els.factorLibraryList.childElementCount !== 0;
  }

  function renderFactorLibrary(items) {
    clearElement(els.factorLibraryList);
    for (const item of Array.isArray(items) ? items : []) {
      if (item && typeof item === 'object') els.factorLibraryList.appendChild(renderFactorPlanCard(item));
    }
    updateFactorLibraryEmptyState();
  }

  async function loadFactorLibrary(silent = false) {
    const requestId = ++factorLibraryRequestId;
    if (factorLibraryController) factorLibraryController.abort();
    factorLibraryController = typeof AbortController !== 'undefined' ? new AbortController() : null;
    if (!silent && els.factorLibraryList) {
      clearElement(els.factorLibraryList);
      const loading = document.createElement('div');
      loading.className = 'muted';
      loading.textContent = '正在加载方案…';
      els.factorLibraryList.appendChild(loading);
    }
    try {
      const result = await API.getFactorLibrary(
        factorLibraryController ? { signal: factorLibraryController.signal } : {}
      );
      if (requestId !== factorLibraryRequestId) return null;
      state.factorLibraryItems = Array.isArray(result.items) ? result.items : [];
      els.factorError.hidden = true;
      renderFactorLibrary(state.factorLibraryItems);
      return state.factorLibraryItems;
    } catch (error) {
      if (requestId === factorLibraryRequestId && error.name !== 'AbortError') {
        if (!silent) {
          clearElement(els.factorLibraryList);
          els.factorError.textContent = factorErrorMessage(error, '因子库暂时不可用，请稍后重试');
          els.factorError.hidden = false;
        }
      }
      return null;
    } finally {
      if (requestId === factorLibraryRequestId) factorLibraryController = null;
    }
  }

  function openFactorBoard(options = {}) {
    hideAllOverlays();
    els.pageFactorBoard.hidden = false;
    setRailActive('factorboard');
    if (options.conditions) setFactorConditions(options.conditions);
    applyEmbeddedUpdate('factorboard');
    void loadFactorCapabilities();
    if (options.autoPreview) window.setTimeout(() => { void previewFactorSelection(); }, 0);
  }

  function applyEmbeddedUpdate(page, token = state.updateToken) {
    const frameId = EMBED_IFRAME_IDS[page];
    const frame = frameId ? $(frameId) : null;
    const route = window.QTradeUpdate && window.QTradeUpdate.cacheBustedRoute(page, token);
    if (!frame || !route || frame.getAttribute('src') === route) return;
    frame.setAttribute('src', route);
  }

  function updateStatusLabel(status) {
    const el = els.updateStatus;
    if (!el || !status) return;
    const labels = {
      running: '数据更新中…',
      success: '数据已更新',
      skip: '今日无需更新',
      failure: '数据更新失败',
    };
    const label = labels[status.state];
    if (!label) {
      el.hidden = true;
      return;
    }
    el.textContent = label;
    el.dataset.state = status.state;
    el.hidden = false;
  }

  function handleUpdateSuccess(status, token, page) {
    state.updateToken = token;
    if (EMBED_IFRAME_IDS[page] && state.activePage === page) {
      applyEmbeddedUpdate(page, token);
    }
    state.factorCapabilities = null;
    state.factorPreview = null;
    if (state.activePage === 'factorboard') {
      setFactorMessage('数据已更新，可重新预览', 'success');
      void loadFactorCapabilities(true);
    }
    void loadFactorLibrary(true);
    updateStatusLabel(status);
  }
  function hideAllOverlays() {
    ['trainingOverlay', 'autoPaperOverlay', 'pageFactors', 'pageRisk',
     'pagePortal', 'pagePitch', 'pageControl', 'pageFactorBoard'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.hidden = true;
    });
  }
  function showEmbedPage(id, page) {
    if (page === 'factorboard') {
      openFactorBoard();
      return;
    }
    hideAllOverlays();
    const el = document.getElementById(id);
    if (el) el.hidden = false;
    setRailActive(page);
    applyEmbeddedUpdate(page);
  }
  function showMarketPage() {
    hideAllOverlays();
    setRailActive('market');
  }
  async function openFactorPage() {
    hideAllOverlays();
    els.pageFactors.hidden = false;
    setRailActive('factors');
    els.factorSym.textContent = '已保存方案';
    els.factorError.hidden = true;
    setFactorMessage('');
    await loadFactorLibrary();
  }
  async function openRiskPage() {
    hideAllOverlays();
    els.pageRisk.hidden = false;
    setRailActive('risk');
    els.riskBody.innerHTML = '<div class="muted">加载中...</div>';
    els.riskError.hidden = true;
    try {
      const st = await API.getAutoPaper('status');
      const r = st.risk || {};
      const pool = st.forward_pool || [];
      const rows = pool.slice().reverse().map(p => `
        <div class="trade-row">
          <span class="mono" style="width:70px;">${p.symbol}</span>
          <span style="width:96px;">入 ${p.entry_date}</span>
          <span style="width:96px;">出 ${p.exit_date}</span>
          <span style="width:80px;">持 ${p.hold_days != null ? p.hold_days : '--'}天</span>
          <span style="width:90px;text-align:right;color:${p.pnl_pct >= 0 ? 'var(--up)' : 'var(--down)'}">${p.pnl_pct >= 0 ? '+' : ''}${p.pnl_pct}%</span>
        </div>`).join('');
      const famRows = Object.entries(r.family_exposure || {}).map(([k, v]) =>
        `<div class="trade-row"><span>${k}</span><span class="num">${v} 仓</span></div>`).join('');
      els.riskBody.innerHTML = `
        <section class="auto-card auto-card-wide">
          <div class="auto-card-title">🛡 风控门禁</div>
          <div class="trade-row"><span>最大持仓</span><span class="num">${r.max_positions}</span></div>
          <div class="trade-row"><span>单轮最大新开仓</span><span class="num">${r.max_new_per_cycle}</span></div>
          <div class="trade-row"><span>回撤暂停阈值</span><span class="num">${r.loss_pause_pct}%</span></div>
          <div class="trade-row"><span>当前收益</span><span class="num" style="color:${(r.current_pnl_pct || 0) >= 0 ? 'var(--up)' : 'var(--down)'}">${r.current_pnl_pct}%</span></div>
          <div class="trade-row"><span>L0 择时门控</span><span class="num">宽度 ${r.l0_breadth != null ? (r.l0_breadth * 100).toFixed(1) + '%' : '--'}（阈值 ${(r.l0_breadth_min * 100).toFixed(0)}%）${r.l0_gate ? ' ✅ 通过' : ' ⛔ 关闭'}</span></div>
        </section>
        <section class="auto-card auto-card-wide">
          <div class="auto-card-title">🧬 单因子暴露（上限 ${r.max_family_positions} 仓/族）</div>
          ${famRows || '<div class="muted">暂无持仓</div>'}
        </section>
        <section class="auto-card auto-card-wide">
          <div class="auto-card-title">📚 远期验证池（V1/5/20/60，共 ${pool.length} 笔）</div>
          ${rows || '<div class="muted">暂无已平仓记录</div>'}
        </section>`;
    } catch (e) {
      els.riskError.textContent = `加载失败: ${e.message}`;
      els.riskError.hidden = false;
    }
  }

  // ======================== 时钟 ========================
  function updateClock() {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    els.clock.textContent =
      `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ` +
      `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  }

  // ======================== 事件绑定 ========================
  function bindEvents() {
    // 搜索
    els.searchInput.addEventListener('input', renderStockList);

    // 工具栏按钮（训练营/自动模拟盘/回测已收敛到左侧导航，仅留刷新）
    $('btnRefresh').addEventListener('click', refreshAll);
    $('btnQuickBacktest').addEventListener('click', openBacktest);

    // 决策台侧边导航
    document.querySelectorAll('.deck-item').forEach(btn => {
      btn.addEventListener('click', () => {
        const page = btn.dataset.page;
        if (page === 'market') showMarketPage();
        else if (page === 'portal') showEmbedPage('pagePortal', 'portal');
        else if (page === 'pitch') showEmbedPage('pagePitch', 'pitch');
        else if (page === 'control') showEmbedPage('pageControl', 'control');
        else if (page === 'factorboard') showEmbedPage('pageFactorBoard', 'factorboard');
        else if (page === 'factors') openFactorPage();
        else if (page === 'risk') openRiskPage();
        else if (page === 'backtest') { hideAllOverlays(); openBacktest(); }
        else if (page === 'training') { hideAllOverlays(); if (window.Training) window.Training.open(); }
        else if (page === 'autopaper') { hideAllOverlays(); if (window.AutoPaper) window.AutoPaper.open(); }
      });
    });
    const fClose = $('btnFactorClose'); if (fClose) fClose.addEventListener('click', showMarketPage);
    const rClose = $('btnRiskClose'); if (rClose) rClose.addEventListener('click', showMarketPage);
    const pClose = $('btnPortalClose'); if (pClose) pClose.addEventListener('click', showMarketPage);
    const ptClose = $('btnPitchClose'); if (ptClose) ptClose.addEventListener('click', showMarketPage);
    const cClose = $('btnControlClose'); if (cClose) cClose.addEventListener('click', showMarketPage);
    const fbClose = $('btnFactorBoardClose'); if (fbClose) fbClose.addEventListener('click', showMarketPage);
    if (els.btnOpenFactorBoard) {
      els.btnOpenFactorBoard.addEventListener('click', () => openFactorBoard());
    }
    if (els.factorFilterToggle) {
      els.factorFilterToggle.addEventListener('click', () => {
        const expanded = els.factorFilterToggle.getAttribute('aria-expanded') !== 'true';
        els.factorFilterToggle.setAttribute('aria-expanded', String(expanded));
        els.factorFilterBody.hidden = !expanded;
      });
    }
    if (els.btnFactorFilterClear) {
      els.btnFactorFilterClear.addEventListener('click', () => {
        setFactorConditions({});
        state.factorPreview = null;
        els.factorPreview.hidden = true;
        setFactorMessage('已清空筛选条件');
      });
    }
    if (els.btnFactorPreview) {
      els.btnFactorPreview.addEventListener('click', () => { void previewFactorSelection(); });
    }
    if (els.btnFactorSave) {
      els.btnFactorSave.addEventListener('click', () => { void saveFactorPlan(); });
    }
    $('btnWatchlist').addEventListener('click', () => {
      if (state.activeSymbol) toggleWatchlist(state.activeSymbol);
    });

    // 指标切换（事件委托）
    els.indicatorToggles.addEventListener('click', (e) => {
      const tag = e.target.closest('.ind-tag');
      if (!tag) return;
      const name = tag.dataset.ind;
      const on = !tag.classList.contains('active');
      tag.classList.toggle('active', on);
      chartManager.toggle(name, on);
    });

    // 回测对话框
    $('btnCloseBacktest').addEventListener('click', closeBacktest);
    $('btnRunBacktest').addEventListener('click', runBacktest);
    const btStrategy = $('btStrategy');
    const updateCustomFactorsRow = () => {
      const row = $('customFactorsRow');
      if (row && btStrategy) row.hidden = btStrategy.value !== 'factor_score_custom';
    };
    btStrategy.addEventListener('change', updateCustomFactorsRow);
    updateCustomFactorsRow();
    els.backtestModal.addEventListener('click', (e) => {
      if (e.target === els.backtestModal) closeBacktest();
    });

    // 底部标签页（事件委托）
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });

    // 键盘快捷键
    document.addEventListener('keydown', (e) => {
      if (e.ctrlKey && e.key.toLowerCase() === 'f') {
        e.preventDefault();
        els.searchInput.focus();
      }
      if (e.key === 'Escape') closeBacktest();
    });

    // 窗口缩放时自适应图表
    window.addEventListener('resize', () => chartManager.resize());
  }

  async function refreshAll() {
    await loadSymbols();
    if (state.activeSymbol) await selectStock(state.activeSymbol);
    log('🔄 已刷新');
  }

  function startUpdateMonitor() {
    if (!window.QTradeUpdate) return;
    updateMonitor = window.QTradeUpdate.createMonitor({
      getStatus: () => API.getUpdateStatus(),
      getPage: () => state.activePage,
      onSuccess: handleUpdateSuccess,
      setIndicator: updateStatusLabel,
    });
    updateMonitor.start();
    window.addEventListener('pagehide', () => {
      updateMonitor.stop();
      if (factorPreviewController) factorPreviewController.abort();
      if (factorLibraryController) factorLibraryController.abort();
      if (factorCapabilitiesController) factorCapabilitiesController.abort();
    }, { once: true });
  }

  // ======================== 启动 ========================
  async function init() {
    chartManager = new ChartManager.ChartManager($('chartContainer'));
    bindEvents();
    renderWatchlist();
    updateClock();
    setInterval(updateClock, 1000);
    renderTab('trades');

    // 获取后端模式（实时/CSV），显示徽标
    try {
      const h = await fetch(API + '/api/health').then(r => r.json());
      if (h.mode === 'live') $('liveBadge').hidden = false;
    } catch (e) { /* 忽略 */ }

    await loadSymbols();

    // 加载最近浏览的股票，否则加载第一只
    const first = recentStocks[0] || state.allSymbols[0];
    if (first) await selectStock(first);
    else els.symName.textContent = '无数据';
    startUpdateMonitor();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
