# QTrade - A股量化交易框架

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Version: 1.0.0](https://img.shields.io/badge/version-1.0.0-green.svg)](https://github.com/qtrade/qtrade)

QTrade 是一个功能完善的 A 股量化交易框架，支持从策略研究、回测验证到实盘交易的完整工作流。框架集成了规则策略与机器学习策略，提供参数优化、多策略组合、风险控制等企业级功能。

## 🌟 核心特性

### 数据管理
- **多数据源支持**: TDX（通达信）、AkShare、CSV 等多种数据源
- **智能数据获取**: 自动故障转移、数据缓存、并行下载
- **数据质量保证**: 自动清洗、异常值检测、数据对齐

### 特征工程
- **丰富的技术指标**: RSI、MACD、布林带、ATR 等 50+ 技术指标
- **特征库管理**: 版本化特征库，支持特征复用和共享
- **因子框架**: 集成 Qlib 因子框架，支持自定义因子

### 策略系统
- **统一策略接口**: 规则策略和 ML 策略使用相同的 `generate_signals()` 接口
- **内置策略**: 双均线、RSI+布林带、动量策略等多种经典策略
- **ML 策略支持**: 集成 XGBoost、LightGBM、PyTorch 等机器学习框架

### 回测引擎
- **参数化回测**: 可配置的手续费、滑点、仓位管理
- **高性能**: 基于 Backtrader，支持向量化回测（vectorbt）
- **详细报告**: 自动生成包含图表的 HTML 回测报告

### 参数优化
- **网格搜索**: 穷举搜索最优参数组合
- **贝叶斯优化**: 基于 Optuna 的智能参数搜索
- **走步式验证**: 时间序列交叉验证，防止过拟合

### 多策略组合
- **策略组合器**: 支持多策略并行运行
- **资金分配**: 灵活的策略权重配置
- **信号集成**: 投票法、加权平均、元学习器等多种集成方式

### 风险控制
- **仓位限制**: 单股票和组合级别的仓位控制
- **止损管理**: 固定止损、追踪止损、ATR 止损
- **熔断机制**: 日损失熔断、回撤熔断
- **风控中间件**: 统一的交易前风控检查

### 实盘交易
- **券商接口**: 支持 Alpaca、掘金等券商 API
- **实时行情**: WebSocket 和轮询两种模式
- **订单管理**: 完整的订单生命周期管理
- **风险监控**: 实时风控和告警系统

### 可视化
- **交互式图表**: 基于 Plotly 的交互式图表
- **一键出图**: 资金曲线、回撤分析、年度收益等
- **QuantStats 集成**: 专业的量化分析报告
- **Web 仪表板**: Streamlit 实时监控界面

## 📦 安装

### 基础安装

```bash
# 使用 pip 安装
pip install qtrade

# 或者从源码安装
git clone https://github.com/qtrade/qtrade.git
cd qtrade
pip install -e .
```

### 完整安装（推荐）

```bash
# 安装所有可选依赖
pip install qtrade[all]

# 或者按需安装
pip install qtrade[data,ml,live,web]  # 数据、机器学习、实盘、Web界面
```

### 可选依赖说明

- `data`: 数据源支持（pytdx, akshare, pyarrow）
- `ml`: 机器学习（scikit-learn, xgboost, lightgbm）
- `dl`: 深度学习（pytorch）
- `live`: 实盘交易（websockets, alpaca-trade-api）
- `web`: Web 界面（fastapi, streamlit）
- `optimization`: 参数优化（optuna）
- `quantstats`: 性能分析（quantstats）
- `dev`: 开发工具（pytest, black, ruff）

## 🚀 快速开始

### 1. 简单回测

```python
from qtrade import Config, BacktestEngine, DataFetcher
from qtrade.strategies import DualMAStrategy

# 加载配置
config = Config.from_yaml("configs/default.yaml")

# 获取数据
fetcher = DataFetcher()
data = fetcher.fetch_history(symbol="000001.SZ", start_date="2023-01-01")

# 创建策略
strategy = DualMAStrategy(fast_window=5, slow_window=20)

# 运行回测
engine = BacktestEngine(config)
result = engine.run(strategy, data)

# 查看结果
print(result.metrics)
result.plot()
```

### 2. 使用 CLI

```bash
# 运行回测
qtrade backtest --config configs/default.yaml --symbol 000001.SZ

# 参数优化
qtrade optimize --config configs/optimization.yaml --strategy dual_ma

# 启动实盘交易
qtrade live --config configs/live_trading.yaml

# 生成报告
qtrade report --backtest-id <id> --output report.html
```

### 3. 启动 Web 界面

```bash
# 启动 API 服务
uvicorn qtrade.api.main:app --host 0.0.0.0 --port 8000

# 启动 Web 仪表板（另一个终端）
streamlit run src/qtrade/web/dashboard.py
```

访问 http://localhost:8501 查看仪表板。

## 🐳 Docker 部署

### 使用 Docker Compose（推荐）

```bash
# 克隆项目
git clone https://github.com/qtrade/qtrade.git
cd qtrade

# 复制环境变量
cp .env.example .env
# 编辑 .env 配置你的 API 密钥等

# 启动所有服务
docker-compose up -d

# 查看服务状态
docker-compose ps

# 查看日志
docker-compose logs -f api
```

服务说明：
- **API 服务**: http://localhost:8000 (FastAPI)
- **Web 仪表板**: http://localhost:8501 (Streamlit)
- **Redis**: localhost:6379
- **PostgreSQL**: localhost:5432

### 单独构建 Docker 镜像

```bash
# 构建镜像
docker build -t qtrade:latest .

# 运行容器
docker run -p 8000:8000 -v $(pwd)/data:/app/data qtrade:latest
```

## 📖 使用指南

### 配置系统

QTrade 使用 YAML 配置文件管理所有参数。配置文件位于 `configs/` 目录：

```yaml
# configs/default.yaml
data:
  source: pytdx
  fallback: akshare
  cache: true
  
backtest:
  initial_capital: 100000
  commission: 0.001
  slippage: 0.001
  
strategy:
  name: dual_ma
  params:
    fast_window: 5
    slow_window: 20
    
risk_control:
  max_position_pct: 0.2
  max_drawdown: 0.1
  stop_loss: 0.05
```

### 自定义策略

```python
from qtrade.strategies import StrategyBase

class MyStrategy(StrategyBase):
    def __init__(self, param1=10, param2=20):
        super().__init__()
        self.param1 = param1
        self.param2 = param2
    
    def generate_signals(self, df):
        """生成交易信号"""
        signals = pd.DataFrame(index=df.index)
        
        # 你的策略逻辑
        signals['signal_action'] = 0  # 0=持有, 1=买入, -1=卖出
        signals['signal_strength'] = 0.0  # 信号强度 0-1
        
        return signals
```

### 机器学习策略

```python
from qtrade.strategies import MLStrategyBase
from xgboost import XGBClassifier

class MyMLStrategy(MLStrategyBase):
    def __init__(self):
        super().__init__()
        self.model = XGBClassifier()
    
    def train(self, df):
        """训练模型"""
        features = self.extract_features(df)
        labels = self.create_labels(df)
        self.model.fit(features, labels)
    
    def generate_signals(self, df):
        """生成预测信号"""
        features = self.extract_features(df)
        predictions = self.model.predict_proba(features)
        
        signals = pd.DataFrame(index=df.index)
        signals['signal_action'] = (predictions[:, 1] > 0.6).astype(int)
        signals['signal_strength'] = predictions[:, 1]
        
        return signals
```

### 参数优化

```python
from qtrade.optimization import BayesianOptimizer

optimizer = BayesianOptimizer(
    strategy_class=DualMAStrategy,
    param_space={
        'fast_window': (5, 20),
        'slow_window': (20, 60),
    },
    metric='sharpe_ratio'
)

best_params = optimizer.optimize(data, n_trials=100)
print(f"最优参数: {best_params}")
```

### 多策略组合

```python
from qtrade.portfolio import StrategyCombiner

combiner = StrategyCombiner()

# 添加策略
combiner.add_strategy(strategy1, weight=0.5)
combiner.add_strategy(strategy2, weight=0.3)
combiner.add_strategy(strategy3, weight=0.2)

# 生成组合信号
combined_signals = combiner.generate_signals(df)
```

## 🏗️ 架构设计

```
qtrade/
├── data/                   # 数据层
│   ├── fetcher.py         # 数据获取
│   ├── cache.py           # 数据缓存
│   └── sources/           # 数据源适配器
├── features/              # 特征工程
│   ├── indicators.py      # 技术指标
│   └── library/           # 特征库管理
├── strategies/            # 策略层
│   ├── base.py            # 策略基类
│   ├── rule_based/        # 规则策略
│   └── ml_based/          # ML策略
├── backtest/              # 回测引擎
│   ├── engine.py          # 回测核心
│   ├── broker.py          # 券商模拟
│   └── analyzers.py       # 性能分析
├── optimization/          # 参数优化
│   ├── grid_search.py     # 网格搜索
│   └── bayesian.py        # 贝叶斯优化
├── portfolio/             # 组合管理
│   ├── combiner.py        # 策略组合
│   └── allocator.py       # 资金分配
├── risk_control/          # 风险控制
│   ├── limits.py          # 仓位限制
│   ├── stop_loss.py       # 止损管理
│   └── middleware.py      # 风控中间件
├── live_trading/          # 实盘交易
│   ├── broker.py          # 券商接口
│   ├── data_feed.py       # 实时行情
│   └── order_manager.py   # 订单管理
├── visualization/         # 可视化
│   ├── charts.py          # 图表生成
│   └── report.py          # 报告生成
├── api/                   # REST API
│   └── main.py            # FastAPI 服务
└── web/                   # Web 界面
    └── dashboard.py       # Streamlit 仪表板
```

## 📊 性能报告示例

运行回测后，QTrade 会自动生成包含以下内容的 HTML 报告：

- **绩效指标**: 总收益率、年化收益率、夏普比率、最大回撤等
- **资金曲线**: 策略资金曲线与基准对比
- **回撤分析**: 回撤深度和持续时间
- **月度收益**: 月度收益热力图
- **交易记录**: 详细的交易记录表格
- **风险分析**: VaR、CVaR、Beta 等风险指标

## 🔧 开发指南

### 设置开发环境

```bash
# 克隆项目
git clone https://github.com/qtrade/qtrade.git
cd qtrade

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装开发依赖
pip install -e ".[dev]"

# 安装 pre-commit hooks
pre-commit install
```

### 运行测试

```bash
# 运行所有测试
pytest

# 运行测试并生成覆盖率报告
pytest --cov=qtrade --cov-report=html

# 运行特定测试
pytest tests/test_strategies/
```

### 代码规范

项目使用以下工具保证代码质量：
- **Black**: 代码格式化
- **Ruff**: 代码检查
- **MyPy**: 类型检查

```bash
# 格式化代码
black src/ tests/

# 检查代码
ruff check src/ tests/

# 类型检查
mypy src/
```

## 📝 更新日志

### v1.0.0 (2024-01-15)
- ✅ 完整的数据管理系统
- ✅ 丰富的特征工程模块
- ✅ 统一的策略接口（规则+ML）
- ✅ 高性能回测引擎
- ✅ 参数优化框架
- ✅ 多策略组合管理
- ✅ 完善的风险控制
- ✅ 实盘交易支持
- ✅ 可视化报告系统
- ✅ REST API 和 Web 界面
- ✅ Docker 部署支持

## 🤝 贡献

欢迎贡献代码！请遵循以下步骤：

1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情

## 🙏 致谢

- [Backtrader](https://www.backtrader.com/) - 回测引擎
- [Qlib](https://github.com/microsoft/qlib) - 因子框架
- [VectorBT](https://vectorbt.dev/) - 向量化回测
- [QuantStats](https://github.com/ranaroussi/quantstats) - 绩效分析

## 📞 联系方式

- 项目地址: https://github.com/qtrade/qtrade
- 问题反馈: https://github.com/qtrade/qtrade/issues
- 文档: https://qtrade.readthedocs.io

## ⚠️ 免责声明

本框架仅供学习和研究使用。实盘交易涉及真实资金风险，请谨慎操作。作者不对使用本框架造成的任何损失负责。

---

**如果这个项目对你有帮助，请给一个 ⭐️ Star 支持！**
