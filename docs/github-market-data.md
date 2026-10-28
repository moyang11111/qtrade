# QTrade 私有 GitHub 行情快照

QTrade 的已验证门户、因子和决策快照可以封装成一个 Release 附件。源码仓库保持公开，行情 ZIP、账户记录和令牌不要提交到源码仓库。建议单独使用 Private 数据仓库，并在启用发布前确认行情源允许这种存放方式。

## 首次部署

1. 创建私有仓库 `moyang11111/qtrade-market-data`，把 `deploy/github-market-data/collect.yml` 复制为该仓库默认分支的 `.github/workflows/collect.yml`。
2. 在本机 QTrade 已有完整快照且没有更新任务运行时，执行：

   ```powershell
   python scripts/github_market_data.py export --user-data "实际的QTrade数据目录" --bundle "$env:TEMP\qtrade-market.zip"
   ```

   数据目录内应有 `state/portal_refresh` 和 `state/snapshot_pipeline`。导出会重新校验两份快照及其日期绑定。
3. 把 `qtrade-market.zip` 作为私有仓库的首个 Release 附件上传，标签设为 `data-YYYY-MM-DD`，标记为 Latest。此后工作流每个交易日下午 18:55（北京时间）自动运行，也可手动触发。节假日会跳过；采集失败不会发布新的 Release。
4. 本机下载需一个仅对私有数据仓库有 **Contents: read** 权限的 GitHub 细粒度令牌。把仓库名和令牌放入本机环境变量 `QTRADE_DATA_GITHUB_REPO`、`QTRADE_DATA_GITHUB_TOKEN`，不要写入仓库或截图：

   ```powershell
   python scripts/github_market_data.py fetch --user-data "实际的QTrade数据目录"
   ```

   配置好环境变量并重启 QTrade 后，原有“立即更新门户、因子和决策”按钮会优先读取与目标交易日匹配的私有 Release；尚未发布或下载失败时仍走本地采集。也可以关闭 QTrade 后执行上面的命令，完成后重启。下载会核对 Release 的 SHA-256，再按 QTrade 原有的数据库、清单、因子与决策绑定规则验收。较旧或同一天的快照不会覆盖本地数据。

GitHub 的定时运行可能延迟；Release 只有在采集成功后才更新。原有本地更新功能仍可在云端未发布新快照时使用。私有数据仓库自己的 `GITHUB_TOKEN` 只需 `contents: write`，无须把桌面读取令牌放入工作流。首次种子数据不在源码仓库中，空仓库无法自行采集历史快照。
