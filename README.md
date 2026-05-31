# QTrade - A股量化交易框架

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A 股量化交易框架，15 种内置策略，支持从数据获取、策略研究、回测验证到实盘模拟的完整工作流。

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
| 双均线 | `dual_ma` | 均线 |
| 五日趋势 | `trend_5d` | 均线 |
| 布林带 | `bollinger` | 布林 |
| 布林+RSI | `bb_rsi` | 布林 |
| 回调布林中轨 | `pullback_bb_mid` | 布林 |
| 深度回调 | `pullback_deep` | 回调 |
| 量价共振 | `pullback_vol` | 回调 |
| **缩量持有20日** | **`pullback_20d`** | **回调** |
| 突破 | `breakout` | 突破 |
| 自适应 | `adaptive` | 自适应 |
| 混合自适应 | `hybrid` | 自适应 |
| 事件驱动 V1/V2 | `event_driven` / `event_v2` | 事件 |
| 市场状态 V1/V2 | `regime_filter` / `regime_v2` | 市场 |

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

- **通达信 (pytdx)**: TCP 协议，五档盘口，3 秒刷新
- **腾讯 HTTP**: 备用通道，5 秒刷新
- 启动时自动优选，不通则自动切换

## License

MIT
