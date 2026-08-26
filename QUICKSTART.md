# QTrade 快速开始指南

本指南帮助你在 5 分钟内开始使用 QTrade 量化交易框架。

## 安装

### 方式 1: 从源码安装（推荐）

```bash
git clone https://github.com/moyang11111/qtrade.git
cd qtrade
pip install -e .          # 基础安装

# 测试、Ruff、构建和确定性服务冒烟
pip install -e ".[test]"

# 或安装完整依赖
pip install -e ".[all]"
```

基础安装已包含桌面门户和应用内交易日调度所需的 `akshare>=1.10.0`；不需要依赖
`.[data]` 才能启动桌面端。

也可以用 requirements.txt：

```bash
pip install -r requirements.txt
```

### 质量门禁

在安装 `.[test]` 后，可复现地运行项目质量门禁：

```bash
python -m pytest -q
ruff check .
ruff check tests/test_service_smoke.py tests/test_quality_gates.py --select E4,E7,E9,F
python -m build --wheel --sdist
python -m pytest -q tests/test_service_smoke.py
```

服务冒烟使用临时目录和最小本地 CSV，启动 `server.py --csv-only --no-browser --single-instance`，
检查静态页、健康检查、股票列表和 K 线接口后清理子进程；不会连接实时行情，也不会交易。

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

# 确定性离线模式：只读本地 CSV，不打开浏览器
python server.py --data-dir data/cache --csv-only --no-browser --single-instance

# 或双击 run.bat
```

打开 http://127.0.0.1:8765

#### Electron 桌面壳

Electron 开发环境需要 Node.js 18+、npm，以及 Python 3.10+ 和项目 Python 依赖：

```bash
python -m pip install -e .
npm --prefix electron ci
```

在项目根目录双击 `qtrade_electron.bat`，或运行：

```bash
npm --prefix electron run start
```

应用会使用本机回环动态端口，健康检查确认后再显示窗口。Python 解释器按
`QTRADE_PYTHON`（单个可执行文件路径）优先发现；Windows 回退到 `py -3`、`python`，
其他平台回退到 `python3`、`python`。`QTRADE_DATA_DIR` 可指定 CSV 缓存目录，
`QTRADE_ELECTRON_CSV_ONLY=1` 可强制离线 CSV 模式。
窗口创建前会执行一次不联网预检，确认 Python >=3.10 且 `pandas`、`akshare` 可导入。
如果 `QTRADE_PYTHON` 已显式设置但缺少模块，错误会列出缺失模块和当前解释器路径，
不会静默改用其他解释器；请使用该解释器执行 `python -m pip install -e .` 后重试。

运行 Electron 自测、语法检查和 Windows 打包：

```bash
npm --prefix electron test
npm --prefix electron run lint
npm --prefix electron run dist:dir   # electron/dist/win-unpacked/
npm --prefix electron run dist:win   # electron/dist/QTrade Setup *.exe
```

打包应用携带 Python 后端、静态资源、`paper_trading/` 和本地因子模块，但不携带
Python 解释器或依赖；运行时仍需系统 Python 3.10+ 和项目依赖，必要时设置
`QTRADE_PYTHON`。`third_party/deepseek-harness-quant` 是独立底座，不随打包提供，
通过 `QTRADE_BASE_DIR` 指向包含 `deck/` 的底座根目录。

### 4. 启动 API 服务

```bash
uvicorn qtrade.api.main:app --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000/docs 查看接口文档。

### 5. 配置可选底座

`third_party/deepseek-harness-quant` 是需要单独准备的外部底座，仓库不会提交该目录。
两个配置变量作用不同：`QTRADE_BASE_DIR` 仅供 `qtrade_base_bridge.py` 使用，指向包含
`deck/` 子目录的 deepseek-harness-quant 根目录；`QTRADE_DECK_DIR` 仅供 `scripts/daily_update_1830.py` 使用，指向每日更新底座目录。

桥接底座配置（`QTRADE_BASE_DIR`）：

```bash
# Linux/macOS
export QTRADE_BASE_DIR=/path/to/deepseek-harness-quant

# Windows PowerShell
$env:QTRADE_BASE_DIR = "D:\path\to\deepseek-harness-quant"
```

每日更新底座配置（`QTRADE_DECK_DIR`）：

```bash
# Linux/macOS
export QTRADE_DECK_DIR=/path/to/deepseek-harness-quant
python scripts/daily_update_1830.py --deck-dir /path/to/deepseek-harness-quant --dry-run

# Windows PowerShell
$env:QTRADE_DECK_DIR = "D:\path\to\deepseek-harness-quant"
python scripts/daily_update_1830.py --deck-dir "D:\path\to\deepseek-harness-quant" --dry-run
```

每日更新路径优先级为 CLI `--deck-dir` > `QTRADE_DECK_DIR` > 项目内默认 `third_party/` 路径。
没有底座时，CSV 服务、测试和本地质量门禁仍可运行，但底座桥接接口不可用。

应用打开期间，QTrade 会在交易日 18:30 调度一次数据更新；18:30 后首次打开会立即补跑。关闭应用不会等待调度，成功、休市或失败当天都不会重复忙循环，下一自然日再检查。调度依赖可选的 `akshare` 交易日历，并将缓存写入 `logs/cache/trading_calendar.json`；日历无法确认且没有缓存时会安全停止，不会猜测执行。设置 `QTRADE_NO_AUTOUPDATE=1` 可完全关闭调度。

需要人工补跑可执行 `python scripts/daily_update_1830.py --force`，目标日期可用 `--date YYYY-MM-DD` 指定；结构化状态默认写入 `logs/daily_update_1830.status.json`，运行日志写入 `logs/daily_update_1830.log`，可用 `--status-file` 覆盖状态路径。每日流程只更新底层数据，不自动交易或启动 HARNESS。

QTrade 打开期间，界面会低频读取只读接口 `/api/update/status`。检测到新的成功状态后，当前门户、决策或因子仪表页面刷新一次；行情页不切页、不整页重载，之后切换到相关页面会自动读取新数据。相同成功状态只处理一次，更新中、失败、休市以及缺失/损坏状态文件都会安全等待下一轮。

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
