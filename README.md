# Fund Simulator / 股票模拟账户 MVP

一个「飞书多维表格 + 本地 Python 脚本」搭的个人股票模拟盘记账系统：在飞书表格里录交易，本地脚本实时取价、按 TWR 口径重算持仓与收益，再写回表格展示。**仅支持 macOS。**

> 每个使用者用自己的飞书 Base（从同一个模板各自复制一份），各自配置自己的 Base 链接，互不影响。代码不含任何账户数据，数据都在各自的飞书 Base 里。

## 部署到本机（首次）

不懂代码也没关系：把本节连同仓库地址发给 WorkBuddy（或任意本地技术助手），让它照着做即可。

**环境要求**：macOS、Python 3、Node.js + npm（用来装飞书 `lark-cli`）、一个飞书账号。缺 Python 或 Node 时先用 Homebrew 安装（`brew install python node`）。

**步骤**：

1. 选一个**长期稳定**的位置克隆仓库——不要放在「下载」或临时目录；整个文件夹要保持完整，不要只取其中几个文件。推荐 `~/Documents/fund-simulator`：
   ```bash
   git clone https://github.com/simon1561/fund-simulator.git ~/Documents/fund-simulator
   cd ~/Documents/fund-simulator
   ```
   不方便用 git 时，也可以在 GitHub 页面点 `Code → Download ZIP`，解压到上述位置。
2. 从**项目维护者给你的模板 Base** 复制一份到自己的飞书，改成自己的名字（例如 `张三-股票模拟账户`），并确认自己对它有编辑权限。
3. 双击 `setup.command`。它会创建 Python 虚拟环境、装 `yfinance`、装并初始化 `lark-cli`，并引导你完成飞书授权。
4. 双击 `configure_account.command`，粘贴自己那份 Base 的链接。看到「验证通过」即配置完成。
5. 以后每次想看最新数据，双击 `recalc_account.command` 即可。

图文版步骤见 [`USER_GUIDE.md`](USER_GUIDE.md)；交给 WorkBuddy 自动配置的提示词见 [`WORKBUDDY_PROMPT.md`](WORKBUDDY_PROMPT.md)。

## 日常流程

1. 在 `投资标的库` 里新增证券，填写 `股票名称`、`股票代码`、`币种`、`资产类型`、`市场`。`标的` 用于交易流水选择时显示，格式建议为 `股票名称 / 股票代码`。
2. 在 `交易流水` 里追加交易，不覆盖旧记录；录错时新增一条 `冲正`。
3. 打开 `设置` 表里的 `重算设置` 标签页，或运行重算脚本，实时获取行情并刷新账户、持仓、Put 和资产快照。

## 数据保存原则

- 长期保存事实数据：`交易流水`、`投资标的库`、`设置`、手工确认过的交易汇率。
- 不保存实时可获取的原始行情缓存：持仓估值和基准收益在重算时用 yfinance 实时获取。
- 保存展示和绩效结果：`持仓统计`、`现金担保Put`、`资产快照`、`资产构成` 用于多维表格和收益统计页面展示。

## 设置表结构

`设置` 是一张数据表，用四个标签页区分用途：

- `账户设置`：账户初始资金、基准货币、TWR 起始日，以及脚本回写的当前现金、股票市值、冻结现金、可用现金、NAV 和累计 TWR。
- `基准设置`：恒生科技、沪深300、标普500 的行情源代码和起始价格，用于计算组合相对基准的累计收益。
- `重算设置`：本地重算按钮入口。先启动 `start_recalc_server.command`，再打开这里的本地触发地址；页面里可以选择本次重算日期。
- `使用说明`：录入和重算规则备忘。

## 交易填写口径

