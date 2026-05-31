# QTrade Skill

## 描述
使用 QTrade 量化交易框架进行 A 股策略研究、回测和实盘交易。

## 触发条件
当用户提到以下任何情况时自动激活：
- 量化交易、策略回测、股票分析
- qtrade、backtrader、backtest
- 技术指标、因子分析、策略优化
- 实盘交易、风控、资金管理
- 需要使用 qtrade 框架

## 核心能力

### 1. 数据获取与管理
```python
from qtrade.data import DataFetcher

# 获取历史数据
fetcher = DataFetcher()
data = fetcher.fetch_history(
    symbol="600519",           # 股票代码
    start_date="2023-01-01",
    end_date="2023-12-31"
)

# 多数据源支持：pytdx, akshare, csv
# 自动故障转移和数据缓存
```

### 2. 策略开发与回测
```python
from qtrade.strategies import DualMAStrategy
from qtrade.backtest import BacktestEngine
from qtrade import Config

# 加载配置
config = Config.from_yaml("configs/default.yaml")

# 创建策略
strategy = DualMAStrategy(fast_window=5, slow_window=20)

# 运行回测
engine = BacktestEngine(config)
result = engine.run(strategy, data)

# 查看结果
print(result.metrics)
result.plot()
result.save_report("report.html")
```

### 3. 特征工程
```python
from qtrade.features import TechnicalIndicators

# 计算技术指标
indicators = TechnicalIndicators()
features = indicators.compute_all(data)

# 支持的指标：RSI, MACD, 布林带, ATR, 动量等 50+ 种
```

### 4. 参数优化
```python
from qtrade.optimization import GridSearchOptimizer, BayesianOptimizer

# 网格搜索
optimizer = GridSearchOptimizer(
    strategy_class=DualMAStrategy,
    param_grid={
        'fast_window': [5, 10, 15],
        'slow_window': [20, 30, 40]
    }
)
best_params = optimizer.optimize(data)

# 贝叶斯优化（更智能）
bayesian_opt = BayesianOptimizer(
    strategy_class=DualMAStrategy,
    param_space={
        'fast_window': (5, 20),
        'slow_window': (20, 60)
    },
    objective_func=lambda s: s.sharpe_ratio
)
```

### 5. 多策略组合
```python
from qtrade.portfolio import StrategyCombiner

combiner = StrategyCombiner()
combiner.add_strategy(strategy1, weight=0.5)
combiner.add_strategy(strategy2, weight=0.3)
combiner.add_strategy(strategy3, weight=0.2)

# 生成组合信号
combined_signals = combiner.generate_signals(data)
```

### 6. 风险控制
```python
from qtrade.risk_control import RiskMiddleware

risk_control = RiskMiddleware(
    max_position_pct=0.2,        # 单只股票最大仓位 20%
    max_drawdown=0.15,           # 最大回撤 15%
    stop_loss=0.05,              # 止损 5%
    daily_loss_limit=0.03        # 单日亏损限制 3%
)
```

### 7. 实盘交易
```python
from qtrade.live_trading import LiveTrader
from qtrade.live_trading.broker import MockBroker

# 配置券商
broker = MockBroker(initial_capital=1000000)

# 创建实时数据源
data_feed = RealtimeDataFeed(symbols=["600519"])

# 启动实盘交易
trader = LiveTrader(
    broker=broker,
    data_feed=data_feed,
    strategy=strategy,
    risk_control=risk_control
)
trader.start()
```

### 8. 可视化与报告
```python
# 生成图表
result.plot_equity_curve()
result.plot_drawdown()
result.plot_monthly_returns()

# 生成 HTML 报告
result.save_report("analysis.html")

# 使用 QuantStats
result.save_quantstats_report("quantstats.html")
```

## CLI 命令

```bash
# 运行回测
qtrade backtest --config configs/default.yaml --symbol 600519

# 参数优化
qtrade optimize --config configs/optimization.yaml

# 启动实盘交易
qtrade live --config configs/live.yaml

# 生成报告
qtrade report --backtest-id <id> --output report.html
```

