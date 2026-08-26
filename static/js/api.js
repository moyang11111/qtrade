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

    /** 运行回测（自定义因子回测可传 factors/weights） */
    runBacktest({ symbol, strategy, capital, commission, stopLoss, takeProfit, factors, weights }) {
      const params = {
        symbol,
        strategy,
        capital,
        commission,
        stop_loss: stopLoss,
        take_profit: takeProfit,
      };
      if (factors) params.factors = factors;
      if (weights) params.weights = weights;
      return get('/api/backtest', params);
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

    /** 应用内每日更新状态（只读，不触发更新） */
    getUpdateStatus() {
      return get('/api/update/status');
    },
  };
})();

/**
 * 应用内更新刷新协调器。
 * 只允许固定的本地嵌入页面，状态令牌由交易日和完成时间组成。
 * 该对象保持无 DOM 依赖，便于 Node/浏览器确定性测试。
 */
const QTradeUpdate = (() => {
  const POLL_INTERVAL_MS = 30000;
  const REFRESH_ROUTES = Object.freeze({
    portal: '/portal',
    pitch: '/pitch',
    factorboard: '/factors',
  });

  function successToken(status) {
    if (!status || status.state !== 'success') return null;
    if (typeof status.trade_date !== 'string' || !status.trade_date) return null;
    if (typeof status.finished_at !== 'string' || !status.finished_at) return null;
    return `${status.trade_date}|${status.finished_at}`;
  }

  function cacheBustedRoute(page, token) {
    const route = REFRESH_ROUTES[page];
    if (!route) return null;
    if (!token) return route;
    return `${route}?qtrade_update=${encodeURIComponent(String(token))}`;
  }

  function updateTargets(status) {
    return successToken(status) ? Object.keys(REFRESH_ROUTES) : [];
  }

  function createMonitor(options = {}) {
    const getStatus = options.getStatus || (() => API.getUpdateStatus());
    const getPage = options.getPage || (() => null);
    const onSuccess = options.onSuccess || (() => {});
    const setIndicator = options.setIndicator || (() => {});
    const setIntervalFn = options.setIntervalFn || ((fn, ms) => setInterval(fn, ms));
    const clearIntervalFn = options.clearIntervalFn || ((id) => clearInterval(id));
    let timer = null;
    let inFlight = false;
    let stopped = false;
    let lastSuccessToken = null;

    async function poll() {
      if (stopped || inFlight) return null;
      inFlight = true;
      try {
        const status = await getStatus();
        const token = successToken(status);
        if (token && token !== lastSuccessToken) {
          lastSuccessToken = token;
          onSuccess(status, token, getPage());
        }
        setIndicator(status);
        return status;
      } catch (e) {
        // 网络暂时不可用时静默等待下一轮，不污染用户控制台。
        return null;
      } finally {
        inFlight = false;
      }
    }

    function start() {
      if (timer !== null) return;
      stopped = false;
      void poll();
      timer = setIntervalFn(() => { void poll(); }, POLL_INTERVAL_MS);
    }

    function stop() {
      stopped = true;
      if (timer !== null) {
        clearIntervalFn(timer);
        timer = null;
      }
    }

    return {
      poll,
      start,
      stop,
      isInFlight: () => inFlight,
      getLastSuccessToken: () => lastSuccessToken,
    };
  }

  return {
    POLL_INTERVAL_MS,
    REFRESH_ROUTES,
    successToken,
    cacheBustedRoute,
    updateTargets,
    createMonitor,
  };
})();

if (typeof window !== 'undefined') window.QTradeUpdate = QTradeUpdate;
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { API, QTradeUpdate };
}
