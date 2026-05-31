# 更新日志

所有重要更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/),
并且本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [1.0.0] - 2024-01-15

### 🎉 首次发布

QTrade 量化交易框架正式发布！这是一个功能完善的 A 股量化交易框架，支持从策略研究、回测验证到实盘交易的完整工作流。

### ✨ 核心功能

#### 数据管理 (Phase 3)
- **多数据源支持**: TDX（通达信）、AkShare、CSV 等多种数据源
- **智能数据获取**: 自动故障转移、数据缓存、并行下载
- **数据质量保证**: 自动清洗、异常值检测、数据对齐
- **统一数据接口**: `DataSource`、`Storage`、`Executor` 抽象层
- **AkShare 数据源**: 集成 AkShare 作为备用数据源
- **Parquet 存储**: 支持高效的 Parquet 格式存储

#### 特征工程 (Phase 5)
- **丰富的技术指标**: RSI、MACD、布林带、ATR 等 50+ 技术指标
- **特征库管理**: 版本化特征库，支持特征复用和共享
- **因子框架**: 集成 Qlib 因子框架，支持自定义因子
- **EDA 工作流**: 完整的数据探索分析流程
  - 数据质量分析（缺失值、异常值、重复值）
  - 特征分布分析（直方图、Q-Q 图、统计检验）
  - 特征相关性分析（相关矩阵、VIF 分析）
  - 特征稳定性分析（PSI、时间序列稳定性）
- **特征版本控制**: 支持特征版本管理和回滚

#### 策略系统 (Phase 2 & 6)
- **统一策略接口**: 规则策略和 ML 策略使用相同的 `generate_signals()` 接口
- **内置策略**: 双均线、RSI+布林带、动量策略等多种经典策略
- **ML 策略支持**: 集成 XGBoost、LightGBM、PyTorch 等机器学习框架
- **策略注册中心**: 统一的策略注册和管理
- **规则策略基类**: `RuleBasedStrategy` 简化规则策略开发
- **ML 策略基类**: `MLStrategy` 简化机器学习策略开发

#### 回测引擎 (Phase 3)
- **参数化回测**: 可配置的手续费、滑点、仓位管理
- **高性能**: 基于 Backtrader，支持向量化回测（vectorbt）
- **详细报告**: 自动生成包含图表的 HTML 回测报告
- **Broker 抽象**: 统一的券商接口抽象层
- **Mock Broker**: 模拟券商用于测试
- **Alpaca Broker**: 集成 Alpaca 真实券商

#### 参数优化 (Phase 6)
- **网格搜索**: 穷举搜索最优参数组合
- **贝叶斯优化**: 基于 Optuna 的智能参数搜索
- **走步式验证**: 时间序列交叉验证，防止过拟合
- **参数稳定性分析**: 评估参数对结果的敏感度

#### 多策略组合 (Phase 6)
- **策略组合器**: 支持多策略并行运行
- **资金分配**: 灵活的策略权重配置
- **信号集成**: 投票法、加权平均、元学习器等多种集成方式
- **策略组合管理**: `StrategyPortfolio` 管理多策略组合
- **信号集成器**: `SignalEnsemble` 集成多个策略的信号

#### 风险控制 (Phase 6)
- **仓位限制**: 单股票和组合级别的仓位控制
- **止损管理**: 固定止损、追踪止损、ATR 止损
- **熔断机制**: 日损失熔断、回撤熔断
- **风控中间件**: 统一的交易前风控检查
- **仓位限制器**: `PositionLimits` 控制仓位大小
- **组合限制器**: `PortfolioLimits` 控制组合风险
- **止损管理器**: `StopLossManager` 管理止损逻辑
- **熔断器**: `CircuitBreaker` 实现熔断机制
- **回撤熔断器**: `DrawdownBreaker` 基于回撤的熔断

#### 实盘交易 (Phase 8)
- **券商接口**: 支持 Alpaca、掘金等券商 API
- **实时行情**: WebSocket 和轮询两种模式
- **订单管理**: 完整的订单生命周期管理
- **持仓同步**: 实时持仓同步和对账
- **风险监控**: 实时风控和告警系统
- **全链路日志**: 完整的交易日志审计
- **券商适配器**: `BrokerAdapter` 抽象层
  - `MockBroker`: 模拟券商
  - `AlpacaBroker`: Alpaca 券商
- **实时数据源**: `RealtimeDataFeed` 实时行情
  - `WebSocketFeed`: WebSocket 实时数据
  - `PollingFeed`: 轮询实时数据
- **订单管理器**: `OrderManager` 管理订单生命周期
- **持仓同步器**: `PositionSynchronizer` 同步持仓信息
- **风险监控器**: `RiskMonitor` 实时风险监控
- **告警系统**: `AlertSystem` 多渠道告警
  - `EmailAlert`: 邮件告警
  - `WebhookAlert`: Webhook 告警（Slack、Discord、企业微信）
