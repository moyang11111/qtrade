# DeepSeek HARNESS Quant 复用与适配规范

本文件是 QTrade 与 `deepseek-harness-quant` 之间的可执行行为边界。目标是保持现有用户功能和公开输出不变，同时让上游底座可以独立更新、替换或缺失。

## 三层所有权边界

### upstream external source

`third_party/deepseek-harness-quant` 是 upstream external source，也是运行时可选依赖。它是独立的外部代码和运行底座：QTrade 不修改其中的文件，不把其目录、日志、数据库、缓存、行情快照或构建产物纳入 QTrade 提交；它不作为 QTrade 源码整体复制或随包携带。安装或部署时由使用者单独准备，并按其 `AGENTS.md` 遵守动态 API、数据来源和回归规则。

### QTrade adapter

QTrade adapter 是 QTrade 的桥接层和服务入口，包括 `qtrade_base_bridge.py` 与 `server.py` 中的桥接调用。它负责路径解析、页面与静态资源挂载、`/api/live/` 和 `/api/proxy/` 前缀、决策入口、进程启动/失败降级，以及缺少底座时不破坏 QTrade 自有服务。

QTrade 用户界面品牌统一；第三方名称、许可证和归属只放在 `THIRD_PARTY_NOTICES.md` 或产品 about 信息中，不伪装成 QTrade 自有品牌，也不暗示第三方背书。

适配层的根目录必须是包含 `deck/` 的 `deepseek-harness-quant` 根目录：

- `QTRADE_BASE_DIR` 仅供 `qtrade_base_bridge.py` 使用，优先于项目内候选路径。
- `QTRADE_DECK_DIR` 仅供 `scripts/daily_update_1830.py` 使用；每日更新的优先级为 CLI `--deck-dir` > `QTRADE_DECK_DIR` > 项目内默认路径。
- `QTRADE_HARNESS_PORT` 配置 HARNESS 代理端口，默认 `3081`。
- `QTRADE_NO_HARNESS` 跳过 QTrade 启动时的 HARNESS 拉起；`QTRADE_NO_AUTOUPDATE` 跳过自动增量更新。
- `QTRADE_DATA_DIR` 只控制本地 CSV 数据目录，不改变底座根目录。

### adapted QTrade-owned code

`factors.py` 等 adapted QTrade-owned code 可以在 QTrade 内部重构，但必须保持公开函数和输出语义。适配不意味着复制外部数据或重新实现第二套决策链。

## 必须保持的行为

### 因子 API

以下公开入口是兼容面：`factor_frame`、`AVAILABLE_FACTORS`、`factor_inventory`、`latest_factors` 和 `composite_score`。当前 `factor_frame` 与 `AVAILABLE_FACTORS` 的 35 个顺序字段为：

```text
std20, downside_vol, reversal20, mom20, o2c, amihud, max_ret20,
skew20, amp20, volume_ratio, limup_ex_5, pullback, ma_alignment,
rsi_revert, macd_hist, roc20, wpr14, cci20, obv_trend, kdj_k,
ma200_up, lowvol_60, mom_120, near_high_250, new_high_250,
consec_limit_up, consec_limit_down, limit_up_flag, limit_down_flag,
kdj_d, kdj_j, vol_contract, near_ma250, ma50_up, rsi6
```

`factor_inventory()` 必须继续区分 `ok`、`need_finance`、`need_cross_section`、`need_lhg` 和 `need_industry`，并提供 `total`、`available`、`need_data` 与 `factors`。`latest_factors()` 对不可用的 NaN 因子值返回 `None`；空输入返回空对象；`composite_score()` 保持滚动、无未来函数的分数和窗口不足时的中性行为。浮点回归只使用明确容差。

### 桥接页面、静态资源和 API

必须保持以下页面映射：`/portal`、`/portal.html`、`/pitch`、`/pitch.html`、`/control`、`/control.html`、`/factors`、`/factors.html`、`/etf`、`/etf.html`。必须保持 `/live_ticker.js` 与 `/nav_common.js` 的底座静态资源挂载，以及 `/api/live/`、`/api/proxy/` 两个前缀。

`server.APIHandler` 对桥接层的公开调用名称为 `base_dir`、`serve_base_file`、`live`、`try_serve`、`decide`、`decide_bg_sync` 和 `QtradeDeckHandler`。QTrade 自有服务入口继续提供 `/api/health`、`/api/symbols`、`/api/factors/list`、`/api/kline/`、`/api/info/`、`/api/indicators/` 和 `/api/factors/`。

页面通过动态 API 获取会变化的数据；不得把股票列表、候选池、评分、状态、策略注册表或行情写死在 HTML/JS/适配测试中。A 股界面语义保持“红涨、绿跌”。

### 失败降级

缺少或不完整的底座时，QTrade 自有静态页和 CSV 服务仍可启动；桥接层应返回未处理或结构化错误，不应伪造有效行情或决策。未启用的 `niuapi` 代理使用稳定的空 JSON/错误响应；其他代理失败必须明确为本地 upstream 不可达。所有失败路径都不能启动真实交易、自动更新或隐式访问外部行情。

## 数据与测试边界

本仓库只提交代码、规范和小型合成测试数据。禁止复用或提交第三方行情 CSV、数据库、日志、缓存、模型、HTML 产物和任何真实业务快照；本 PR 不复制第三方目录。契约测试用合成 OHLCV、临时 `ui_v2/deck` 文件、伪 handler 和 AST 解析验证行为，不依赖第三方底座、不打开真实端口、不连接网络、不交易。

适配测试不快照时间戳、动态端口、实时数据或文件 mtime。上游页面和 API 应保持动态数据通道，变化的配置放在配置/数据文件中；新增配置必须提供可解释的示例和缺省降级。任何涉及回测的改动仍须遵守 T+1 开盘执行、一字板过滤、成本模型和结论分级等项目规则。

## 变更回归要求

修改适配层或 QTrade-owned 因子实现时，先运行本文件对应的契约测试，再运行全量 pytest、现有 Ruff 门禁和服务 CSV 冒烟。检查页面/API 可用性、Python 语法、范围 diff 与第三方目录只读状态；未经过审核不得提交或推送 upstream。
