---
name: qtrade
description: 使用 QTrade 量化交易框架进行 A 股策略研究、回测、参数优化和实盘交易
---

# QTrade Skill

使用 QTrade 框架进行 A 股量化交易的全流程工作。

## 触发条件
当用户提到以下情况时激活：
- 量化交易、策略回测、股票分析
- qtrade、backtrader、backtest
- 技术指标、因子分析、策略优化
- 实盘交易、风控、资金管理

## 项目路径
- 本地路径: `C:\Users\ASUS\qtrade`
- 源码: `src/qtrade/`
- 配置: `configs/` (7 个 YAML 文件)
- 文档: README.md, QUICKSTART.md, docs/ARCHITECTURE.md

## 数据获取
```python
from qtrade.data import DataFetcher

# 无参构造（使用默认配置）
fetcher = DataFetcher()
data = fetcher.fetch_history(symbol="600519", start_date="2023-01-01", end_date="2023-12-31")

# 或带配置构造
from qtrade import load_config
cfg = load_config("configs/quick.yaml")
fetcher = DataFetcher(cfg)
data = fetcher.fetch("600519", "20230101", "20231231")
```

多数据源：pytdx / akshare / csv，自动故障转移和缓存。

## 策略开发与回测
所有策略实现统一的 `SignalGenerator` 接口，输出 `signal_action/signal_strength/signal_score` 三列。

**推荐写法（配置驱动）：**
```python
from qtrade import load_config, BacktestEngine
from qtrade.data import DataFetcher
from qtrade.strategy.registry import get_signal_generator

cfg = load_config("configs/quick.yaml")
fetcher = DataFetcher(cfg)
df = fetcher.fetch(cfg["data"]["symbol"], cfg["data"]["start_date"], cfg["data"]["end_date"])

# 策略名从 config 读取，参数通过 config 传递
strat_cls = get_signal_generator(cfg["strategy"]["name"])
generator = strat_cls({"name": cfg["strategy"]["name"], **cfg["strategy"].get("params", {})})
df_signals = generator.generate_signals(df)

engine = BacktestEngine(cfg)
result = engine.run(df_signals)
print(result.metrics)
result.plot()
result.save_report("report.html")
```

**快捷写法（兼容 QUICKSTART 文档）：**
```python
from qtrade import Config, BacktestEngine, DataFetcher
from qtrade.strategies import DualMAStrategy

config = Config.from_yaml("configs/backtest_example.yaml")
fetcher = DataFetcher()
data = fetcher.fetch_history(symbol="600519", start_date="2023-01-01", end_date="2023-12-31")

strategy = DualMAStrategy({"name": "dual_ma", "fast_window": 5, "slow_window": 20})
# 注意：engine.run() 接受的是已含信号的 DataFrame，需先调用 strategy.generate_signals()
df_signals = strategy.generate_signals(data)

engine = BacktestEngine(config)
result = engine.run(df_signals)
print(result.metrics)
result.plot()
result.save_report("report.html")
```

## 内置策略（15 个）
| 注册名 | 类名 | 类型 |
|--------|------|------|
| `dual_ma` | DualMASignal / DualMAStrategy | 均线 |
| `trend_5d` | Trend5DSignal / Trend5DStrategy | 均线 |
| `bollinger` | BollingerSignal / BollingerStrategy | 布林 |
| `bb_rsi` | BBRsiSignal / BBRsiStrategy | 布林 |
| `pullback_bb_mid` | PullbackBBMidSignal / PullbackBBMidStrategy | 布林 |
| `pullback_deep` | PullbackDeepSignal / PullbackDeepStrategy | 回调 |
| `pullback_vol` | PullbackVolSignal / PullbackVolStrategy | 回调 |
| `pullback_20d` | Pullback20DSignal / Pullback20DStrategy | 回调 |
| `breakout` | BreakoutSignal / BreakoutStrategy | 突破 |
| `adaptive` | AdaptiveSignal / AdaptiveStrategy | 自适应 |
| `hybrid` | AdaptiveHybridSignal / AdaptiveHybridStrategy | 自适应 |
| `event_driven` | EventDrivenSignal / EventDrivenStrategy | 事件 |
| `event_v2` | EventDrivenV2Signal / EventDrivenV2Strategy | 事件 |
| `regime_filter` | RegimeFilterSignal / RegimeFilterStrategy | 市场 |
| `regime_v2` | RegimeFilterV2Signal / RegimeFilterV2Strategy | 市场 |