- `现金影响USD`：正数表示现金增加，负数表示现金减少。
- `外部现金流USD`：只给 `入金` / `出金` 使用；入金正数，出金可以填正数或负数，脚本会按出金处理成负数。
- `标的币种`：自动从 `投资标的库` 的证券记录带出，例如腾讯会显示 `HKD`，不需要手填。
- `USD汇率`：目标货币折算成 USD 的汇率，例如 HKD 标的填 HKD->USD。手填值优先；买入/卖出股票时如果留空，脚本会按 `交易日期` 自动抓当天或最近前一个可用交易日的汇率并回填。USD 标的留空时按 `1`。
- 买入股票：如果 `现金影响USD` 为空，脚本用 `数量 × 成交价 × USD汇率` 自动计算现金流出。
- 卖出股票：如果 `现金影响USD` 为空，脚本用 `数量 × 成交价 × USD汇率` 自动计算现金流入。
- 分红：填写 `现金影响USD`，不填外部现金流。
- 拆股/合股：填写 `拆合股比例`，例如 1 拆 10 填 `10`，10 合 1 填 `0.1`。
- 卖 Put：只支持 100% 现金担保。填写 `合约数`、`行权价USD`、`到期日`、`合约乘数`、`权利金每股USD`。乘数为空时脚本按 `100`。

## 持仓统计口径

- `最新价原币`：按股票交易货币展示的最新价，例如港股是 HKD，A 股是 CNY，美股是 USD。
- `平均成本原币`：按移动加权平均计算的原币成本价；多次买入会按买入数量和成交价加权。
- `最新价USD`、`平均成本USD`、`市值`、`成本`、`盈亏额度`、`盈亏比例` 继续用于 USD 口径的账户净值和收益统计。

## 资产构成口径

- `资产构成` 是脚本生成的结果表，用于收益统计里的 `NAV构成占比`。
- 股票/ETF 和类现金标的按持仓市值展示；类现金标的需要在 `投资标的库.资产类型` 里选 `类现金`。
- 现金会拆成 `可用现金` 和 `现金担保Put冻结现金`。如果没有未到期 Put，冻结现金为 0，不会显示。
- 所有构成项的金额加总应等于当前 NAV。

## 运行命令

首次配置推荐直接双击：

```bash
open setup.command
open configure_account.command
```

`setup.command` 会创建虚拟环境、安装 `yfinance`、检查 `lark-cli` 并引导飞书授权。`configure_account.command` 会让你粘贴自己的 Base 链接，自动写入 `.env` 并验证表结构。

配置文件 `.env` 示例：

```text
BASE_TOKEN=你的 Base token
```

以后日常重算可以双击：

```bash
open recalc_account.command
```

也可以手动运行：

```bash
cd ~/Documents/fund-simulator   # 换成你自己的项目目录
.venv/bin/python stock_sim_account_recalc.py --as-of 2026-06-24
```

想先看会写什么，不真正写入：

```bash
.venv/bin/python stock_sim_account_recalc.py --as-of 2026-06-24 --dry-run
```

处理完成后把交易流水标记成 `已重算`：

```bash
.venv/bin/python stock_sim_account_recalc.py --as-of 2026-06-24 --mark-transactions
```

`recalc_account.command` 会用当天日期重算，并自动把已处理交易标记为 `已重算`。

```bash
open recalc_account.command
```

如果想从多维表格里触发，先在 Finder 里双击项目目录下的 `start_recalc_server.command`，保持弹出的终端窗口打开。然后在 `设置` 表的 `重算设置` 标签页里打开 `链接`，点击页面里的按钮即可实时取价并重新统计。

```bash
open start_recalc_server.command
```

## 实时行情

脚本使用 yfinance 实时获取持仓行情、基准行情和缺失的历史汇率。安装依赖后可以运行：

```bash
.venv/bin/python -m pip install yfinance
.venv/bin/python stock_sim_account_recalc.py --update-prices
```

`--update-prices` 现在只是兼容旧命令的参数；脚本不会再写入 `行情快照`，行情会在重算时直接获取。

自动汇率只会填补空的 `USD汇率`；如果你手工修改过该字段，后续重算会尊重手填值。想重新自动抓一次，可以把这笔交易的 `USD汇率` 清空后再重算。

取价优先用 `yfinance`，失败时回退到 Yahoo Finance chart 接口；回退请求会带浏览器 `User-Agent` 并对 `429/5xx` 做指数退避重试（Yahoo 对无 UA 的请求一律返回 429）。如果重算时报「行情抓取失败，沿用上次价格」，多为 Yahoo 按出口 IP 临时限流：用代理时给 `query1.finance.yahoo.com` 走一个稳定/直连的节点，并避免短时间反复重算即可。
