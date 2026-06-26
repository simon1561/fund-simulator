# 给 WorkBuddy 的配置 Prompt

把下面这段发给 WorkBuddy（运行在你 Mac 上的本地技术助手），并补上你自己那份 Base 的链接。WorkBuddy 会从 GitHub 把项目部署到你电脑上合适的位置并完成配置。

```text
你是我的本地技术助手，运行在我的 Mac 上。请直接动手帮我从 GitHub 部署并配置这个「飞书股票模拟账户」项目，不要只给我说明文档。

仓库地址：https://github.com/simon1561/fund-simulator
我的飞书 Base 链接是：（在这里粘贴我自己复制的那份 Base 的链接）

请按下面步骤操作：
1. 先读这个仓库的 README.md，按里面的「部署到本机」一节执行。
2. 检查环境：确认有 Python 3 和 Node.js + npm；缺哪个就用 Homebrew 装哪个（必要时先帮我装 Homebrew）。
3. 把仓库克隆到一个长期稳定的位置（推荐 ~/Documents/fund-simulator；不要放在「下载」或临时目录，整个文件夹保持完整）。如果已经存在就更新到最新。
4. 在项目目录运行 setup.command，安装 Python 虚拟环境、yfinance 和 lark-cli。
5. 如果 lark-cli 需要初始化或飞书授权，请把授权链接 / 二维码清楚展示给我，等我完成后再继续。
6. 运行 configure_account.command，把我上面给的 Base 链接配置进去（写入项目里的 .env）。
7. 跑一次 dry-run 或重算，确认能读取我的 Base、识别表结构、实时取价、并写回结果。
8. 如果失败，请根据报错自己继续修复，不要让我自己排查；行情报 429 / 限流时，按 README「实时行情」一节的稳定取价建议处理（带 User-Agent、退避重试、必要时换稳定网络节点）。
9. 最后告诉我：以后只要双击 recalc_account.command 就能重算，结果会在飞书的「收益统计」页面更新。

注意：
- 不要删除或改动我飞书 Base 里的历史数据。
- 不要把我的 Base token、.env 内容或飞书授权信息发到这台电脑之外。
- 删除、覆盖、重置这类高风险操作，必须先问我。
```