## 配置文件示例

### configs/default.yaml
```yaml
data:
  source: pytdx
  symbol: "600519"
  start_date: "2023-01-01"
  end_date: "2023-12-31"

strategy:
  name: dual_ma
  params:
    fast_window: 5
    slow_window: 20

backtest:
  initial_capital: 1000000
  commission: 0.001
  slippage: 0.001

risk_control:
  max_position_pct: 0.2
  max_drawdown: 0.15
  stop_loss: 0.05
```

## 工作流程

### 策略研究流程
1. 数据探索（EDA）
2. 特征工程
3. 策略开发
4. 回测验证
5. 参数优化
6. 风险评估

### 实盘交易流程
1. 策略回测验证
2. 模拟盘测试
3. 风控配置
4. 实盘部署
5. 实时监控
6. 定期评估

## 常用策略模板

### 双均线策略
```python
class DualMAStrategy(StrategyBase):
    def __init__(self, fast_window=5, slow_window=20):
        self.fast_window = fast_window
        self.slow_window = slow_window
    
    def generate_signals(self, df):
        signals = pd.DataFrame(index=df.index)
        
        fast_ma = df['close'].rolling(self.fast_window).mean()
        slow_ma = df['close'].rolling(self.slow_window).mean()
        
        signals['signal_action'] = 0
        signals.loc[fast_ma > slow_ma, 'signal_action'] = 1   # 买入
        signals.loc[fast_ma < slow_ma, 'signal_action'] = -1  # 卖出
        signals['signal_strength'] = 0.8
        
        return signals
```

### RSI 策略
```python
class RSIStrategy(StrategyBase):
    def __init__(self, period=14, oversold=30, overbought=70):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
    
    def generate_signals(self, df):
        signals = pd.DataFrame(index=df.index)
        
        # 计算 RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        signals['signal_action'] = 0
        signals.loc[rsi < self.oversold, 'signal_action'] = 1    # 超卖买入
        signals.loc[rsi > self.overbought, 'signal_action'] = -1  # 超买卖出
        signals['signal_strength'] = 0.7
        
        return signals
```

## 最佳实践

### 1. 数据质量
- 始终检查数据完整性
- 处理缺失值和异常值
- 验证数据对齐

### 2. 回测验证
- 使用走步式验证防止过拟合
- 考虑交易成本（手续费、滑点）
- 进行样本外测试

### 3. 风险管理
- 设置合理的仓位限制
- 配置止损和熔断机制
- 监控实时风险指标

### 4. 实盘交易
- 先用模拟盘充分测试
- 配置完整的告警系统
- 保持详细的交易日志

## 故障排除

### 数据获取失败
```python
# 配置备用数据源
data_config = {
    'source': 'pytdx',
    'fallback': ['akshare', 'csv']
}
```

### 回测性能慢
```python
# 使用向量化回测
from qtrade.backtest import VectorizedBacktestEngine
engine = VectorizedBacktestEngine(config)
```

### 内存不足
```python
# 分批处理数据
for chunk in data.chunk(1000):
    signals = strategy.generate_signals(chunk)
```

## 项目资源

- **文档**: https://qtrade.readthedocs.io
- **代码仓库**: https://github.com/qtrade/qtrade
- **问题反馈**: https://github.com/qtrade/qtrade/issues
- **示例代码**: examples/ 目录

## 依赖安装

```bash
# 基础安装
pip install qtrade

# 完整安装（推荐）
pip install qtrade[all]

# 按需安装
pip install qtrade[data,ml,live,web,optimization]
```

## 版本信息

当前版本: 1.0.0
Python 支持: 3.10+
许可证: MIT

## 注意事项

1. **实盘风险**: 实盘交易涉及真实资金，请谨慎操作
2. **数据延迟**: 实时数据可能有延迟，影响策略执行
3. **市场风险**: 历史表现不代表未来收益
4. **系统风险**: 网络故障、系统宕机可能导致损失

建议先在模拟盘充分测试后再进行实盘交易。
