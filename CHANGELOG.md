# 更新日志

所有重要更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/),
并且本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [未发布]

### 🌐 交易池扩至整个主板（3,581 只）

- **融合/欧奈尔策略全主板扫描**（沪 60/深 00，全部本地 CSV），RPS 相对强度与
  市场宽度均改为全主板口径（此前 60/300 只样本）；其余信号源维持 60 只池
- **两级漏斗架构**：第一级 numpy 快筛（RPS+52周新高+MA50+枢轴+四形态当天命中，
  3,581 只 → 当日候选通常 <50 只，与完整引擎逐只比对验证零漏报/零误报）；
  第二级仅对幸存者跑完整引擎精析
- **性能**：修复 `_load_csv` 缓存只写不读的问题后，首轮 19s（一次性加载全主板），
  之后每轮 **~0.5s**（60s 间隔的后台线程几乎无感）；弱市宽度不达标时直接短路买入扫描
- 界面（三端）资产栏新增「股票池 N 只」显示

### 🎯 红杉×欧奈尔融合策略（主力专用，新默认信号源）

- **设计**：`SequoiaOneilEngine`（`sequoia_oneil`）——欧奈尔管纪律，Sequoia-X 管扳机：
  ① 欧奈尔闸门：市场宽度≥40%（M）+ RPS≥85（L，RS 扩大到 300 只本地样本计算）
  + 距52周新高≤15%（N）+ 站上走平/上升 MA50；② Sequoia-X 四形态扳机；
  ③ 枢轴边缘入场：收盘距近10日高点 ≤3%（不追高）；④ 卖出铁律：止损7.5% /
  止盈20% / 八周持股规则；⑤ 连续两日跌破MA20 信号卖出
- **迭代过程（20股×250日弱市样本）**：初版 −2.5%（Sequoia 扳机入场质量不足）→
  加枢轴边缘规则后 **+0.6% / 回撤−6.1% / 胜率25% / 前后半段均正**；
  出场侧（50日线跟随/时间止损）对该策略无增益，未采用
- **设为默认**：新账户与 API 默认信号源均改为 `sequoia_oneil`；
  其余 8 个信号源保留在下拉框可随时切换

### ✨ 自动模拟盘（新版模拟交易系统）

- **独立信号引擎 `LearnedSignalEngine`**：不再依赖内置策略库，自研四类因子打分——
  趋势（收盘>MA20 且 MA20 走升）、中期多头（>MA60）、短动量回升（MA5 向上）、
  位置安全（距 MA10<2.5% 不追高），确认项 MACD/放量二选一，RSI<72 拒绝超买追入；
  卖出信号：跌破 MA20 趋势破位 / 跌破 MA10 且 MACD 死叉 / RSI>80 极端超买。
- **自动交易 `AutoPaperTrader`**：服务启动即开后台线程，每 60 秒自动扫描股票池
  买卖（单只 20% 仓位、最多 8 只、整手交易、双边费率 0.15%），持仓自动止盈
  （+12%，即预期卖出价）/止损（-6%），状态持久化 `auto_paper_state.json`。
- **新接口 `GET /api/auto/paper?action=status|run|toggle|reset`**。
- **前端新增「⚙️ 自动模拟盘」标签页**：总资产/现金/持仓市值/收益/仓位比例、
  持仓明细（买入价、现价、**预期卖出价（止盈目标）**、止损价、盈亏%、市值）、
  交易记录（含买卖理由），15 秒自动刷新，支持暂停/立即运行/清仓重置。
- **桌面端（PySide6）同步接入**：底部新增「⚙ 自动模拟盘」标签页，持仓表格
  显示买入价/现价/预期卖出价/止损价/盈亏%（红涨绿跌），后台线程自动交易，
  支持暂停/立即跑一轮/清仓重置；与网页端共用账户状态文件。
- **Tk 桌面端（desktop_tk.py）同步接入**：底部新增「⚙ 自动模拟盘」标签页，
  含资产摘要、持仓明细（买入价/预期卖出价/止损价/盈亏%）、交易记录、
  信号源切换与暂停/跑一轮/重置按钮，后台线程自动交易。

### ✨ GitHub 高星经典策略（4 个）

- **turtle 海龟交易法**：20 日 Donchian 突破入场、10 日低点 + 2×ATR 移动止损离场，
  参考 vnpy（44k★）官方示例 `turtle_signal_strategy.py`
- **supertrend 超级趋势**：SuperTrend(10, 3) 趋势线翻多/翻空，参考 pandas_ta 实现
- **dual_thrust 区间突破**：经典 HH/HC/LC/LL Range 公式（N=4, K1=0.4, K2=0.6）
  日线适配版，参考 fmzquant/strategies 与 vnpy 官方示例
