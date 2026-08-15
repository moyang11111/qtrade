# QTrade 快速开始指南

本指南帮助你在 5 分钟内开始使用 QTrade 量化交易框架。

## 安装

### 方式 1: 从源码安装（推荐）

```bash
git clone https://github.com/qtrade/qtrade.git
cd qtrade
pip install -e .          # 基础安装

# 或安装完整依赖
pip install -e ".[all]"
```

也可以用 requirements.txt：

```bash
pip install -r requirements.txt
```

### 方式 2: Docker 部署

```bash
cd qtrade
cp .env.example .env
docker-compose up -d
```

## 5 分钟快速体验

### 1. 运行你的第一个回测

```python
import sys
sys.path.insert(0, "src")  # 未 pip install 时

from qtrade import BacktestEngine, DataFetcher
from qtrade.strategy.registry import get_signal_generator

# 1. 配置
cfg = {
    "data": {"symbol": "600519", "start_date": "20230101", "end_date": "20231231"},
    "backtest": {"initial_capital": 100000, "commission": 0.0003, "slippage": 0.001},
    "strategy": {"name": "dual_ma", "type": "rule",
                 "params": {"fast_period": 5, "slow_period": 20}},
}

# 2. 获取数据
fetcher = DataFetcher(cfg)
data = fetcher.fetch_history("600519", "2023-01-01", "2023-12-31")

# 3. 创建策略并生成信号
strategy_cls = get_signal_generator("dual_ma")
signals = strategy_cls({"name": "dual_ma", "fast_period": 5, "slow_period": 20}).generate_signals(data)

# 4. 运行回测
engine = BacktestEngine(cfg)
result = engine.run(signals)

# 5. 查看结果
print(result.metrics["total_return"])
print(result.metrics["sharpe_ratio"])
print(result.metrics["max_drawdown"])

# 6. 生成报告（需要 quantstats）
result.save_report("my_first_report.html")
```

### 2. 使用 CLI 工具

```bash
# 运行回测
python -m qtrade backtest -c configs/backtest_example.yaml

# 参数优化
python -m qtrade optimize -c configs/optimization_example.yaml

# 比较全部策略
python -m qtrade compare -c configs/backtest_example.yaml --strategies dual_ma bollinger

# 从资金曲线 CSV 生成 HTML 报告
python -m qtrade report --equity-csv results/equity.csv -o report.html
```

### 3. 启动桌面交易终端

```bash
# 项目根目录，默认端口 8765，自动打开浏览器
python server.py --data-dir data/cache

# 或双击 run.bat
```

打开 http://127.0.0.1:8765

### 4. 启动 API 服务

```bash
uvicorn qtrade.api.main:app --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000/docs 查看接口文档。

## 核心概念

### 1. 策略接口

所有策略都实现统一的 `generate_signals()` 接口：

```python
from qtrade.strategy.base import SignalGenerator

class MyStrategy(SignalGenerator):
    def generate_signals(self, df):
        result = df.copy()
        result["signal_action"] = 0      # 0=持有, 1=买入, -1=卖出
        result["signal_strength"] = 0.0  # 信号强度 0-1
        result["signal_score"] = 0.0
        return result
```

内置策略通过 `get_signal_generator("dual_ma")` 获取，`list_strategies()` 查看全部名称。

### 2. 数据获取

```python
from qtrade.data import DataFetcher

fetcher = DataFetcher()  # 默认 pytdx + akshare 回退
data = fetcher.fetch_history("600519", "2023-01-01", "2023-12-31")
```

### 3. 回测引擎

`BacktestEngine.run(df)` 接收**已带信号列**的 DataFrame：

```python
engine = BacktestEngine(cfg)
result = engine.run(signals)
print(result.metrics)
result.plot_equity_curve()
result.save_report("report.html")
```

### 4. 参数优化

优化器面向 `SignalGenerator`：

```python
from qtrade.optimization.grid_search import GridSearchOptimizer
from qtrade.strategy.registry import get_signal_generator

def objective(strategy, df):
    sig = strategy.generate_signals(df)
    result = BacktestEngine(cfg).run(sig)
    return result.metrics.get("total_return", -999)

optimizer = GridSearchOptimizer(
    get_signal_generator("dual_ma"),
    {"fast_period": [3, 5, 10], "slow_period": [15, 20, 30]},
    objective,
)
best = optimizer.optimize(data)
print(best["best_params"], best["best_score"])
```

### 5. 模拟盘

```bash
# 多策略模拟盘（默认 pullback_20d + pullback_deep）
python scripts/paper_trading.py

# 指定策略/资金与股票
python scripts/paper_trading.py --strategies dual_ma:300000,pullback_20d:500000 --symbols 600519,000001

# 全市场
python scripts/paper_trading.py --all
```

## 常见问题

### Q: 数据获取失败怎么办？

A: 检查网络，或切换数据源。`data.cache` 目录下有本地 CSV 时，`DataFetcher` 会优先使用完整覆盖请求区间的缓存。

### Q: 回测结果和桌面端不一致？

A: 桌面端 `server.py` 内置了简化的回测模拟器；框架 `BacktestEngine` 的 A 股成本模型（佣金+最低佣金+印花税+T+1）更完整，建议以框架为准。

### Q: 如何添加自定义指标？

A: 在 `src/qtrade/features/` 下扩展特征函数，并在 `FeatureEngine.compute_features()` 中注册。

## 更多资源

- 完整文档：`docs/ARCHITECTURE.md`
- 配置示例：`configs/*.yaml`
- 更新日志：`CHANGELOG.md`
