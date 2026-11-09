---
name: elysium-radar
version: 1.0.0
description: >-
  只读监控 Kinetiq Elysium(99901) 的新币工厂启动事件、PerpMe 新币和 Elysium Machines ERC-404
  机器行情；在证据足够时分析稀有度概率与收益回本。用户提到 Elysium 新币/新池/头矿、$MACHINE、404
  Machines、稀有度或回本天数时使用。
---
# Elysium Radar — 主网头矿与 404 Machines 数据 Skill

## 什么时候调用
用户要求检查 Elysium 主网新币/新池/头矿、`$MACHINE` 价格走势、404 机器稀有度概率、机器收益或回本天数时使用。不要把普通的 `Elysium` 同名链（1339）与 Kinetiq Elysium（99901）混为一谈。

## 本地程序（优先，省额度）
本 Skill 目录复用原包文件，不重写：
- `elysium_radar.py`：stdlib-only 只读监控（无第三方 pip）
- `gui.py`：Windows 桌面面板（本机 Linux 无 tkinter 时跳过 GUI）
- `config.example.json` → 首次复制为 `config.json`
- `tests/test_radar.py`：8 项单元测试
- `README.md` / `先看这里.txt`：用户说明

工作目录固定为本 Skill 根目录。命令：
```bash
python3 elysium_radar.py check      # 核验 chainId=99901
python3 elysium_radar.py once       # 单轮扫描
python3 elysium_radar.py run        # 本地持续轮询（poll_seconds≥15）
python3 elysium_radar.py odds --draws 3
python3 elysium_radar.py roi
python3 elysium_radar.py status
python3 -m unittest tests.test_radar -v
```

## 可用输入与读取数据
- 主网 RPC：`eth_chainId`、`eth_blockNumber`、`eth_getLogs`、`eth_getCode`，链 ID 必须为 99901。拒绝任何链 ID 不符合要求的响应。
- PerpMe 官方开发集成文档：`https://perpme.fun/elysium/docs/integration`；公开 API `/tokens?sort=new&limit=50`；直接监控其合约 `TokenLaunched` 事件。
- OPSIS / ElysiumExplore 的只读索引：`https://elysiumexplore.com/api-docs`，机器合约查询 `/v1/tokens/0xf67E8dA4251EbA24f9679fd3F9e39405BAfdC10C`。
- 依赖仅 Python 3 标准库。

## 执行顺序
1. 验证 `eth_chainId == 99901`，RPC 不可用时标记失败；不能改用别的 Elysium 同名链。
2. 通过已核验的 PerpMe 四个工厂合约及 `TokenLaunched` 主题扫描新增事件。区块检查点存储在 `data/state.json`，避免重复报警。不是全链、全发币平台覆盖。
3. 查看 PerpMe 新币索引的新增代币。标出 quote token/venue/24h 量；未知数据标注「暂无」。把缓存延迟写清楚。
4. 检查 MACHINE 合约是否存在代码，优先查询 OPSIS 行情；失败时仅采用 DexScreener 中链 ID 和币地址完全匹配的交易对数据，缺少的持有人/集中度必须标为未知。查询价格、交易量、持有人与 Top10 集中度。在上次观察与本次观察差值达到阈值时提醒。
5. 稀有度计算：只有获得可审计的各代 **剩余数量** 并证实机制为「等概率无放回」后才能使用超几何分布计算。收益倍率（1.0x / 1.25x / 1.75x / 3.0x）不等于抽中概率。
6. 收益分析：仅在拿到真实的「日实际分配 HYPE、所有已激活机器总收益权重、具体机器买入成本」后，做静态情景计算。切勿将 3% 的理论手续费 × 24h 成交量直接当成机器实际收益，更不能给出保证回本结论。
7. 输出要短，有消息时间、合约地址、来源和风险。区分事实、情景计算与未核实数据。

## 定时与提醒
- **优先本地**：用户 Windows 用 `打开监控面板.bat` / `python elysium_radar.py run`；本 Bot 电脑可后台跑 `run`，但机器休眠/重启会断。
- **Grok Routine**：可用 `@every 5m` 或更粗间隔触发 `once`，有新事件才通知用户；Telegram 需用户在 `config.json` 填 token/chat_id。
- 无 Telegram 时，事件仍写入 `data/events.jsonl`，可在聊天摘要。

## 输出模板
```
🚨 Elysium Radar｜2026-11-09 14:00 CST
网络：Kinetiq Elysium / 99901 ✅
事件：PerpMe 新池｜项目名/币名，0x...｜区块 #...（若查到）
机器：价格 X HYPE ｜24h量 Y ｜持有人 Z（未知就写暂无）
概率：当前缺少各代真实剩余数量，暂不计算
注意：索引延迟、滑点、合约风险、价格波动；仅预警不交易
来源：具体交易浏览器 / 官方 API
```

## 安全边界
- 所有操作只读，不要求私钥、助记词、签名或授权。不交易、不 Mint、不代签。
- 碰到 RPC 链 ID 不匹配、数据结构异常、合约无代码、索引延迟时停止或降级，不能输出看似精准的假数据。
- 首次运行建立基线，不把历史代币当成新币报警；已记录事件不重复发送。
- 机器 NFT 地址与倍率来自公开资料，尚需在正式参与前由用户再次核验；本地配置均可更改。
- `config.json`、Telegram token、`data/` 不得提交公开 GitHub。
- 仅上传本项目压缩包并不表示后台 Routine 已经启动；需要时单独创建 Routine。
