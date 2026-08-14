# QTrade - A股量化交易框架 & 桌面终端

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A 股量化交易框架 + 桌面交易终端：15 种内置策略，支持从数据获取、策略研究、回测验证到实盘模拟的完整工作流，并提供现代 Web/桌面交易界面。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动模拟盘（默认监控 7 只主力信号股）
python scripts/paper_trading.py

# 指定股票
python scripts/paper_trading.py --symbols 002580,000066,002297

# 龙虎榜回测
python scripts/backtest_lhb_pullback.py
```

## 核心策略: Pullback20D

基于 3373 只 A 股主板 1 年回测验证的最优策略：

```
买入条件:
  ① 距60日高点回落 15%-40%
  ② 5日均量 / 20日均量 < 0.7（缩量）
  ③ 收盘价 > MA60

卖出: 持有满 20 个交易日

回测结果 (2025.06-2026.05):
  胜率 42% | 平均 +0.5% | 最佳 +461%
```

## 全部 15 个策略

| 策略 | 注册名 | 类型 |
|------|--------|------|
| 双均线 | dual_ma | 均线 |
| 五日趋势 | trend_5d | 均线 |
| 布林带 | bollinger | 布林 |
| 布林+RSI | bb_rsi | 布林 |
| 回调布林中轨 | pullback_bb_mid | 布林 |
| 深度回调 | pullback_deep | 回调 |
| 量价共振 | pullback_vol | 回调 |
| 缩量持有20日 | pullback_20d | 回调 |
| 突破 | breakout | 突破 |
| 自适应 | adaptive | 自适应 |
| 混合自适应 | hybrid | 自适应 |
| 事件驱动 V1/V2 | event_driven / event_v2 | 事件 |
| 市场状态 V1/V2 | regime_filter / regime_v2 | 市场 |

## 目录结构

```
qtrade/
├── src/qtrade/
│   ├── data/          # 多数据源 (TDX/AkShare/CSV) + 龙虎榜
│   ├── strategy/      # 15 个策略
│   ├── backtest/      # 回测引擎
│   ├── live_trading/  # 实盘模拟 (通达信/腾讯行情)
│   ├── optimization/  # 参数优化
│   ├── ml/            # ML Pipeline
│   └── visualization/ # 图表报告
├── scripts/
│   ├── paper_trading.py      # 模拟盘终端
│   ├── backtest_pullback_20d.py  # Pullback20D 回测
│   └── download_main_board.py    # 全市场数据下载
├── data/cache/        # K线缓存 (CSV)
└── configs/           # YAML 配置
```

## 行情源

- 通达信 (pytdx): TCP 协议，五档盘口，3 秒刷新
- 腾讯 HTTP: 备用通道，5 秒刷新
- 启动时自动优选，不通则自动切换

---

## 桌面终端 (qtrade-desktop)

基于 Python 后端 + HTML5/CSS3/JS 前端的 A 股量化交易终端，内嵌 TradingView [lightweight-charts](https://github.com/tradingview/lightweight-charts)，浏览器打开即是桌面级交易界面。

### 启动

```bash
cd qtrade_desktop
python server.py --data-dir C:/Users/ASUS/qtrade/data/cache   # 或双击 run.bat
```

打开 http://127.0.0.1:8765

也提供 PySide6 桌面版（`desktop_app.py`）与 Electron 壳（`electron/`）。

### 桌面终端功能

- **K线图表**：K线 + MA5/10/20/60 + BOLL + 成交量（Pane 0）、MACD（Pane 1）、RSI（Pane 2），指标可开关
- **股票列表**：全部 A 股 + 自选股 + 搜索 + 最近浏览
- **策略信号**：内置策略信号展示
- **DSA AI 观点**：接入 daily-stock-analysis 数据库，展示 AI 买卖/持有观点（`api/dsa/analyze/{symbol}` 提供实时技术分析兜底）
- **训练营**：猜涨跌 / 买卖点 / 逐根复盘三模式，含 MA+BOLL 指标、150 交易日复盘
- **回测**：多策略回测（dual_ma / bollinger / rsi_layered 等）
- **AI 模拟盘**：AI 自动模拟交易

### 桌面终端 API 端点

| 端点 | 说明 |
|------|------|
| `GET /api/health` | 健康检查（返回股票数量） |
| `GET /api/symbols` | 全部股票列表 |
| `GET /api/kline/{symbol}?limit=400` | K 线 OHLCV |
| `GET /api/info/{symbol}` | 最新价/涨跌/60日高低等 |
| `GET /api/indicators/{symbol}` | 指标（MA/MACD/RSI/BOLL） |
| `GET /api/backtest?symbol=&strategy=&capital=...` | 回测 |
| `GET /api/dsa/analyze/{symbol}` | DSA 实时技术分析 |
| `GET /api/training/next?lookback=&horizon=` | 训练营题目 |

无效 symbol 返回 `404 {"error": "..."}`。

### 桌面终端技术栈

- **后端**：Python 标准库 `http.server` + pandas/numpy
- **前端**：原生 HTML/CSS/JS
- **图表**：TradingView lightweight-charts 4.2.1（CDN）
- **K线颜色**：红涨绿跌（A 股习惯）

## License

MIT