- **boll_reversion 布林均值回归**：BB(20, 2σ) 下轨超卖买入、中轨回归卖出，MA60 趋势过滤
- 接入位置：策略框架注册（`src/qtrade/strategy/rule/`，可用于控制台模拟盘）、
  三处回测下拉框（网页 / PySide6 / Tk）、**自动模拟盘信号源切换**
  （`/api/auto/paper?action=setmode`，三端均有下拉框：自研学习引擎 / 四大策略）

### 📚 Sequoia-X 与欧奈尔 CANSLIM（GitHub 调研学习成果）

- **调研确认**：sngyai/Sequoia-X（原 sngyia/sequoia，**5,340★**，A股自动选股系统）；
  CANSLIM 在 GitHub 有 151 个仓库（xang1234/stock-screener 278★ 等）
- **新信号源 `sequoia`**：移植 Sequoia-X V2 四类量价入场——海龟突破（阳线防假突破+
  成交额≥1亿）、MA5/MA20 金叉放量1.5倍、高而窄旗形（40日涨60%后10日收紧缩量）、
  涨停洗盘（昨涨停今阴回踩不破）；卖出=连续两日跌破MA20
- **新信号源 `oneil` 欧奈尔 CANSLIM 精简版**（量价可计算子集，C/A/I 基本面要素除外）：
  N=距52周新高≤15%+基底回调12-30%+枢轴突破放量1.3倍且不追高；L=120日收益市场
  百分位RPS≥85；M=市场宽度≥40%才开仓；卖出铁律=止损7.5%/止盈20%/八周持股规则
  （15日内涨满20%→40日不落袋）
- **v2 反哺增强（O'Neil M 要素融合）**：v2 新增市场宽度闸门（宽度<35%不开新仓），
  回测收益 +3.5%→+5.0%，回撤 −7.2%→−5.8%，前后半段均为正收益
- **20股×250日组合回测结论**：v2+闸门(+5.0%) > v1(−6.6%) ；欧奈尔仅出手1次亏1.5%
  （弱市空仓观望，忠实还原 M 要素）；Sequoia-X 追强策略在震荡市 −19%（宜牛市使用）

### 🧠 自研引擎 v2（多策略融合，GitHub 九大概念学习成果）

- **信号层融合**（`LearnedSignalEngineV2`，新账户默认信号源 `learned_v2`，v1 保留可切换）：
  ADX(14) 状态分层（Wilder）自动区分趋势/震荡市 · TTM Squeeze 挤压释放点火
  （John Carter/pandas_ta）· z 分数(20) 超卖低吸（Ernest Chan/Quantopian）·
  OBV>OBV-MA21 量能吸筹确认（Granville）· 5日涨幅>8% 防追高护栏 + MA60 趋势锚
  （NostalgiaForInfinity 3.4k★ 设计思想）
- **交易层升级**：候选股按融合评分排名，每轮只买最优 2 只（二八轮动思想）·
  钱德利拖曳止损（峰值收盘−3×ATR22，只升不降，Chuck LeBeau）·
  时间止损（持有12日未盈利离场，Van Tharp）· 时间衰减止盈（15日后浮盈≥4%落袋，NFI）
- **20股×250日组合回测**（与 v1 同规则对比）：交易 99→39 笔（更挑剔），
  胜率 29%→31%，最大回撤 **−16%→−7%**，前后半段收益 +19.1%/−11.3%（v1 大起大落）
  → +6.0%/−3.3%（v2 稳定），风险收益比 0.32→0.37

### ⚙️ 后台交易服务（关窗口继续自动交易）

- **静默启动脚本** `QTrade后台交易_静默启动.vbs`：后台拉起交易服务（pythonw 无窗口），
  关闭 QTrade 窗口后自动交易继续；配套 启动(控制台调试)/停止/安装开机自启/卸载自启 脚本
- **`server.py --single-instance`**：后台服务模式端口被占直接退出，防止重复进程
- **跨进程引擎锁 `EngineLock`**（OS 文件锁）：后台服务与桌面窗口同时打开时，
  只有一个进程真正执行交易，其余只读状态；UI 显示「由后台交易服务驱动」；
  持锁进程退出后其他进程自动接管
- **状态热加载 + 原子写入**：每轮交易前从磁盘重载状态，窗口端的重置/暂停/切信号源
  对后台服务下一轮（≤60 秒）即时生效；JSON 原子替换保证多进程读取安全

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
strategy = get_signal_generator("dual_ma")({"name": "dual_ma", "fast_period": 5, "slow_period": 20})

# 运行回测
engine = BacktestEngine(config)
result = engine.run(strategy.generate_signals(data))

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