- **实盘交易器**: `LiveTrader` 实盘交易主控制器

#### 可视化 (Phase 7)
- **交互式图表**: 基于 Plotly 的交互式图表
- **一键出图**: 资金曲线、回撤分析、年度收益等
- **QuantStats 集成**: 专业的量化分析报告
- **Web 仪表板**: Streamlit 实时监控界面
- **图表模块**: `visualization/charts.py`
  - `plot_equity_curve`: 资金曲线图
  - `plot_drawdown`: 回撤分析图
  - `plot_benchmark_comparison`: 基准对比图
  - `plot_signal_overlay`: 信号叠加图
  - `plot_annual_returns`: 年度收益图
  - `plot_position_exposure`: 持仓暴露图
  - `plot_sector_exposure`: 行业暴露图
- **报告生成器**: `visualization/report.py`
  - `generate_report`: 生成完整 HTML 报告
- **Web 仪表板**: `visualization/dashboard.py`
  - `create_dashboard`: 创建 Streamlit 仪表板

#### 打包与部署 (Phase 9)
- **pip 安装**: 支持 `pip install qtrade[all]`
- **Docker 部署**: 完整的 Docker 化部署方案
- **Web 控制台**: FastAPI + Streamlit 现代化 Web 界面
- **REST API**: 完整的 RESTful API
- **配置文件**: 4 个完整的配置示例
- **项目打包**: `pyproject.toml` 现代化打包
  - 核心依赖: pandas, numpy, backtrader, matplotlib, plotly, loguru
  - 可选依赖:
    - `data`: pytdx, akshare, pyarrow
    - `ml`: scikit-learn, xgboost, lightgbm
    - `dl`: torch
    - `live`: websockets, alpaca-trade-api
    - `web`: fastapi, streamlit
    - `optimization`: optuna
    - `quantstats`: quantstats
    - `dev`: pytest, black, ruff
- **Docker 配置**:
  - `Dockerfile`: 多阶段构建
  - `docker-compose.yml`: 服务编排
  - `.env.example`: 环境变量模板
- **Web 控制台**:
  - `api/main.py`: FastAPI REST API
  - `web/dashboard.py`: Streamlit 仪表板
- **配置示例**:
  - `configs/backtest_example.yaml`: 回测配置
  - `configs/optimization_example.yaml`: 优化配置
  - `configs/live_trading_example.yaml`: 实盘配置
  - `configs/multi_strategy_example.yaml`: 多策略配置
- **文档**:
  - `README.md`: 完整项目文档
  - `QUICKSTART.md`: 快速开始指南

### 📊 架构设计

```
qtrade/
├── data/                   # 数据层
│   ├── fetcher.py         # 数据获取
│   ├── cache.py           # 数据缓存
│   ├── sources/           # 数据源适配器
│   ├── source.py          # DataSource 抽象层
│   ├── storage.py         # Storage 抽象层
│   ├── registry.py        # 数据源注册中心
│   └── storages.py        # 存储实现（CSV、Parquet）
├── features/              # 特征工程
│   ├── indicators.py      # 技术指标
│   ├── engine.py          # 特征计算引擎
│   ├── technical.py       # 技术指标实现
│   ├── momentum.py        # 动量指标实现
│   ├── volatility.py      # 波动率指标实现
│   ├── target.py          # 目标变量生成
│   └── library/           # 特征库管理
│       ├── registry.py    # 特征注册中心
│       ├── store.py       # 特征存储
│       └── version.py     # 特征版本管理
├── strategies/            # 策略层
│   ├── base.py            # 策略基类
│   ├── registry.py        # 策略注册中心
│   ├── interface.py       # 策略接口
│   ├── rule_base.py       # 规则策略基类
│   ├── ml_base.py         # ML 策略基类
│   ├── rule/              # 规则策略
│   │   └── dual_ma.py     # 双均线策略
│   └── ml/                # ML 策略
│       └── ml_signal.py   # ML 信号策略
├── backtest/              # 回测引擎
│   ├── engine.py          # 回测核心
│   ├── broker.py          # 券商模拟
│   ├── analyzers.py       # 性能分析
│   ├── broker_config.py   # 券商配置
│   ├── data_feed.py       # 数据供给
│   ├── signal_strategy.py # 信号策略
│   ├── trade_log.py       # 交易日志
│   ├── performance.py     # 绩效指标
│   └── report.py          # 报告生成
├── optimization/          # 参数优化
│   ├── grid_search.py     # 网格搜索
│   ├── bayesian.py        # 贝叶斯优化
│   └── walk_forward.py    # 走步式验证
├── portfolio/             # 组合管理
│   ├── combiner.py        # 策略组合
│   ├── portfolio.py       # 组合管理
│   └── ensemble.py        # 信号集成
├── risk_control/          # 风险控制
│   ├── limits.py          # 仓位限制
│   ├── stop_loss.py       # 止损管理
│   ├── circuit_breaker.py # 熔断机制
│   └── middleware.py      # 风控中间件
├── live_trading/          # 实盘交易
│   ├── broker.py          # 券商接口
│   ├── data_feed.py       # 实时行情
│   ├── order_manager.py   # 订单管理
│   ├── position_sync.py   # 持仓同步
│   ├── risk_monitor.py    # 风险监控
│   ├── alerts.py          # 告警系统
│   └── live_trader.py     # 实盘交易器
├── visualization/         # 可视化
│   ├── charts.py          # 图表生成
│   ├── report.py          # 报告生成
│   └── dashboard.py       # Web 仪表板
├── api/                   # REST API
│   ├── __init__.py        # API 模块
│   └── main.py            # FastAPI 服务
├── web/                   # Web 界面
│   ├── __init__.py        # Web 模块
│   └── dashboard.py       # Streamlit 仪表板
├── eda/                   # 数据探索分析
│   ├── __init__.py        # EDA 模块
│   ├── analyzer.py        # EDA 分析器
│   ├── quality.py         # 数据质量分析
│   ├── distribution.py    # 分布分析
│   ├── correlation.py     # 相关性分析
│   ├── stability.py       # 稳定性分析
│   └── report.py          # EDA 报告
├── qlib_integration/      # Qlib 集成
│   ├── __init__.py        # Qlib 模块
│   ├── adapter.py         # Qlib 适配器
│   ├── factors.py         # 因子管理
│   └── expressions.py     # 因子表达式
└── vectorbt_integration/  # vectorbt 集成
    ├── __init__.py        # vectorbt 模块
    ├── backtester.py      # 向量化回测器
    ├── experiments.py     # 实验管理
    └── parameter_sweep.py # 参数扫描
```

