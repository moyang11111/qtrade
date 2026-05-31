# QTrade 快速开始指南

本指南帮助你在 5 分钟内开始使用 QTrade 量化交易框架。

## 📦 安装

### 方式 1: pip 安装（推荐）

```bash
# 基础安装
pip install qtrade

# 完整安装（包含所有功能）
pip install qtrade[all]

# 按需安装
pip install qtrade[data,ml,live,web]  # 数据 + ML + 实盘 + Web界面
```

### 方式 2: 从源码安装

```bash
git clone https://github.com/qtrade/qtrade.git
cd qtrade
pip install -e ".[all]"
```

### 方式 3: Docker 部署

```bash
git clone https://github.com/qtrade/qtrade.git
cd qtrade
cp .env.example .env
# 编辑 .env 配置你的 API 密钥

docker-compose up -d
```

## 🚀 5 分钟快速体验

### 1. 运行你的第一个回测

创建文件 `my_first_backtest.py`:

```python
from qtrade import Config, BacktestEngine, DataFetcher
from qtrade.strategies import DualMAStrategy

# 1. 加载配置
config = Config.from_yaml("configs/backtest_example.yaml")

# 2. 获取数据
fetcher = DataFetcher()
data = fetcher.fetch_history(
    symbol="600519",           # 贵州茅台
    start_date="2023-01-01",
    end_date="2023-12-31"
)

# 3. 创建策略
strategy = DualMAStrategy(fast_window=5, slow_window=20)

# 4. 运行回测
engine = BacktestEngine(config)
result = engine.run(strategy, data)

# 5. 查看结果
print(f"总收益率: {result.metrics['total_return']:.2f}%")
print(f"夏普比率: {result.metrics['sharpe_ratio']:.2f}")
print(f"最大回撤: {result.metrics['max_drawdown']:.2f}%")

# 6. 生成报告
result.plot()
result.save_report("my_first_report.html")
```

运行:

```bash
python my_first_backtest.py
```

### 2. 使用 CLI 工具

```bash
# 运行回测
qtrade backtest --config configs/backtest_example.yaml

# 查看可用策略
qtrade strategies list

# 参数优化
qtrade optimize --config configs/optimization_example.yaml

# 生成报告
qtrade report --backtest-id <id> --output report.html
```

### 3. 启动 Web 界面

```bash
# 终端 1: 启动 API 服务
uvicorn qtrade.api.main:app --host 0.0.0.0 --port 8000

# 终端 2: 启动 Web 仪表板
streamlit run src/qtrade/web/dashboard.py --server.port 8501
```

打开浏览器访问:
- API 文档: http://localhost:8000/docs
- Web 仪表板: http://localhost:8501

### 4. 使用 Docker（最简单）

```bash
# 启动所有服务
docker-compose up -d

# 访问
# API: http://localhost:8000
# Web 仪表板: http://localhost:8501
```

## 📚 核心概念

### 1. 配置文件

QTrade 使用 YAML 配置文件管理所有参数。主要配置项：

```yaml
data:                    # 数据源配置
  source: pytdx
  symbol: "600519"
  
backtest:                # 回测引擎配置
  initial_capital: 1000000
  commission: 0.0003
  
strategy:                # 策略配置
  name: dual_ma
  params:
    fast_window: 5
    slow_window: 20
    
risk_control:            # 风险控制
  max_position_pct: 0.2
  stop_loss: 0.05
```

### 2. 策略接口

所有策略都实现统一的 `generate_signals()` 接口:

```python
from qtrade.strategies import StrategyBase

class MyStrategy(StrategyBase):
    def generate_signals(self, df):
        signals = pd.DataFrame(index=df.index)
        signals['signal_action'] = 0      # 0=持有, 1=买入, -1=卖出
        signals['signal_strength'] = 0.0  # 信号强度 0-1
        return signals
```

### 3. 数据获取

```python
from qtrade.data import DataFetcher

fetcher = DataFetcher()

# 获取历史数据
data = fetcher.fetch_history(symbol="600519", start_date="2023-01-01")

# 获取实时数据（需要 live 依赖）
realtime = fetcher.fetch_realtime(symbols=["600519", "000001"])
```

### 4. 回测引擎

```python
from qtrade.backtest import BacktestEngine

engine = BacktestEngine(config)
result = engine.run(strategy, data)

# 查看绩效指标
print(result.metrics)

# 生成图表
result.plot_equity_curve()
result.plot_drawdown()

# 导出报告
result.save_report("report.html")
```

## 🎯 常见使用场景

### 场景 1: 策略研究

```python
# 1. 获取数据
data = fetcher.fetch_history(symbol="600519", start_date="2020-01-01")

# 2. 特征工程
from qtrade.features import TechnicalIndicators
features = TechnicalIndicators.compute_all(data)

# 3. 策略开发
strategy = MyCustomStrategy()

# 4. 回测验证
result = engine.run(strategy, data)

# 5. 参数优化
from qtrade.optimization import BayesianOptimizer
optimizer = BayesianOptimizer(strategy, param_space={...})
best_params = optimizer.optimize(data)
```

