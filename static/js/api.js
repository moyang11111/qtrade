/**
 * QTrade Desktop — API 封装层
 * 统一处理与 Python 后端的 HTTP 通信。
 */
const API = (() => {
  const BASE = '';

  /** 通用 GET 请求，返回 JSON */
  async function get(path, params = {}) {
    const qs = new URLSearchParams(params).toString();
    const url = BASE + path + (qs ? '?' + qs : '');
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`);
    return res.json();
  }

  return {
    /** 获取全部股票代码 */
    listSymbols() {
      return get('/api/symbols');
    },

    /** 获取单只股票 K 线数据 */
    getKline(symbol, limit = 400) {
      return get(`/api/kline/${encodeURIComponent(symbol)}`, { limit });
    },

    /** 获取股票概要信息 */
    getInfo(symbol) {
      return get(`/api/info/${encodeURIComponent(symbol)}`);
    },

    /** 获取技术指标（MA/MACD/RSI/BOLL） */
    getIndicators(symbol) {
      return get(`/api/indicators/${encodeURIComponent(symbol)}`);
    },

    /** 获取最新 A 股量价因子（首批移植 deepseek-harness-quant） */
    getFactors(symbol) {
      return get(`/api/factors/${encodeURIComponent(symbol)}`);
    },

    /** 运行回测 */
    runBacktest({ symbol, strategy, capital, commission, stopLoss, takeProfit }) {
      return get('/api/backtest', {
        symbol,
        strategy,
        capital,
        commission,
        stop_loss: stopLoss,
        take_profit: takeProfit,
      });
    },

    /** AI 信号模拟盘：action = status | sync | mark */
    getAiPaper(action = 'status') {
      return get('/api/ai/paper', { action });
    },

    /** 自动模拟盘：action = status | run | toggle | reset | setmode */
    getAutoPaper(action = 'status', mode = null) {
      const params = { action };
      if (mode) params.mode = mode;
      return get('/api/auto/paper', params);
    },

    /** K线训练营：抽一道看图猜涨跌题（已脱敏） */
    trainingNext(lookback = 60, horizon = 5) {
      return get('/api/training/next', { lookback, horizon });
    },
  };
})();