### 🔧 技术栈

- **编程语言**: Python 3.10+
- **数据处理**: pandas, numpy
- **回测引擎**: backtrader, vectorbt
- **机器学习**: scikit-learn, xgboost, lightgbm, torch
- **可视化**: matplotlib, plotly, seaborn
- **Web 框架**: FastAPI, Streamlit
- **任务调度**: asyncio
- **实时通信**: websockets
- **数据库**: PostgreSQL, Redis (可选)
- **容器化**: Docker, Docker Compose
- **代码质量**: pytest, black, ruff, mypy

### 📦 安装

```bash
# 基础安装
pip install qtrade

# 完整安装
pip install qtrade[all]

# 按需安装
pip install qtrade[data,ml,live,web]
```

### 🚀 快速开始

```python
from qtrade import Config, BacktestEngine, DataFetcher
from qtrade.strategies import DualMAStrategy

# 加载配置
config = Config.from_yaml("configs/backtest_example.yaml")

# 获取数据
fetcher = DataFetcher()
data = fetcher.fetch_history(symbol="600519", start_date="2023-01-01")

# 创建策略
strategy = DualMAStrategy(fast_window=5, slow_window=20)

# 运行回测
engine = BacktestEngine(config)
result = engine.run(strategy, data)

# 查看结果
print(result.metrics)
result.plot()
```

详细使用指南请参阅 [QUICKSTART.md](QUICKSTART.md)

### 📝 配置文件

项目提供了 4 个完整的配置示例：

1. `configs/backtest_example.yaml` - 简单回测配置
2. `configs/optimization_example.yaml` - 参数优化配置
3. `configs/live_trading_example.yaml` - 实盘交易配置
4. `configs/multi_strategy_example.yaml` - 多策略组合配置

### 🐳 Docker 部署

```bash
# 克隆项目
git clone https://github.com/qtrade/qtrade.git
cd qtrade

# 配置环境变量
cp .env.example .env
# 编辑 .env 配置你的 API 密钥

# 启动所有服务
docker-compose up -d

# 访问
# API: http://localhost:8000
# Web 仪表板: http://localhost:8501
```

### 📚 文档

- **快速开始**: [QUICKSTART.md](QUICKSTART.md)
- **完整文档**: https://qtrade.readthedocs.io
- **API 文档**: https://qtrade.readthedocs.io/api
- **示例代码**: https://github.com/qtrade/qtrade/tree/main/examples

### 🤝 贡献

欢迎贡献代码！请遵循以下步骤：

1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

### 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情

### 🙏 致谢

- [Backtrader](https://www.backtrader.com/) - 回测引擎
- [Qlib](https://github.com/microsoft/qlib) - 因子框架
- [VectorBT](https://vectorbt.dev/) - 向量化回测
- [QuantStats](https://github.com/ranaroussi/quantstats) - 绩效分析
- [Optuna](https://optuna.org/) - 超参数优化

### ⚠️ 免责声明

本框架仅供学习和研究使用。实盘交易涉及真实资金风险，请谨慎操作。作者不对使用本框架造成的任何损失负责。

---

**如果这个项目对你有帮助，请给一个 ⭐️ Star 支持！**

[1.0.0]: https://github.com/qtrade/qtrade/releases/tag/v1.0.0
