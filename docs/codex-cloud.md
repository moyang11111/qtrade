# QTrade 的 Codex 云端环境与使用方法

本文和仓库脚本准备了项目安装方式；云端环境仍需在你的 ChatGPT/Codex 账号中创建并发布。

## 建议设置

| 项目 | 设置 |
| --- | --- |
| 名称 | `qtrade` |
| GitHub 仓库 | `moyang11111/qtrade` |
| 开始分支 | `main` |
| Python | `3.12` |
| Node.js | `20`（GitHub 验收使用 `20.19.4`） |
| 安装命令 | `bash scripts/codex_cloud_setup.sh` |
| 联网 | 开发环境使用 `Package managers`；无需为普通代码任务开放行情源 |
| 可用范围 | `Only me`（仅自己） |
| 密钥 | 当前离线开发与测试不需要填写行情、券商或 OpenAI API 密钥 |

环境变量填在环境设置中：

```text
TZ=Asia/Shanghai
MPLBACKEND=Agg
QTRADE_NO_HARNESS=1
QTRADE_NO_AUTOUPDATE=1
QTRADE_ELECTRON_CSV_ONLY=1
```

安装脚本从官方 PyPI/npm 下载依赖，安装现有的 `test,data,ml` 依赖组，并运行离线检查。
这会包含 LightGBM、scikit-learn、XGBoost 和 Arrow；不会自动下载全市场行情或启动模拟盘。

## 新版云端入口

1. 在 ChatGPT 网页或桌面端打开 **设置 → Codex Cloud → Environments → Create environment**；也可以在新任务里选择 **Work in → Cloud → Select environment → Create environment**。
2. 选择 `moyang11111/qtrade`。如果需要连接 GitHub，只选择这个仓库所需的访问范围。
3. 选择 **Get started**，然后在环境配置对话中发送：

   > 为 qtrade 配置 Python 3.12 和 Node.js 20。使用 main 分支，在仓库根目录运行 bash scripts/codex_cloud_setup.sh。按照 docs/codex-cloud.md 设置环境变量，联网使用 Package managers，可用范围 Only me。先验证依赖、CSV 服务、下一交易日概率、模拟盘和 Electron 单元测试。启动检查使用离线 CSV 测试，不启动自动交易或全市场下载。完成后报告通过、失败和跳过的检查。

4. 查看安装和测试报告。安装脚本与启动检查设置中应保留已验证的命令；如需重验，执行 `bash scripts/codex_cloud_check.sh smoke`。
5. 保存设置，选择 **Publish**；只有看到 **Environment published** 后，这个环境才可用于新任务。

新版环境会保存准备好的文件和依赖。以后依赖要求发生变化，可在环境的菜单中选择 **Edit**，完成安装与验收后 **Republish**；现有任务保持自己的状态。

## 如果你看到旧版设置页

旧版可能显示 `Setup script`、`Maintenance script` 和 `Set package versions`：

- 在版本设置中选择 Python 3.12、Node 20。
- `Setup script` 填写 `bash scripts/codex_cloud_setup.sh`。
- `Maintenance script` 可填写同一命令，让缓存容器在依赖变化后重新安装并检查。
- 环境变量填在设置中，不能只在安装脚本里临时 `export`。
- 普通代码任务的 agent 联网可关闭；安装阶段可以联网下载依赖。

## 日常怎样使用

1. 新建任务，选择 **Cloud** 和已经发布的 `qtrade` 环境。
2. 用中文描述目标、界面要求和验收标准。例如：

   > 检查 QTrade 模拟盘手动买卖和人工确认流程，修复发现的问题。保留现有界面和账户历史，使用隔离测试数据。完成相关测试后创建 codex/ 分支并提交 PR，报告修改和验证结果，等待我审阅后合并。

3. 在任务中查看进度、文件修改和测试结果；需要调整时直接在同一个任务里补充要求。
4. 查看 PR 和 GitHub 自动检查，通过后再合并。
5. 合并到 `main` 后，在本机同步并更新安装的 QTrade，才能看到桌面软件的新效果。可以在本地对话里要求：“同步云端合并后的版本，保留账户与数据，验收后更新桌面软件。”

### 两种检查

```bash
# 安装验收与日常快速检查
bash scripts/codex_cloud_check.sh smoke

# 改动较大时：完整测试和 Python 打包
bash scripts/codex_cloud_check.sh full
```

## 云端与本机的区别

- 云端任务可以在电脑休眠时继续运行。
- 云端默认拿到 GitHub 源码；你电脑中的行情缓存、研究快照、模拟盘资金和持仓不会自动上传。
- 实际获取 A 股数据需要额外配置对应行情源的网络访问，并重新获取或提供指定数据；离线测试通过不代表今天的行情已更新。
- Windows 桌面窗口、快捷方式和安装包需要本机或 Windows CI 验收。仓库现有 `electron-quality-gates` 负责 Windows 打包检查。
- 普通开发建议先在云端改代码和提 PR，再在本机验收并安装。

## 遇到安装问题

- LightGBM 提示找不到 `libgomp.so.1`：在云端 Ubuntu 安装 `libgomp1` 后重试；不要因此跳过模型测试。
- Electron 下载失败：检查 Package managers 联网策略是否允许官方 npm 和 GitHub release 下载。
- 环境没有新依赖：新版编辑环境后重新发布并创建新任务；旧版可重跑安装脚本或重置缓存。
- 不要把本机账户数据库、密钥或大型 `work/` 目录提交到 GitHub。

## 官方说明

- [Codex Cloud 使用入口](https://learn.chatgpt.com/docs/cloud)
- [新版云端环境配置](https://learn.chatgpt.com/docs/environments/cloud-environments)
- [旧版环境配置](https://learn.chatgpt.com/docs/environments/cloud-environment)
