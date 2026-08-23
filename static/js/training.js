/**
 * QTrade Desktop — K线训练营模块（三模式）
 *
 * 1. 看图猜涨跌 (guess)：展示已知K线，判断未来 N 天涨/跌，揭晓计分。
 * 2. 买卖点训练 (trade)：在已知K线上点击标记买卖点，结算模拟收益 vs 买入持有。
 * 3. 逐根复盘 (replay)：热身段后K线逐根揭示，每根做买入/加仓/减仓/清仓/持有决策。
 *
 * 统一计分：得分 / 连对 / 最佳分（localStorage）。
 * 数据脱敏：假时间轴、无股票代码/名称（后端完成）。
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const els = {
    overlay: $('trainingOverlay'),
    tabs: $('trainingTabs'),
    horizonWrap: $('trHorizonWrap'),
    horizon: $('trHorizon'),
    score: $('trScore'),
    streak: $('trStreak'),
    best: $('trBest'),
    hint: $('trHint'),
    chart: $('trainingChart'),
    result: $('trResult'),
    close: $('trClose'),
    // guess
    actionsGuess: $('trActionsGuess'),
    up: $('trUp'), down: $('trDown'),
    // trade
    actionsTrade: $('trActionsTrade'),
    buy: $('trBuy'), sell: $('trSell'), undo: $('trUndo'), finish: $('trFinish'),
    tradeStatus: $('trTradeStatus'),
    // replay
    actionsReplay: $('trActionsReplay'),
    rpBuy: $('rpBuy'), rpAdd: $('rpAdd'), rpCut: $('rpCut'),
    rpClear: $('rpClear'), rpHold: $('rpHold'), rpNext: $('rpNext'),
    rpStatus: $('rpStatus'),
  };

  const BEST_KEY = 'qtrade_training_best';
  const COMM = 0.0003;          // 手续费率
  const INIT_CASH = 100000;     // 模拟初始资金

  // 全局状态
  let chart = null;
  let candleSeries = null;
  let volumeSeries = null;
  let futureCandleSeries = null;
  let maSeries = null;      // {ma5, ma10, ma20, ma60}
  let bollSeries = null;    // {up, mid, low}
  let mode = 'guess';
  let question = null;
  let answered = false;
  let score = 0;
  let streak = 0;
  let best = parseInt(localStorage.getItem(BEST_KEY) || '0', 10);

  // 买卖点状态
  let marks = [];               // [{type:'buy'|'sell', time, price, idx}]
  let nextMarkType = 'buy';

  // 逐根复盘状态
  let rp = null;                // {cash, shares, cost, idx(已揭示到 future 的哪根), revealed}

  // ---------- 生命周期 ----------

  function open() {
    els.overlay.hidden = false;
    renderStats();
    switchMode('guess');
  }

  function close() {
    els.overlay.hidden = true;
    destroyChart();
    question = null;
    answered = false;
  }

  function destroyChart() {
    if (chart) { chart.remove(); chart = null; }
    candleSeries = null; volumeSeries = null; futureCandleSeries = null;
  }

  // ---------- 模式切换 ----------

  function switchMode(m) {
    mode = m;
    // tabs 高亮
    els.tabs.querySelectorAll('.tr-tab').forEach(t =>
      t.classList.toggle('active', t.dataset.mode === m));
    // 预测天数选择：仅 guess 模式显示
    els.horizonWrap.style.display = m === 'guess' ? '' : 'none';
    // 按钮区
    els.actionsGuess.hidden = m !== 'guess';
    els.actionsTrade.hidden = m !== 'trade';
    els.actionsReplay.hidden = m !== 'replay';
    els.result.hidden = true;
    // 重置跨模式残留的按钮状态
    [els.actionsGuess, els.actionsTrade, els.actionsReplay].forEach(c => hideNextButton(c));
    els.finish.hidden = false;
    els.finish.disabled = true;
    els.rpNext.textContent = '➡ 下一根';

    if (m === 'guess') {
      marks = []; nextMarkType = 'buy'; rp = null; answered = false;
      loadQuestion();
    } else if (m === 'trade') {
      marks = []; nextMarkType = 'buy'; answered = false;
      loadTrade();
    } else if (m === 'replay') {
      rp = null; answered = false;
      loadReplay();
    }
  }

  // ---------- 抽题 ----------

  async function fetchQuestion(lookback, horizon) {
    return API.trainingNext(lookback, horizon);
  }

  // ---------- 模式一：猜涨跌 ----------

  async function loadQuestion() {
    answered = false;
    els.result.hidden = true;
    els.up.hidden = false; els.down.hidden = false;
    hideNextButton(els.actionsGuess);
    els.hint.textContent = '加载题目中...';
    const horizon = els.horizon.value;
    try {
      question = await fetchQuestion(60, horizon);
    } catch (e) {
      els.hint.textContent = `⚠ 题目加载失败: ${e.message}`;
      return;
    }
    ensureChart();
    drawKnown(question.known);
    els.hint.textContent =
      `已显示最近 60 根K线，请判断未来 ${question.horizon} 天是涨还是跌`;
  }

  function answer(pick) {
    if (!question || answered) return;
    answered = true;
    const correct = pick === question.answer;
    revealFuture();
    const chg = question.change_pct;
    const chgText = (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%';

    if (correct) {
      streak += 1;
      const gain = 10 + (streak - 1) * 2;
      addScore(gain);
      showResult('ok',
        `✅ <b>答对了！</b>未来 ${question.horizon} 天 <b>${question.answer === 'up' ? '上涨' : '下跌'}</b> ` +
        `(${chgText})　本题 <b>+${gain}</b> 分`);
    } else {
      streak = 0;
      renderStats();
      showResult('no',
        `❌ <b>答错了</b>　未来 ${question.horizon} 天实际是 ` +
        `<b>${question.answer === 'up' ? '上涨' : '下跌'}</b> (${chgText})　连对已清零`);
    }
    els.hint.textContent = '揭晓完毕，点击下方按钮进入下一题';
    els.up.hidden = true; els.down.hidden = true;
    showNextButton(els.actionsGuess, loadQuestion);
  }

  // ---------- 模式二：买卖点训练 ----------

  async function loadTrade() {
    answered = false;            // 结算后点「下一题」必须重置，否则 onChartClick 被吞
    hideNextButton(els.actionsTrade);
    els.result.hidden = true;
    els.tradeStatus.textContent = '加载题目中...';
    marks = []; nextMarkType = 'buy';
    els.finish.disabled = true;
    try {
      question = await fetchQuestion(80, 40);
    } catch (e) {
      els.tradeStatus.textContent = `⚠ 题目加载失败: ${e.message}`;
      return;
    }
    ensureChart();
    drawKnown(question.known);
    renderMarks();
    els.tradeStatus.textContent =
      '点击图上的 K 线标记买卖点（先买后卖，最多 5 买 5 卖），完成后点「完成结算」';
    els.finish.disabled = marks.length === 0;
  }

  // 图表点击 -> 添加买卖标记（仅限 known 区间）
  function onChartClick(param) {
    if (mode !== 'trade' || !question || answered) return;
    if (!param.time) return;
    const known = question.known;
    if (param.time > known[known.length - 1].time) return;  // 不能点未来
    if (marks.length >= 10) {
      els.tradeStatus.textContent = '已达标记上限（10 个点）';
      return;
    }
    // 找到对应K线
    const bar = known.find(b => b.time === param.time) ||
                known.reduce((a, b) => Math.abs(b.time - param.time) < Math.abs(a.time - param.time) ? b : a);
    marks.push({ type: nextMarkType, time: bar.time, price: bar.close, idx: known.indexOf(bar) });
    nextMarkType = nextMarkType === 'buy' ? 'sell' : 'buy';
    renderMarks();
    els.tradeStatus.textContent =
      `已标记 ${marks.length} 个点（${marks.filter(m => m.type === 'buy').length} 买 / ${marks.filter(m => m.type === 'sell').length} 卖）`;
    els.finish.disabled = false;
  }

  function renderMarks() {
    if (!candleSeries) return;
    candleSeries.setMarkers(marks.map((m, i) => ({
      time: m.time,
      position: m.type === 'buy' ? 'belowBar' : 'aboveBar',
      color: m.type === 'buy' ? '#EB5757' : '#27AE60',
      shape: m.type === 'buy' ? 'arrowUp' : 'arrowDown',
      text: `${i + 1}${m.type === 'buy' ? '买' : '卖'}@${m.price}`,
    })));
  }

  function undoMark() {
    if (mode !== 'trade' || marks.length === 0) return;
    marks.pop();
    nextMarkType = nextMarkType === 'buy' ? 'sell' : 'buy';
    renderMarks();
    els.tradeStatus.textContent = `已撤销，当前可标「${nextMarkType === 'buy' ? '买入' : '卖出'}」`;
    els.finish.disabled = marks.length === 0;
  }

  function finishTrade() {
    if (mode !== 'trade' || !question || answered || marks.length === 0) return;
    answered = true;
    revealFuture();
    const lastFutureClose = question.future[question.future.length - 1].close;

    // 模拟：全仓单标的，先买后卖
    let cash = INIT_CASH;
    let shares = 0;
    const tlog = [];
    for (const m of marks) {
      const px = m.price;
      if (m.type === 'buy' && cash > 0) {
        shares = Math.floor(cash / (px * (1 + COMM)));
        cash -= shares * px * (1 + COMM);
        tlog.push(`买入 @${px} × ${shares} 股`);
      } else if (m.type === 'sell' && shares > 0) {
        cash += shares * px * (1 - COMM);
        tlog.push(`卖出 @${px} × ${shares} 股`);
        shares = 0;
      }
    }
    // 未平仓按未来最后收盘价结算
    if (shares > 0) {
      cash += shares * lastFutureClose * (1 - COMM);
      tlog.push(`期末结算 @${lastFutureClose} × ${shares} 股`);
    }
    const userRet = (cash / INIT_CASH - 1) * 100;
    const bhRet = (lastFutureClose / question.known[0].close - 1) * 100;
    const beat = userRet - bhRet;

    let cls, msg;
    if (beat > 0) {
      streak += 1;
      const gain = 10 + (streak - 1) * 2;
      addScore(gain);
      cls = 'ok';
      msg = `🏆 <b>跑赢买入持有！</b>你的收益 <b>${userRet >= 0 ? '+' : ''}${userRet.toFixed(2)}%</b>，` +
        `买入持有 ${bhRet >= 0 ? '+' : ''}${bhRet.toFixed(2)}%，超额 <b>+${beat.toFixed(2)}%</b>　本题 +${gain} 分`;
    } else {
      streak = 0;
      renderStats();
      cls = 'no';
      msg = `📉 <b>未跑赢基准</b>　你的收益 <b>${userRet >= 0 ? '+' : ''}${userRet.toFixed(2)}%</b>，` +
        `买入持有 ${bhRet >= 0 ? '+' : ''}${bhRet.toFixed(2)}%，落后 ${Math.abs(beat).toFixed(2)}%　连对已清零`;
    }
    els.tradeStatus.textContent = tlog.join(' | ') || '未产生交易';
    showResult(cls, msg);
    els.finish.hidden = true;
    showNextButton(els.actionsTrade, loadTrade);
  }

  // ---------- 模式三：逐根复盘 ----------

  async function loadReplay() {
    answered = false;            // 关键：结算后点「下一题」必须重置，否则 revealNextBar 被吞
    hideNextButton(els.actionsReplay);
    els.result.hidden = true;
    els.rpStatus.textContent = '加载题目中...';
    rp = null;
    try {
      question = await fetchQuestion(40, 150);   // 热身 40 + 复盘 150 个交易日
    } catch (e) {
      els.rpStatus.textContent = `⚠ 题目加载失败: ${e.message}`;
      return;
    }
    rp = { cash: INIT_CASH, shares: 0, cost: 0, revealed: 0 };
    ensureChart();
    // 热身段 40 根直接显示（固定K线间距，右侧预留复盘区）
    const warm = question.known;
    candleSeries.setData(warm.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
    volumeSeries.setData(warm.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? 'rgba(39,174,96,0.35)' : 'rgba(235,87,87,0.35)',
    })));
    futureCandleSeries.setData([]);
    updateIndicators(warm);
    chart.timeScale().applyOptions({ barSpacing: 8, rightOffset: 60 });  // 固定比例，右侧留 60 根空位
    els.hint.textContent =
      `热身段 ${question.lookback} 根已显示。点击「下一根」逐根揭示未来 ${question.horizon} 根，每根做出操作决策。`;
    renderReplayStatus();
  }

  function revealNextBar() {
    if (mode !== 'replay' || !question || !rp || answered) return;
    if (rp.revealed >= question.future.length) {
      finishReplay();
      return;
    }
    rp.revealed += 1;
    // 全量重绘（不用 update 增量，避免与指标 setData 混用时的状态问题）
    const shown = question.known.concat(question.future.slice(0, rp.revealed));
    const bar = question.future[rp.revealed - 1];
    candleSeries.setData(shown.map(b => ({
      time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
    })));
    volumeSeries.setData(shown.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? 'rgba(39,174,96,0.35)' : 'rgba(235,87,87,0.35)',
    })));
    updateIndicators(shown);
    // 保持固定K线间距：新根从右侧空位出现，不缩放；全部揭示后才 fitContent
    if (rp.revealed >= question.future.length) {
      chart.timeScale().fitContent();
    }
    els.hint.textContent =
      `第 ${rp.revealed}/${question.horizon} 根 已揭示（收盘 ${bar.close}）。请决策，或继续「下一根」。`;
    renderReplayStatus();
    if (rp.revealed >= question.future.length) {
      els.rpNext.textContent = '🏁 结算';
    }
  }

  function replayAction(action) {
    if (mode !== 'replay' || !question || !rp || answered) return;
    if (rp.revealed === 0) {
      els.rpStatus.textContent = '请先点「下一根」揭示K线再操作';
      return;
    }
    const px = question.future[rp.revealed - 1].close;
    switch (action) {
      case 'buy':   // 全仓买入
        if (rp.shares === 0 && rp.cash > 0) {
          rp.shares = Math.floor(rp.cash / (px * (1 + COMM)));
          rp.cash -= rp.shares * px * (1 + COMM);
        }
        break;
      case 'add':   // 用剩余现金一半加仓
        if (rp.cash > 0) {
          const spend = rp.cash * 0.5;
          const addShares = Math.floor(spend / (px * (1 + COMM)));
          if (addShares > 0) { rp.shares += addShares; rp.cash -= addShares * px * (1 + COMM); }
        }
        break;
      case 'cut':   // 卖出一半持仓
        if (rp.shares > 0) {
          const sellShares = Math.floor(rp.shares / 2);
          rp.cash += sellShares * px * (1 - COMM);
          rp.shares -= sellShares;
        }
        break;
      case 'clear': // 清仓
        if (rp.shares > 0) {
          rp.cash += rp.shares * px * (1 - COMM);
          rp.shares = 0;
        }
        break;
      case 'hold':
        break;
    }
    renderReplayStatus();
  }

  function renderReplayStatus() {
    if (!rp) return;
    const marketVal = rp.shares * (rp.revealed > 0 ? question.future[rp.revealed - 1].close : 0);
    const total = rp.cash + marketVal;
    const pnl = (total / INIT_CASH - 1) * 100;
    const posStr = rp.shares > 0 ? `${rp.shares} 股` : '空仓';
    els.rpStatus.textContent =
      `持仓 ${posStr} | 现金 ¥${rp.cash.toFixed(0)} | 总资产 ¥${total.toFixed(0)} | 浮盈 ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%`;
  }

  function finishReplay() {
    if (mode !== 'replay' || !question || !rp || answered) return;
    if (rp.revealed < question.future.length) {
      els.rpStatus.textContent = `还有 ${question.future.length - rp.revealed} 根未揭示，无法结算`;
      return;
    }
    answered = true;
    // 未清仓按最后收盘价结算
    const lastClose = question.future[question.future.length - 1].close;
    if (rp.shares > 0) {
      rp.cash += rp.shares * lastClose * (1 - COMM);
      rp.shares = 0;
    }
    const userRet = (rp.cash / INIT_CASH - 1) * 100;
    const bhRet = (lastClose / question.known[0].close - 1) * 100;
    const beat = userRet - bhRet;

    let cls, msg;
    if (beat > 0) {
      streak += 1;
      const gain = 15 + (streak - 1) * 2;
      addScore(gain);
      cls = 'ok';
      msg = `🏆 <b>跑赢买入持有！</b>你的收益 <b>${userRet >= 0 ? '+' : ''}${userRet.toFixed(2)}%</b> vs ` +
        `买入持有 ${bhRet >= 0 ? '+' : ''}${bhRet.toFixed(2)}%，超额 <b>+${beat.toFixed(2)}%</b>　本题 +${gain} 分`;
    } else {
      streak = 0;
      renderStats();
      cls = 'no';
      msg = `📉 <b>未跑赢基准</b>　你的收益 <b>${userRet >= 0 ? '+' : ''}${userRet.toFixed(2)}%</b> vs ` +
        `买入持有 ${bhRet >= 0 ? '+' : ''}${bhRet.toFixed(2)}%　连对已清零`;
    }
    showResult(cls, msg);
    els.hint.textContent = '复盘结束';
    els.rpNext.textContent = '➡ 下一根';
    showNextButton(els.actionsReplay, loadReplay);
  }

  // ---------- 图表通用 ----------

  function ensureChart() {
    if (chart) return;
    chart = LightweightCharts.createChart(els.chart, {
      layout: {
        background: { type: 'solid', color: '#131316' },
        textColor: '#A1A1AA',
      },
      grid: { vertLines: { color: '#1c1c22' }, horzLines: { color: '#1c1c22' } },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#2a2a30' },
      timeScale: {
        borderColor: '#2a2a30', timeVisible: true, secondsVisible: false,
        barSpacing: 8,          // 固定K线间距，避免比例忽宽忽窄
        minBarSpacing: 3,
      },
      handleScroll: { vertTouchDrag: false },
      autoSize: true,           // 跟随容器自适应，窗口变化时比例不歪
    });
    candleSeries = chart.addCandlestickSeries({
      upColor: '#EB5757', downColor: '#27AE60',
      borderUpColor: '#EB5757', borderDownColor: '#27AE60',
      wickUpColor: '#EB5757', wickDownColor: '#27AE60',
    });
    futureCandleSeries = chart.addCandlestickSeries({
      upColor: '#F2C94C', downColor: '#2D9CDB',
      borderUpColor: '#F2C94C', borderDownColor: '#2D9CDB',
      wickUpColor: '#F2C94C', wickDownColor: '#2D9CDB',
    });

    // MA 均线（暗色风格，与主图一致）
    const maColors = { ma5: '#F2C94C', ma10: '#5E6AD2', ma20: '#8B5CF6', ma60: '#A1A1AA' };
    maSeries = {};
    for (const [k, color] of Object.entries(maColors)) {
      maSeries[k] = chart.addLineSeries({
        color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      });
    }

    // BOLL 布林带
    bollSeries = {
      up: chart.addLineSeries({ color: 'rgba(235,87,87,0.6)', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }),
      mid: chart.addLineSeries({ color: 'rgba(242,201,76,0.6)', lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
      low: chart.addLineSeries({ color: 'rgba(39,174,96,0.6)', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }),
    };

    volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' }, priceScaleId: 'vol',
      lastValueVisible: false, priceLineVisible: false,
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    chart.timeScale().fitContent();
    chart.subscribeClick(onChartClick);
  }

  // 计算并绘制 MA5/10/20/60 与 BOLL(20,2)
  function updateIndicators(bars) {
    if (!maSeries || !bollSeries) return;
    const n = bars.length;
    const closes = bars.map(b => b.close);
    const sma = (win) => {
      const out = [];
      for (let i = win - 1; i < n; i++) {
        let s = 0;
        for (let j = i - win + 1; j <= i; j++) s += closes[j];
        out.push({ time: bars[i].time, value: +(s / win).toFixed(2) });
      }
      return out;
    };
    maSeries.ma5.setData(sma(5));
    maSeries.ma10.setData(sma(10));
    maSeries.ma20.setData(sma(20));
    maSeries.ma60.setData(sma(60));
    // BOLL: 20 日均线 ± 2σ
    const midA = [], upA = [], lowA = [];
    for (let i = 19; i < n; i++) {
      let s = 0;
      for (let j = i - 19; j <= i; j++) s += closes[j];
      const mid = s / 20;
      let v = 0;
      for (let j = i - 19; j <= i; j++) v += (closes[j] - mid) ** 2;
      const sd = Math.sqrt(v / 20);
      const t = bars[i].time;
      midA.push({ time: t, value: +mid.toFixed(2) });
      upA.push({ time: t, value: +(mid + 2 * sd).toFixed(2) });
      lowA.push({ time: t, value: +(mid - 2 * sd).toFixed(2) });
    }
    bollSeries.mid.setData(midA);
    bollSeries.up.setData(upA);
    bollSeries.low.setData(lowA);
  }

  function drawKnown(bars) {
    candleSeries.setData(bars.map(b => ({
      time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
    })));
    volumeSeries.setData(bars.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? 'rgba(39,174,96,0.35)' : 'rgba(235,87,87,0.35)',
    })));
    futureCandleSeries.setData([]);
    updateIndicators(bars);
    chart.timeScale().fitContent();
  }

  function revealFuture() {
    if (!question) return;
    const all = question.known.concat(question.future);
    candleSeries.setData(all.map(b => ({
      time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
    })));
    futureCandleSeries.setData(question.future.map(b => ({
      time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
    })));
    volumeSeries.setData(all.map(b => ({
      time: b.time, value: b.volume,
      color: b.close >= b.open ? 'rgba(39,174,96,0.35)' : 'rgba(235,87,87,0.35)',
    })));
    updateIndicators(all);
    chart.timeScale().fitContent();
  }

  // ---------- 计分 ----------

  function addScore(gain) {
    score += gain;
    if (score > best) {
      best = score;
      localStorage.setItem(BEST_KEY, String(best));
    }
    renderStats();
  }

  function renderStats() {
    els.score.textContent = score;
    els.streak.textContent = streak;
    els.best.textContent = best;
  }

  function showResult(cls, html) {
    els.result.className = 'training-result ' + cls;
    els.result.innerHTML = html;
    els.result.hidden = false;
  }

  function showNextButton(container, fn) {
    let btn = container.querySelector('.tr-next');
    if (!btn) {
      btn = document.createElement('button');
      btn.className = 'btn primary tr-big tr-next';
      btn.textContent = '➡ 下一题';
      container.appendChild(btn);
    }
    btn.hidden = false;
    btn.onclick = fn;
  }

  function hideNextButton(container) {
    const btn = container.querySelector('.tr-next');
    if (btn) btn.hidden = true;
  }

  // ---------- 事件 ----------

  function init() {
    els.close.onclick = close;
    els.horizon.onchange = () => { if (mode === 'guess') loadQuestion(); };
    // tabs
    els.tabs.querySelectorAll('.tr-tab').forEach(t =>
      t.addEventListener('click', () => switchMode(t.dataset.mode)));
    // guess
    els.up.onclick = () => answer('up');
    els.down.onclick = () => answer('down');
    // trade
    els.buy.onclick = () => setNextMark('buy');
    els.sell.onclick = () => setNextMark('sell');
    els.undo.onclick = undoMark;
    els.finish.onclick = finishTrade;
    // replay
    els.rpBuy.onclick = () => replayAction('buy');
    els.rpAdd.onclick = () => replayAction('add');
    els.rpCut.onclick = () => replayAction('cut');
    els.rpClear.onclick = () => replayAction('clear');
    els.rpHold.onclick = () => replayAction('hold');
    els.rpNext.onclick = revealNextBar;
  }

  function setNextMark(t) {
    if (mode !== 'trade' || answered) return;
    nextMarkType = t;
    els.tradeStatus.textContent = `下一个标记：${t === 'buy' ? '🟢 买入' : '🔴 卖出'}（点击图上K线）`;
  }

  // 暴露给 app.js
  window.Training = { open, close };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