### 场景 2: 多策略组合

```python
from qtrade.portfolio import StrategyCombiner

combiner = StrategyCombiner()

# 添加多个策略
combiner.add_strategy(strategy1, weight=0.4)
combiner.add_strategy(strategy2, weight=0.3)
combiner.add_strategy(strategy3, weight=0.3)

# 生成组合信号
combined_signals = combiner.generate_signals(data)

# 回测组合
result = engine.run_portfolio(combiner, data)
```

### 场景 3: 实盘交易

```python
from qtrade.live_trading import LiveTrader
from qtrade.live_trading.broker import MockBroker

# 1. 配置券商（模拟）
broker = MockBroker(initial_capital=1000000)

# 2. 创建实时数据源
data_feed = RealtimeDataFeed(symbols=["600519"])

# 3. 创建交易员
trader = LiveTrader(
    broker=broker,
    data_feed=data_feed,
    strategy=strategy,
    risk_control=risk_control
)

# 4. 启动交易
trader.start()

# 5. 监控状态
status = trader.get_status()
print(status)
```

### 场景 4: 机器学习策略

```python
from qtrade.strategies import MLStrategyBase
from xgboost import XGBClassifier

class MLStrategy(MLStrategyBase):
    def __init__(self):
        super().__init__()
        self.model = XGBClassifier()
    
    def train(self, data):
        features = self.extract_features(data)
        labels = self.create_labels(data)
        self.model.fit(features, labels)
    
    def generate_signals(self, data):
        features = self.extract_features(data)
        predictions = self.model.predict_proba(features)
        
        signals = pd.DataFrame(index=data.index)
        signals['signal_action'] = (predictions[:, 1] > 0.6).astype(int)
        signals['signal_strength'] = predictions[:, 1]
        return signals

# 使用
strategy = MLStrategy()
strategy.train(train_data)
result = engine.run(strategy, test_data)
```

## 🔧 配置示例

项目提供了 4 个完整的配置示例：

1. **backtest_example.yaml** - 简单回测配置
   ```bash
   qtrade backtest --config configs/backtest_example.yaml
   ```

2. **optimization_example.yaml** - 参数优化配置
   ```bash
   qtrade optimize --config configs/optimization_example.yaml
   ```

3. **live_trading_example.yaml** - 实盘交易配置
   ```bash
   qtrade live --config configs/live_trading_example.yaml
   ```

4. **multi_strategy_example.yaml** - 多策略组合配置
   ```bash
   qtrade backtest --config configs/multi_strategy_example.yaml
   ```

## 📊 查看结果

### 1. 绩效指标

```python
result.metrics
# {
#     'total_return': 45.32,
#     'annual_return': 23.15,
#     'sharpe_ratio': 1.85,
#     'max_drawdown': -12.34,
#     'win_rate': 58.33,
#     ...
# }
```

### 2. 可视化图表

```python
# 资金曲线
result.plot_equity_curve()

# 回撤分析
result.plot_drawdown()

# 月度收益
result.plot_monthly_returns()

# 交易记录
result.plot_trades()
```

### 3. HTML 报告

```python
# 生成完整报告
result.save_report("report.html")

# 使用 QuantStats 生成专业报告
result.save_quantstats_report("quantstats_report.html")
```

## 🐛 常见问题

### Q: 数据获取失败怎么办？

A: 检查网络连接，或切换数据源：

```yaml
data:
  source: pytdx
  fallback: [akshare]  # 添加备用数据源
```

### Q: 回测速度很慢？

A: 使用向量化回测：

```python
from qtrade.backtest import VectorizedBacktestEngine
engine = VectorizedBacktestEngine(config)  # 更快
```

### Q: 如何使用真实券商？

A: 配置真实券商 API：

```yaml
broker:
  type: alpaca
  alpaca:
    api_key: ${ALPACA_API_KEY}
    api_secret: ${ALPACA_API_SECRET}
    base_url: https://api.alpaca.markets  # 真实环境
```

### Q: 如何添加自定义指标？

A: 继承 `TechnicalIndicator` 类：

```python
from qtrade.features import TechnicalIndicator

class MyIndicator(TechnicalIndicator):
    @staticmethod
    def compute(df, **kwargs):
        # 你的指标计算逻辑
        return result
```

## 📖 更多资源

- **完整文档**: https://qtrade.readthedocs.io
- **示例代码**: https://github.com/qtrade/qtrade/tree/main/examples
- **问题反馈**: https://github.com/qtrade/qtrade/issues
- **更新日志**: https://github.com/qtrade/qtrade/blob/main/CHANGELOG.md

## 💡 下一步

1. ✅ 运行你的第一个回测
2. 📚 阅读完整文档了解高级功能
3. 🔬 尝试参数优化
4. 🎯 开发你的自定义策略
5. 🚀 部署到实盘环境

---

**祝你交易顺利！** 📈
