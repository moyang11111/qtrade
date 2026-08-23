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
  };

  const recentStocks = JSON.parse(localStorage.getItem('qtrade_recent') || '[]');
  const watchlist = JSON.parse(localStorage.getItem('qtrade_watchlist') || '[]');
  const logs = [];

  let chartManager = null;

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
    pageFactors: $('pageFactors'), pageRisk: $('pageRisk'),
    factorSym: $('factorSym'), factorError: $('factorError'),
    factorTable: $('factorTable') ? $('factorTable').querySelector('tbody') : null,
    riskBody: $('riskBody'), riskError: $('riskError'),
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
  const FACTOR_LABELS = {
    composite_score: '合成打分', std20: '20日波动', downside_vol: '下行波动',
    reversal20: '20日反转', mom20: '动量20', o2c: '开收比', amihud: '非流动性',
    max_ret20: '20日最大涨', skew20: '20日偏度', amp20: '平均振幅',
    volume_ratio: '量能比', limup_ex_5: '近5涨停', pullback: '60日回撤',
    ma_alignment: '均线多头', rsi_revert: 'RSI超卖',
    macd_hist: 'MACD柱', roc20: 'ROC20', wpr14: 'W%R14', cci20: 'CCI20',
    obv_trend: 'OBV趋势', kdj_k: 'KDJ-K', ma200_up: '站上MA200',
    lowvol_60: '60日波动', mom_120: '120日动量', near_high_250: '接近52周高',
    new_high_250: '52周新高', consec_limit_up: '连续涨停', consec_limit_down: '连续跌停',
    limit_up_flag: '涨停标记', limit_down_flag: '跌停标记',
  };
  const FACTOR_DESC = {
    composite_score: '滚动z-score加权，正=偏多 负=偏空', std20: '收益波动，越低越稳',
    downside_vol: '仅下跌日波动', reversal20: '过去跌越多反弹预期越高',
    mom20: '20日动量', o2c: '开→收收益的10日均值', amihud: '非流动性（越高越差）',
    max_ret20: '近20日最大单日涨幅', skew20: '收益偏度', amp20: '振幅越低越稳',
    volume_ratio: '当日量/20日均量', limup_ex_5: '近5日涨停次数',
    pullback: '距60日高点回撤', ma_alignment: 'MA5>10>20>60 排列度', rsi_revert: 'RSI离50下方越远越超卖',
    macd_hist: 'DIF-DEA 动能', roc20: '20日变动率（短期反转）', wpr14: '威廉指标（高位超买偏空）',
    cci20: '顺势指标', obv_trend: 'OBV 相对21日均值趋势', kdj_k: 'KDJ K 值（趋势）',
    ma200_up: '站上200日均线', lowvol_60: '60日波动（低波正用）', mom_120: '120日动量（反转）',
    near_high_250: '收盘距52周高点（越接近0越强）', new_high_250: '创52周新高标记',
    consec_limit_up: '连续涨停天数', consec_limit_down: '连续跌停天数',
    limit_up_flag: '当日涨停', limit_down_flag: '当日跌停',
  };
  function setRailActive(page) {
    document.querySelectorAll('.deck-item').forEach(b =>
      b.classList.toggle('active', b.dataset.page === page));
  }
  function hideAllOverlays() {
    ['trainingOverlay', 'autoPaperOverlay', 'pageFactors', 'pageRisk',
     'pagePortal', 'pagePitch', 'pageControl', 'pageFactorBoard'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.hidden = true;
    });
  }
  function showEmbedPage(id, page) {
    hideAllOverlays();
    const el = document.getElementById(id);
    if (el) el.hidden = false;
    setRailActive(page);
  }
  function showMarketPage() {
    hideAllOverlays();
    setRailActive('market');
  }
  async function openFactorPage() {
    hideAllOverlays();
    els.pageFactors.hidden = false;
    setRailActive('factors');
    const sym = state.activeSymbol || (state.allSymbols.length ? state.allSymbols[0] : null);
    els.factorSym.textContent = sym ? `🧬 ${sym}` : '🧬 未选股';
    els.factorError.hidden = true;
    if (!els.factorTable) return;
    els.factorTable.innerHTML = '<tr><td colspan="3" class="auto-empty">加载中...</td></tr>';
    if (!sym) return;
    try {
      const f = await API.getFactors(sym);
      if (f.error) throw new Error(f.error);
      const rows = Object.entries(f)
        .filter(([k]) => !['symbol', 'error'].includes(k))
        .map(([k, v]) => {
          const val = (v === null || v === undefined) ? '--' : (typeof v === 'number' ? v.toFixed(4) : v);
          return `<tr><td class="mono">${FACTOR_LABELS[k] || k}</td><td class="num">${val}</td><td class="muted">${FACTOR_DESC[k] || ''}</td></tr>`;
        }).join('');
      els.factorTable.innerHTML = rows || '<tr><td colspan="3" class="auto-empty">暂无因子</td></tr>';
    } catch (e) {
      els.factorError.textContent = `因子加载失败: ${e.message}`;
      els.factorError.hidden = false;
      els.factorTable.innerHTML = '';
    }
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
  }

  document.addEventListener('DOMContentLoaded', init);
})();