## 特征工程
50+ 技术指标（`qtrade.features.technical`）：RSI, MACD, 布林带, ATR, 动量等。
```python
from qtrade.features.engine import FeatureEngine
fe = FeatureEngine({"indicators": ["rsi", "macd", "bb"]})
df_features = fe.compute(df)
```

## 参数优化
```python
from qtrade.optimization import GridSearchOptimizer, BayesianOptimizer

# 网格搜索
opt = GridSearchOptimizer(strategy_cls, param_grid={"fast_window": [3,5,10], "slow_window": [15,20,30]}, objective_func=my_objective)
best = opt.optimize(df)

# 贝叶斯优化（基于 Optuna）
opt = BayesianOptimizer(strategy_cls, param_space={"fast_window": {"type":"int","low":2,"high":20}}, objective_func=my_objective)
best = opt.optimize(df)
```

## 多策略组合
```python
from qtrade.portfolio.combiner import StrategyCombiner

combiner = StrategyCombiner([(strategy1, 0.5), (strategy2, 0.5)])
combined_signals = combiner.generate_signals(df)
```

## 风险控制
```python
from qtrade.risk_control.middleware import RiskMiddleware
from qtrade.risk_control.limits import PositionLimits, PortfolioLimits
from qtrade.risk_control.stop_loss import StopLossManager
from qtrade.risk_control.circuit_breaker import DrawdownBreaker

rm = RiskMiddleware(
    position_limits=PositionLimits(max_single_position_pct=0.2),
    stop_loss_manager=StopLossManager(stop_loss_pct=0.05),
    drawdown_breaker=DrawdownBreaker(max_drawdown=0.15),
)
```

## 实盘交易
```python
from qtrade.live_trading import LiveTrader
from qtrade.live_trading.broker import MockBroker
from qtrade.live_trading.data_feed import RealtimeDataFeed
from qtrade.live_trading.risk_monitor import RiskMonitor

broker = MockBroker(initial_capital=1000000)
data_feed = RealtimeDataFeed(["600519"])
trader = LiveTrader(
    broker=broker,
    data_feed=data_feed,
    strategy=strategy,
    risk_monitor=RiskMonitor(),  # 注意：参数名是 risk_monitor
)
trader.start(["600519"])
```

## 可视化
```python
# BacktestResult 方法
result.plot()                    # 资金曲线 + 回撤图
result.plot_equity_curve()       # 仅资金曲线
result.plot_drawdown()           # 仅回撤图
result.save_report("r.html")     # 生成 QuantStats HTML 报告
result.save_quantstats_report()  # 同上

# 独立函数
from qtrade.visualization.charts import plot_equity_curve, plot_drawdown
from qtrade.backtest.report import generate_quantstats_report
```

## CLI
```bash
qtrade backtest  --config configs/quick.yaml --plot --report
qtrade train     --config configs/ml_xgboost.yaml
qtrade compare   --config configs/quick.yaml --strategies dual_ma bollinger --plot
qtrade optimize  --config configs/optimization_example.yaml --method grid
qtrade live      --config configs/live_trading_example.yaml --symbols 600519 000001
qtrade report    --equity-csv results/equity.csv --output report.html
```

## 配置文件
| 文件 | 用途 |
|------|------|
| `configs/quick.yaml` | 快速回测（默认） |
| `configs/default.yaml` | 完整回测配置 |
| `configs/backtest_example.yaml` | 示例回测 |
| `configs/optimization_example.yaml` | 参数优化 |
| `configs/live_trading_example.yaml` | 实盘/模拟盘 |
| `configs/multi_strategy_example.yaml` | 多策略组合 |
| `configs/ml_xgboost.yaml` | ML 训练+回测 |

## 工作流程
1. 数据探索（EDA: `qtrade.eda`）→ 2. 特征工程（`qtrade.features`）→ 3. 策略开发（`qtrade.strategy`）→ 4. 回测验证（`qtrade.backtest`）→ 5. 参数优化（`qtrade.optimization`）→ 6. 风险评估（`qtrade.risk_control`）→ 7. 模拟盘（`qtrade.live_trading` + MockBroker）→ 8. 实盘部署 → 9. 监控与评估

## 最佳实践
- 数据质量：用 `qtrade.eda.quality` 检查完整性、处理缺失值
- 防过拟合：使用 `qtrade.optimization.walk_forward.WalkForwardValidator` 做步进式验证
- 风险第一：仓位限制、止损、熔断（`RiskMiddleware`）
- 模拟盘充分测试后再上实盘
- 策略必须先 `generate_signals(df)` 生成信号列，再将含信号 DataFrame 传入 `engine.run()`
