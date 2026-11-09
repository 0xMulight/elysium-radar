# Elysium Radar v1.0

**给 Grok Bot / Windows 10 准备的 Elysium 主网新币 + ERC-404 Machines 只读监控工具**。这不是只写了一段提示词的 Skill：包含本地 Python 程序、图形面板、JSON-RPC 工厂事件、第三方数据检查、预警和赔率情景模型。

## 1 分钟开始（Windows 10）

1. 电脑安装 [Python 3（Windows 官方下载）](https://www.python.org/downloads/windows/)；安装时勾选 **Add Python to PATH**。已有 Python 可以跳过。不需要 pip，也不需要第三方包。
2. 解压整包到电脑上任意文件夹。
3. **双击 `打开监控面板.bat`**，或者用 `无黑框启动.vbs` 打开桌面窗口。
4. 先点「检查主网」，显示 **99901** 才能继续；然后点「仅运行一次」。
5. 点「开始持续监控」，窗口保持打开就会按默认每 30 秒执行一次（服务端 API 自身可能缓存 15–60 秒）。
6. 希望 Telegram 实时通知：用 @BotFather 创建 bot，获取 `bot_token`，给 bot 发至少一条消息，从 Telegram API 获取 `chat_id` 后在面板输入，点击「保存设置」→「测试 Telegram」。不配置也会把事件存到 `data/events.jsonl`，可在面板看。

### 没有任何 Grok 额度也能运行
本监控由 Windows 本地 Python 执行。**不调用 xAI/Grok/DeepSeek 模型**，不需要任何 AI API Key。关闭窗口就停止本地监控。它不会在云端自动继续运行；要实现全天候监控需要让本地电脑保持运行或将程序部署到自己的服务器。

## 监控什么

| 功能 | 具体能力 | 边界 |
|---|---|---|
| 主网验证 | RPC 必须返回 99901，读取区块高度 | 公共 RPC 可能限流或停机 |
| 工厂头矿 | 用 `eth_getLogs` 监听 **PerpMe 的四个已公开工厂** `TokenLaunched` 事件，附交易哈希 | 不覆盖所有工厂、不保证第一个区块提示到 |
| 新币雷达 | PerpMe `/tokens?sort=new` 监控未知币及价格/交易量/报价币 | 非 PerpMe 币不在这里；API 有缓存 |
| 404 机器 | 核验 MACHINE 合约代码；优先读取 OPSIS 第三方行情，若不可用则尝试 DexScreener 中同链同合约的交易对；提醒涨跌/持仓集中变化 | 不等于机器 NFT 地板价或实际激活收益 |
| 赔率计算 | 输入 **已核实** 的各代剩余台数，按等概率无放回模型计算单抽与多抽概率 | 未核实不显示赔率；分配机制若不同，模型失效 |
| 回本情景 | 真实日奖励池、总激活权重、买入价格齐全才计算 | 静态测算、非承诺收益；不自动抓取未核实分配数据 |
| 通知 | 本地 JSONL 事件、可选 Telegram | 无 Telegram 配置则不会发远程通知 |

> **特别注意**：Elysium 也有另一条链名相同、链 ID **1339**，属于不同项目。该工具会强制检查 Kinetiq Elysium 的 **99901**。配错链立刻停止，不会默默换链。

## 概率与收益参数（高级可选）

点击面板里的「打开配置文件」会打开 `config.json`。找到：

```json
"odds_remaining_counts_verified_by_user": {}
```

只有取得项目真实每代剩余数量和已核实的抽取方式后，才可填写，例如 `{"Gen V Colossus": 375, "其他": 4069}` **仅是展示数据结构的例子，不代表实时剩余数量，也不证明是公平随机**。可在命令行运行：

```text
python elysium_radar.py odds --draws 3
python elysium_radar.py roi
```

回本模型的三个参数：`daily_distributed_hype_verified`、`total_active_weight_verified`、`machine_buy_price_hype_verified`。缺一个就写「无法计算」，不会猜数字。你可以在 `config.json` 中调整买入手续费，全部属于手动输入的情景数据。

## 源码、接口与透明度

- Kinetiq Elysium 网络与 RPC：`https://elysium.kinetiq.xyz/explorer`，`https://rpc.elysium.kinetiq.xyz`（实际启动时 `eth_chainId` 再验证）
- PerpMe（自主公开开发文档、工厂与 ABI）：`https://perpme.fun/elysium/docs/integration`
- OPSIS 第三方索引及端点：`https://elysiumexplore.com/api-docs`
- 机器行情备用：`https://api.dexscreener.com/latest/dex/tokens/<address>`；仅接受 `chainId=elysium/99901` 且交易对基础币地址与 MACHINE 完全一致的数据。无法提供持有人和稀有度信息时显示未知。
- Machines 项目主页：`https://elysiummachines.xyz/`

第三方网络数据可能不完整。所有缺失值保留 `None` /「暂无」，不会当作零。只监听上面写明的工厂，不伪称“全链所有头矿”。机器 NFT 合约地址、机器代际倍率需要额外做链上 ABI 验证，因此本版将其保留在配置中并标为未核实，不会伪装成已经读取的实时收益。

## 与 Grok Bot 的关系

- **无额度时**：直接使用这里的面板即可，监控执行不依赖 Grok。
- **额度恢复后**：可以把 `SKILL.md` 和程序文件交给 Grok Bot，让它参考已有工作流建立私有 Skill 或基于消息触发 Routine。实际导入由 Grok Bot 界面完成。
- 仅导入 `SKILL.md` 不能让 Grok Bot 在云端自动执行本地 Python。需要具备运行环境、数据源和通知权限。

## 故障排查

- **检查主网失败**：常见为 RPC 不通/超时/限流；可在配置 `rpc_urls` 添加经过你自己验证的 RPC，不能添加 1339 链 RPC。
- **工厂事件读取失败**：某些 RPC 不提供 `eth_getLogs`。工具仍会使用 PerpMe API，但这会失去直接监听工厂事件的能力。
- **机器行情无法解析**：第三方提供商更改了 JSON 字段，程序会安全停止机器异常计算；可以更新适配器，绝不硬填价格。
- **初次没有新币提醒**：正常。第一次建立基线，防止把所有历史币当新发。
- **机器报“合约无代码”**：RPC 同步不完整、地址与目标链不符或未部署。切勿借此认定假币安全或危险。
- **Telegram 不响**：先打开 bot 私聊并发送消息，再核对 Token / Chat ID，使用「测试 Telegram」。

**验证范围**：已通过 8 项本地单元测试与 Python 语法检查。当前打包环境无法访问公网，因此**没有完成在线连通性验证**；第一次运行需点击「检查主网」「仅运行一次」，界面会清楚显示失败项。

**安全说明**：只允许读取 `eth_chainId`/`eth_blockNumber`/`eth_getCode`/`eth_getLogs`。源码中没有私钥、交易发送、交易签名、合约授权流程。配置中的 Telegram Token 请只在自己电脑存放，不要将 config.json 或 data 上传到公开 GitHub。
