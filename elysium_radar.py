#!/usr/bin/env python3
"""Elysium Radar — stdlib-only, READ-ONLY chain monitor for Kinetiq Elysium (99901).
No wallet keys, signatures, transactions, or trading in this program.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
TEMPLATE = BASE / "config.example.json"
STATE = BASE / "data" / "state.json"
EVENTS = BASE / "data" / "events.jsonl"
TOKEN_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
ZERO_ADDRESS = "0x" + "0" * 40
TOPIC_V3_LAUNCHED = "0x235e34a4e0e6a401dae6851f6fab4a919a1fdd0ae0073ac2fc4d1d4a87e548e5"
TOPIC_V4_LAUNCHED = "0x78a585b45465534eb3a9d11f931896c7370e9a3bd6f94ab346c7c4b548335766"
# From PerpMe's published Elysium factory & ABI documentation; independent projects aren't covered.
FACTORIES = {
    "Uniswap V3": "0xe35B622b0D37d57BF3E4D6930A057E58e7Ba30bE",
    "Uniswap V4": "0xa338c80bbEC54f3439f6af22d21723226ABc48bd",
    "Ramses V3": "0x0369B8eeB92f1F49a6f446B2B6cb57381484Ab7e",
    "Ramses V3 early": "0x682dC0458815d1f4927D366fA91e645c71024d7B",
}


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def atomic_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def ensure_config():
    if not CONFIG.exists():
        CONFIG.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")


def load_config(path=CONFIG):
    ensure_config()
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if result["network"]["chain_id"] != 99901:
        raise ValueError("安全停止：网络必须是 Kinetiq Elysium，chain ID 99901；不是 1339。")
    for addr in (result["machine"]["token_contract"], result["machine"]["nft_contract"]):
        if not TOKEN_RE.fullmatch(addr):
            raise ValueError("合约地址不是有效的 0x+40 位十六进制格式")
    if int(result["monitor"]["poll_seconds"]) < 15:
        raise ValueError("监控间隔不能小于 15 秒：公开 API 需要避免过快轮询")
    return result


def get_json(url: str, *, params=None, method="GET", body=None, timeout=12):
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method, headers={
        "User-Agent": "ElysiumRadar/1.0 (+read-only; personal research)",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    with urlopen(req, timeout=timeout) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"HTTP {response.status}")
        raw = response.read(3 * 1024 * 1024)
        result = json.loads(raw)
        return result


class RPC:
    def __init__(self, urls, request=get_json):
        self.urls = [u.strip() for u in urls if u.strip()]
        self.url = None
        self.request = request
        self.counter = 0
        if not self.urls:
            raise ValueError("至少配置一个 RPC")

    def call(self, method, params):
        if method not in {"eth_chainId", "eth_blockNumber", "eth_getCode", "eth_getLogs"}:
            raise ValueError("不允许的 JSON-RPC 方法：" + method)
        self.counter += 1
        payload = {"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params}
        errors = []
        candidates = ([self.url] if self.url else []) + [u for u in self.urls if u != self.url]
        for url in candidates:
            try:
                output = self.request(url, method="POST", body=payload)
                if not isinstance(output, dict) or "error" in output or "result" not in output:
                    raise RuntimeError(str(output.get("error", "invalid JSON-RPC response") if isinstance(output, dict) else output))
                if method != "eth_chainId":
                    # Verify chain before trusting a rotated provider.
                    chain = self.request(url, method="POST", body={"jsonrpc": "2.0", "id": -1, "method": "eth_chainId", "params": []})
                    if not isinstance(chain, dict) or int(chain.get("result", "0"), 16) != 99901:
                        raise RuntimeError("RPC 网络 ID 不等于 99901")
                self.url = url
                return output["result"]
            except (URLError, TimeoutError, ValueError, RuntimeError, OSError, HTTPError) as exc:
                errors.append(url + " => " + str(exc)[:150])
        raise RuntimeError("所有 RPC 不可用：" + " / ".join(errors))

    def check(self):
        chain = int(self.call("eth_chainId", []), 16)
        if chain != 99901:
            raise RuntimeError(f"安全停止：RPC 返回 chain ID {chain}，预期 99901。")
        height = int(self.call("eth_blockNumber", []), 16)
        return height


def load_state(path=STATE):
    if Path(path).exists():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return {"last_block": None, "seen_tokens": [], "machine": None, "recent_alert_keys": []}


def valid_address(value):
    return isinstance(value, str) and bool(TOKEN_RE.fullmatch(value))


def numeric(val):
    try:
        if isinstance(val, bool) or val is None or val == "":
            return None
        number = float(val)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def nested(src, *paths):
    for path in paths:
        node = src
        for key in path.split("."):
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if node is not None:
            return node
    return None


def extract_items(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for k in ("items", "tokens", "results", "data"):
        if isinstance(data.get(k), list):
            return data[k]
    return []


def machine_snapshot(payload):
    """Unknown provider fields remain None, NEVER fabricate them as zero."""
    if not isinstance(payload, dict):
        raise ValueError("机器行情数据不是 JSON object")
    src = payload
    for wrapped in ("token", "data"):
        if isinstance(src, dict) and isinstance(src.get(wrapped), dict):
            src = src[wrapped]
    if not isinstance(src, dict):
        raise ValueError("机器行情结构未知")
    result = {
        "price_usd": numeric(nested(src, "market.price_usd", "market.priceUsd", "price_usd", "priceUsd")),
        "price_hype": numeric(nested(src, "market.price_hype", "market.priceHype", "price_hype", "priceHype")),
        "volume_24h_usd": numeric(nested(src, "market.volume_24h_usd", "market.volume24hUsd", "volume_24h_usd")),
        "volume_24h_hype": numeric(nested(src, "market.volume_24h_hype", "market.volume24h", "volume_24h_hype")),
        "holders": numeric(nested(src, "holders_count", "holder_count", "holdersCount", "holders")),
        "top10_pct": numeric(nested(src, "top10_pct", "top10_share_pct", "top10SharePct", "holders_top10_pct")),
        "supply": numeric(nested(src, "total_supply", "totalSupply", "supply")),
        "observed_at": utc_now(),
        "provider": "opsis REST indexer",
    }
    if result["top10_pct"] is None:
        frac = numeric(nested(src, "top10_share", "top10Share"))
        if frac is not None and 0 <= frac <= 1:
            result["top10_pct"] = frac * 100
    if all(result[k] is None for k in ("price_usd", "price_hype", "volume_24h_usd", "volume_24h_hype", "holders")):
        raise ValueError("第三方数据结构无法解析，停止机器行情判断，不填假值")
    return result


def dexscreener_snapshot(payload, contract):
    """Optional fallback: only accept a pair with matching chain AND exact base token."""
    pairs = payload.get("pairs", []) if isinstance(payload, dict) else []
    if not isinstance(pairs, list):
        pairs = []
    candidates = []
    for p in pairs:
        if not isinstance(p, dict):
            continue
        chain = str(p.get("chainId", "")).lower()
        base = p.get("baseToken") or {}
        if chain not in ("elysium", "99901") or not isinstance(base, dict) or str(base.get("address", "")).lower() != contract.lower():
            continue
        liquidity = numeric(nested(p, "liquidity.usd")) or 0
        candidates.append((liquidity, p))
    if not candidates:
        raise ValueError("DexScreener 未返回 Elysium 99901 的同合约交易对，不能当作 MACHINE 行情")
    pair = max(candidates, key=lambda x: x[0])[1]
    snap = {
        "price_usd": numeric(pair.get("priceUsd")),
        "price_hype": None,  # priceNative might refer to a different quote token; do not assume HYPE.
        "volume_24h_usd": numeric(nested(pair, "volume.h24")),
        "volume_24h_hype": None,
        "holders": None,
        "top10_pct": None,
        "supply": None,
        "observed_at": utc_now(),
        "provider": "DexScreener (pair source, may be delayed)",
    }
    if snap["price_usd"] is None:
        raise ValueError("DexScreener 价格缺失")
    return snap


def compare_machine(previous, current, cfg):
    if not previous:
        return []
    alerts = []
    for k, label, threshold in (
        ("price_usd", "USD 价格", float(cfg["price_change_alert_pct"])),
        ("price_hype", "HYPE 价格", float(cfg["price_change_alert_pct"])),
    ):
        old, new = numeric(previous.get(k)), numeric(current.get(k))
        if old is not None and new is not None and old > 0:
            diff = 100 * (new - old) / old
            if abs(diff) >= threshold:
                alerts.append(f"$MACHINE {label}较上次采样变动 {diff:+.1f}%（{old:g} → {new:g}）")
        # Prevent duplicate alerts when USD and HYPE prices both spike in the same interval.
        if alerts:
            break
    for k, label, threshold in (
        ("holders", "持有人数量", int(cfg["holders_change_alert"])),
        ("top10_pct", "Top10 集中度", float(cfg["top10_change_alert_pp"])),
    ):
        old, new = numeric(previous.get(k)), numeric(current.get(k))
        if old is not None and new is not None and abs(new - old) >= threshold:
            unit = "人" if k == "holders" else "个百分点"
            alerts.append(f"$MACHINE {label}变化 {new-old:+.1f}{unit}（{old:g} → {new:g}）")
    return alerts


def extract_factory_launch(log):
    if not isinstance(log, dict) or log.get("removed", False):
        return None
    topics = log.get("topics")
    if not isinstance(topics, list) or len(topics) < 3:
        return None
    if str(topics[0]).lower() not in (TOPIC_V3_LAUNCHED, TOPIC_V4_LAUNCHED):
        return None
    t = topics[1]
    if not isinstance(t, str) or not re.fullmatch("0x[0-9a-fA-F]{64}", t):
        return None
    address = "0x" + t[-40:]
    if not valid_address(address) or address.lower() == ZERO_ADDRESS:
        return None
    factory = next((name for name, a in FACTORIES.items() if a.lower() == str(log.get("address", "")).lower()), None)
    if factory is None:
        return None
    return {"token": address, "factory": factory, "block": int(log["blockNumber"], 16), "tx": log.get("transactionHash")}


def calculate_odds(counts, draws):
    """Hypergeometric WITHOUT replacement. Valid only if documented mechanics match."""
    if not isinstance(counts, dict) or not counts:
        return None
    if not isinstance(draws, int) or draws < 1:
        raise ValueError("抽取数量必须为正整数")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in counts.values()):
        raise ValueError("各等级剩余数量必须是非负整数")
    n = sum(counts.values())
    if n == 0 or draws > n:
        raise ValueError("抽取数量不能超过剩余机器数量")
    result = {}
    for name, target in counts.items():
        p1 = target / n
        p_any = 1.0 if draws > n-target else 1 - math.comb(n-target, draws) / math.comb(n, draws)
        result[name] = {"one_draw_pct": p1 * 100, "at_least_one_pct": p_any * 100}
    return result


def reward_model(model, weight):
    daily = numeric(model.get("daily_distributed_hype_verified"))
    total = numeric(model.get("total_active_weight_verified"))
    price = numeric(model.get("machine_buy_price_hype_verified"))
    fee = numeric(model.get("trade_fee_pct"))
    if daily is None or total is None or price is None or total <= 0 or daily <= 0 or price <= 0:
        return None
    if fee is not None and not (0 <= fee < 100):
        raise ValueError("交易费率必须在 0–100% 之间")
    # User-supplied daily rewards are a scenario input, NOT inferred from trading volume.
    daily_income = daily * weight / total
    cost = price * (1 + (fee or 0) / 100)
    return {"daily_hype": daily_income, "daily_yield_pct": 100*daily_income/cost, "theoretical_payback_days": cost/daily_income}


class Radar:
    def __init__(self, config, *, http_get=get_json, rpc=None, state_path=STATE, events_path=EVENTS, on_message=print):
        self.cfg = config
        self.http = http_get
        self.rpc = rpc or RPC(config["network"]["rpc_urls"])
        self.state_path = Path(state_path)
        self.events_path = Path(events_path)
        self.state = load_state(self.state_path)
        self.out = on_message
        self.alerts = []

    def log(self, message):
        self.out(f"[{utc_now()}] {message}")

    def alert(self, key, message):
        """Idempotency across restarts using bounded keys; record before network notification."""
        keys = self.state.setdefault("recent_alert_keys", [])
        if key in keys:
            return
        keys.append(key)
        self.state["recent_alert_keys"] = keys[-500:]
        event = {"time": utc_now(), "key": key, "message": message}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.alerts.append(message)
        self.log("🚨 " + message)

    def _scan_factories(self, block):
        conf = max(0, int(self.cfg["monitor"]["confirmations"]))
        safe_head = max(0, block - conf)
        last = self.state.get("last_block")
        if last is None:
            backfill = max(0, int(self.cfg["monitor"].get("bootstrap_backfill_blocks", 0)))
            self.state["last_block"] = max(0, safe_head - backfill)
            self.log(f"初始化工厂事件检查点 #{self.state['last_block']}；首次运行默认不追溯旧事件。")
            if backfill == 0:
                return
        last = int(self.state["last_block"])
        if last >= safe_head:
            return
        limit = max(1, min(1000, int(self.cfg["monitor"].get("max_blocks_per_round", 300))))
        end = min(safe_head, last + limit)
        query = {
            "fromBlock": hex(last + 1), "toBlock": hex(end),
            "address": list(FACTORIES.values()),
            "topics": [[TOPIC_V3_LAUNCHED, TOPIC_V4_LAUNCHED]],
        }
        logs = self.rpc.call("eth_getLogs", [query])
        if not isinstance(logs, list):
            raise ValueError("RPC eth_getLogs 返回了非数组，未更新检查点")
        for entry in logs:
            parsed = extract_factory_launch(entry)
            if not parsed:
                continue
            addr = parsed["token"].lower()
            if self.cfg["monitor"].get("notify_on_factory_launch", True):
                url = self.cfg["network"]["explorer"].rstrip("/") + "/transaction/" + str(parsed["tx"])
                self.alert("factory:" + str(parsed["tx"]) + ":" + addr,
                           f"【主网新池/工厂事件】{parsed['factory']} ｜币 {addr} ｜区块 {parsed['block']} ｜交易 {url}。注意：仅证明工厂发出事件，不证明安全或可盈利。")
            seen = self.state.setdefault("seen_tokens", [])
            if addr not in seen:
                seen.append(addr)
        # Advance ONLY after complete successful response / parsing.
        self.state["last_block"] = end
        if safe_head > end:
            self.log(f"事件扫描落后 {safe_head-end} 块，正在逐轮补扫；未宣称全链实时覆盖。")
        else:
            self.log(f"工厂事件已检查至区块 #{end}，本轮 {len(logs)} 条匹配日志。")

    def _scan_tokens(self):
        url = self.cfg["sources"]["perpme_api"].rstrip("/") + "/tokens"
        payload = self.http(url, params={"sort": "new", "limit": min(100, max(1, self.cfg["monitor"].get("api_limit", 50)))})
        items = extract_items(payload)
        if not isinstance(items, list):
            raise ValueError("代币列表无法解析")
        seen_order = list(dict.fromkeys(self.state.get("seen_tokens", [])))
        seen = set(seen_order)
        bootstrap = not self.state.get("api_initialized", False)
        count = 0
        for token in reversed(items):
            if not isinstance(token, dict):
                continue
            addr = str(token.get("address", "")).lower()
            if not valid_address(addr):
                continue
            if addr in seen:
                continue
            seen.add(addr)
            seen_order.append(addr)
            if not bootstrap and self.cfg["monitor"].get("notify_on_new_tokens", True):
                q = token.get("quote_token") or {}
                sym = str(token.get("symbol") or "?")[:24]
                quote = (str(q.get("symbol") or "?") if isinstance(q, dict) else "?")[:20]
                vol = numeric(token.get("volume_24h_usd"))
                count += 1
                self.alert("api-token:" + addr,
                           f"【PerpMe 新币】{sym} / {quote} ｜{addr} ｜场所 {token.get('venue','未知')} ｜24h交易量 " +
                           (f"${vol:,.0f}" if vol is not None else "暂无") +
                           f" ｜https://perpme.fun/elysium/token/{addr}。来自索引API，可能有缓存或延迟；不是买入信号。")
        self.state["seen_tokens"] = seen_order[-3000:]
        self.state["api_initialized"] = True
        self.log(f"PerpMe 新币列表 {len(items)} 条；" + ("首次建立基线，不发旧币通知" if bootstrap else f"发现 {count} 个未见币种"))

    def _scan_machine(self):
        address = self.cfg["machine"]["token_contract"]
        code = self.rpc.call("eth_getCode", [address, "latest"])
        if code in ("0x", "0x0", None):
            raise RuntimeError("机器 token 地址没有链上代码：跳过，可能 RPC 未同步或地址未部署")
        try:
            payload = self.http(self.cfg["sources"]["opsis_api"].rstrip("/") + "/tokens/" + address)
            snapshot = machine_snapshot(payload)
        except Exception as exc:
            self.log("OPSIS 行情不可用/字段变化，尝试 DexScreener 同链同合约数据：" + str(exc)[:100])
            fallback = self.cfg["sources"].get("dexscreener_api")
            if not fallback:
                raise
            snap_payload = self.http(fallback.rstrip("/") + "/" + address)
            snapshot = dexscreener_snapshot(snap_payload, address)
        prior = self.state.get("machine")
        self.state["machine"] = snapshot
        self.log("$MACHINE 快照 [" + snapshot["provider"] + "] " + ", ".join(f"{k}={snapshot[k]}" for k in ("price_usd", "price_hype", "volume_24h_usd", "holders", "top10_pct")))
        if self.cfg["monitor"].get("notify_on_machine_changes", True):
            for msg in compare_machine(prior, snapshot, self.cfg["machine"]):
                self.alert("machine:" + msg + ":" + snapshot["observed_at"], msg + "。来源：" + snapshot["provider"] + "，数据延迟/小池滑点需自行核对。")

    def once(self):
        self.alerts = []
        height = self.rpc.check()  # Fail closed: never interpret data from the wrong Elysium.
        self.log(f"网络核验成功：chain ID 99901 ｜ RPC {self.rpc.url} ｜最新区块 {height}")
        errors = []
        for name, action in (("工厂事件", lambda: self._scan_factories(height)),
                             ("新币索引", self._scan_tokens),
                             ("机器行情", self._scan_machine)):
            try:
                action()
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                self.log(f"⚠️ {name} 本轮失败：{exc}")
        atomic_json(self.state_path, self.state)
        if self.alerts:
            self.send_telegram("\n\n".join(self.alerts[:10]))
        self.log("本轮结束。" + ("失败模块：" + "、".join(errors) if errors else "所有模块已执行。"))
        return {"height": height, "alerts": list(self.alerts), "errors": errors, "rpc": self.rpc.url}

    def send_telegram(self, message, force=False):
        tg = self.cfg.get("telegram", {})
        if not tg.get("enabled") and not force:
            self.log("Telegram 未启用：仅本地记录提醒。")
            return False
        token, chat = str(tg.get("bot_token") or ""), str(tg.get("chat_id") or "")
        if not token or not chat:
            self.log("Telegram 未配置完整，跳过发送。")
            return False
        # Bot token is never printed/logged.
        url = "https://api.telegram.org/bot" + token + "/sendMessage"
        try:
            result = self.http(url, method="POST", body={"chat_id": chat, "text": "📡 Elysium Radar\n" + message[:3500], "disable_web_page_preview": True})
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ValueError("Telegram API 未确认发送成功")
            self.log("Telegram 已发送预警")
            return True
        except Exception as exc:
            # Mask secret if an HTTP stack included the URL.
            self.log("Telegram 发送失败：" + str(exc).replace(token, "[redacted]")[:160])
            return False


def display_odds(config, draws):
    counts = config["machine"].get("odds_remaining_counts_verified_by_user", {})
    if not counts:
        return "尚无已核实的各代剩余数量：拒绝编造机器抽中概率。请先在 config.json 填入真实剩余数量并确认抽取机制是等概率、不放回。"
    ans = calculate_odds(counts, draws)
    text = [f"抽 {draws} 次；假设每台机器等概率、无放回且剩余数量准确："]
    for name, pair in ans.items():
        text.append(f"{name}: 单抽 {pair['one_draw_pct']:.3f}%，至少抽中一次 {pair['at_least_one_pct']:.3f}%")
    text.append("注意：这些是条件概率，不是项目官方承诺；若分配机制不是等概率无放回，结果无效。")
    return "\n".join(text)


def display_roi(config):
    text = []
    for gen, weight in config["machine"].get("generation_weights_unverified", {}).items():
        result = reward_model(config["machine"]["reward_model"], float(weight))
        if result:
            text.append(f"{gen}: 假设日奖励 {result['daily_hype']:.5f} HYPE，静态回本 {result['theoretical_payback_days']:.1f} 天，日收益率 {result['daily_yield_pct']:.3f}%")
    if not text:
        return "收益池实际日分配、已激活总权重和买价尚未提供已核实数据，无法算回本时间。收益倍率只是假设值。"
    return "以下仅为使用手动输入参数的情景演算，不含价格波动、gas、价格滑点、未来交易量变动：\n" + "\n".join(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Elysium Radar — 本地零 API-Key 的只读监控")
    parser.add_argument("command", nargs="?", default="once", choices=["check", "once", "run", "odds", "roi", "telegram-test", "status"])
    parser.add_argument("--draws", type=int, default=1, help="模拟抽取数量（仅真实完整的剩余数量已填写时计算）")
    args = parser.parse_args(argv)
    cfg = load_config()
    if args.command == "odds":
        print(display_odds(cfg, args.draws)); return 0
    if args.command == "roi":
        print(display_roi(cfg)); return 0
    if args.command == "status":
        st = load_state()
        print(json.dumps(st, ensure_ascii=False, indent=2)); return 0
    radar = Radar(cfg)
    if args.command == "check":
        h = radar.rpc.check()
        print(f"✔ RPC 已验证：Kinetiq Elysium 99901；最新区块 #{h}；地址 {radar.rpc.url}")
        return 0
    if args.command == "telegram-test":
        return 0 if radar.send_telegram("这是一条只读监控测试消息。", force=True) else 1
    if args.command == "once":
        radar.once(); return 0
    try:
        while True:
            try:
                radar.once()
            except Exception as exc:
                radar.log(f"⚠️ 本轮安全停止：{exc}（下一轮将重试）")
            time.sleep(max(15, int(cfg["monitor"]["poll_seconds"])))
    except KeyboardInterrupt:
        print("已停止监控")
    return 0


if __name__ == "__main__":
    sys.exit(main())
