/**
 * QTrade Desktop — 图表模块
 *
 * 基于 TradingView lightweight-charts 4.x（支持 panes）。
 *
 * 布局：
 *   Pane 0 ── K 线 + MA5/10/20/60 + BOLL（叠加） + 成交量（底部 overlay）
 *   Pane 1 ── MACD（独立子图）
 *   Pane 2 ── RSI（独立子图，含 30/70 参考线）
 */
const ChartManager = (() => {
  const PANE_MAIN = 0;
  const PANE_MACD = 1;
  const PANE_RSI = 2;

  // 指标组 → 是否默认显示
  const DEFAULTS = { ma: true, boll: false, macd: false, rsi: false };

  class ChartManager {
    constructor(container) {
      this.container = container;
      this.chart = null;
      this.series = {};   // 存储所有 series 引用
      this.visible = { ...DEFAULTS };

      this._init();
    }

    // ---- 初始化 ----
    _init() {
      const c = LightweightCharts.createChart(this.container, {
        layout: {
          background: { type: 'solid', color: '#131316' },
          textColor: '#A1A1AA',
        },
        grid: {
          vertLines: { color: '#1c1c22' },
          horzLines: { color: '#1c1c22' },
        },
        crosshair: { mode: 1 },
        rightPriceScale: { borderColor: '#2a2a30' },
        timeScale: { borderColor: '#2a2a30', timeVisible: true, secondsVisible: false },
        handleScroll: { vertTouchDrag: false },
      });
      this.chart = c;

      // ---- Pane 0: K 线（A股习惯：红涨/绿跌） ----
      this.series.candle = c.addCandlestickSeries({
        upColor: '#EB5757',
        downColor: '#27AE60',
        borderUpColor: '#EB5757',
        borderDownColor: '#27AE60',
        wickUpColor: '#EB5757',
        wickDownColor: '#27AE60',
      }, PANE_MAIN);

      // 成交量（overlay 在 Pane 0 底部）
      this.series.volume = c.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'vol',
        lastValueVisible: false,
        priceLineVisible: false,
      }, PANE_MAIN);
      this.series.volume.priceScale().applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });

      // 均线（暗色数据密室风格）
      const maColors = { ma5: '#F2C94C', ma10: '#5E6AD2', ma20: '#8B5CF6', ma60: '#A1A1AA' };
      this.series.ma = {};
      for (const [key, color] of Object.entries(maColors)) {
        this.series.ma[key] = c.addLineSeries({
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        }, PANE_MAIN);
      }

      // 布林带
      this.series.boll = {
        upper: c.addLineSeries({ color: '#EB5757', lineWidth: 1.5, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }, PANE_MAIN),
        middle: c.addLineSeries({ color: '#F2C94C', lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, PANE_MAIN),
        lower: c.addLineSeries({ color: '#27AE60', lineWidth: 1.5, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }, PANE_MAIN),
      };

      // ---- Pane 1: MACD ----
      this.series.macd = {
        line: c.addLineSeries({ color: '#5E6AD2', lineWidth: 1, priceLineVisible: false }, PANE_MACD),
        signal: c.addLineSeries({ color: '#8B5CF6', lineWidth: 1, priceLineVisible: false }, PANE_MACD),
        histogram: c.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false }, PANE_MACD),
      };

      // ---- Pane 2: RSI ----
      this.series.rsi = c.addLineSeries({ color: '#F2C94C', lineWidth: 1, priceLineVisible: false }, PANE_RSI);
      // RSI 30/70 参考线
      const rsi70 = this.series.rsi.createPriceLine({
        price: 70, color: 'rgba(235,87,87,0.5)',
        lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '',
      });
      const rsi30 = this.series.rsi.createPriceLine({
        price: 30, color: 'rgba(39,174,96,0.5)',
        lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '',
      });
      this.rsiRefLines = [rsi70, rsi30];

      this._applyVisibility();
    }

    // ---- 数据 ----
    /** 设置 K 线 + 成交量数据 */
    setKline(data) {
      if (!data || data.length === 0) return;

      this.series.candle.setData(data.map(d => ({
        time: d.time, open: d.open, high: d.high, low: d.low, close: d.close,
      })));

      this.series.volume.setData(data.map(d => ({
        time: d.time,
        value: d.volume,
        color: d.close >= d.open ? 'rgba(235,87,87,0.28)' : 'rgba(39,174,96,0.28)',
      })));

      this.chart.timeScale().fitContent();
    }

    /** 设置指标数据（MA/MACD/RSI/BOLL） */
    setIndicators(ind) {
      if (!ind) return;

      // MA（后端已带 time，直接对齐）
      if (ind.mas) {
        for (const [key, seriesData] of Object.entries(ind.mas)) {
          const line = this.series.ma[key];
          if (!line) continue;
          const data = seriesData
            .filter(d => d.value != null)
            .map(d => ({ time: d.time, value: d.value }));
          if (data.length > 0) line.setData(data);
        }
      }

      // BOLL
      if (ind.boll && ind.boll.length > 0) {
        this.series.boll.upper.setData(ind.boll
          .filter(d => d.upper != null)
          .map(d => ({ time: d.time, value: d.upper })));
        this.series.boll.middle.setData(ind.boll
          .filter(d => d.middle != null)
          .map(d => ({ time: d.time, value: d.middle })));
        this.series.boll.lower.setData(ind.boll
          .filter(d => d.lower != null)
          .map(d => ({ time: d.time, value: d.lower })));
      }

      // MACD
      if (ind.macd && ind.macd.length > 0) {
        this.series.macd.line.setData(ind.macd.map(d => ({ time: d.time, value: d.macd })));
        this.series.macd.signal.setData(ind.macd.map(d => ({ time: d.time, value: d.signal })));
        this.series.macd.histogram.setData(ind.macd.map(d => ({
          time: d.time,
          value: d.histogram,
          color: d.histogram >= 0 ? 'rgba(235,87,87,0.45)' : 'rgba(39,174,96,0.45)',
        })));
      }

      // RSI
      if (ind.rsi && ind.rsi.length > 0) {
        this.series.rsi.setData(ind.rsi
          .filter(d => d.value != null)
          .map(d => ({ time: d.time, value: d.value })));
      }
    }

    // ---- 可见性 ----
    /** 切换某个指标组的显示 */
    toggle(name, on) {
      this.visible[name] = on;
      this._applyVisibility();
    }

    _applyVisibility() {
      const { visible, series } = this;

      // MA 组
      if (series.ma) {
        for (const line of Object.values(series.ma)) {
          line.applyOptions({ visible: visible.ma });
        }
      }
      // BOLL 组
      if (series.boll) {
        for (const line of Object.values(series.boll)) {
          line.applyOptions({ visible: visible.boll });
        }
      }
      // MACD 组
      if (series.macd) {
        for (const line of Object.values(series.macd)) {
          line.applyOptions({ visible: visible.macd });
        }
      }
      // RSI
      if (series.rsi) {
        series.rsi.applyOptions({ visible: visible.rsi });
        for (const ref of (this.rsiRefLines || [])) {
          ref.applyOptions({ visible: visible.rsi });
        }
      }
    }

    /** 调整图表尺寸（容器变化时调用） */
    resize() {
      this.chart.applyOptions({
        width: this.container.clientWidth,
        height: this.container.clientHeight,
      });
    }

    destroy() {
      if (this.chart) {
        this.chart.remove();
        this.chart = null;
      }
    }
  }

  return { ChartManager };
})();
