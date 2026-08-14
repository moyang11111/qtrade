# QTrade Desktop — 现代前端交易终端

基于 Python 后端 + HTML5/CSS3/JS 前端的 A 股量化交易终端，
内嵌 TradingView [lightweight-charts](https://github.com/tradingview/lightweight-charts)，
浏览器打开即是桌面级交易界面。

## 快速开始

```bash
cd src/qtrade/desktop

# 启动（自动打开浏览器）
python server.py --data-dir C:/Users/ASUS/qtrade/data/cache

# 或双击 run.bat
```

访问 http://127.0.0.1:8765

## 项目结构

```
desktop/
├── server.py              # 后端：零依赖 HTTP 服务 + JSON API
├── run.bat                # Windows 启动脚本
├── test_backend.py        # 后端自测脚本
├── check_js.py            # 前端 JS 语法检查脚本
└── static/
    ├── index.html         # 页面结构
    ├── css/style.css      # 暗黑主题样式
    └── js/
        ├── api.js         # API 封装层
        ├── chart.js       # 图表模块（轻量级封装）
        └── app.js         # 主应用逻辑
```

## 图表布局（lightweight-charts panes）

```
Pane 0 ── K线 + MA5/10/20/60 + BOLL（叠加） + 成交量（底部 overlay）
Pane 1 ── MACD（独立子图：DIF / DEA / 柱）
Pane 2 ── RSI（独立子图，含 30/70 参考线）
```

指标开关（[MA] [BOLL] [MACD] [RSI]）只控制对应 series 的可见性，
不重建图表。

## API 端点

| 端点 | 说明 |
|------|------|
| `GET /api/health` | 健康检查（返回股票数量） |
| `GET /api/symbols` | 全部股票代码 |
| `GET /api/kline/{symbol}?limit=400` | K 线 OHLCV |
| `GET /api/info/{symbol}` | 行情概要（最新价/涨跌幅/60日高低…） |
| `GET /api/indicators/{symbol}` | 指标（MA/MACD/RSI/BOLL，带缓存） |
| `GET /api/backtest?symbol=&strategy=&capital=&commission=&stop_loss=&take_profit=` | 回测 |

无效 symbol 返回 `404 {"error": "..."}`。

## 技术栈

- **后端**：Python 标准库 `http.server` + pandas/numpy（零额外依赖）
- **前端**：原生 HTML/CSS/JS（无框架）
- **图表**：TradingView lightweight-charts 4.2.1（CDN，首次加载需联网）
- **K 线配色**：红涨绿跌（A 股习惯）

## 开发调试

```bash
# 后端自测
python test_backend.py

# 前端 JS 语法检查（括号/引号配平）
python check_js.py

# 手动启动（不打开浏览器）
python server.py --no-browser --port 9000
```
