import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from elysium_radar import (FACTORIES, TOPIC_V3_LAUNCHED, RPC, Radar, calculate_odds,
                            compare_machine, extract_factory_launch, machine_snapshot, dexscreener_snapshot,
                            reward_model, load_config)


class MockRPC:
    def __init__(self):
        self.url = "mock:rpc"
        self.head = 500
        self.logs = []
        self.calls = []
    def check(self):
        return self.head
    def call(self, method, args):
        self.calls.append((method, args))
        if method == "eth_getLogs":
            return list(self.logs)
        if method == "eth_getCode":
            return "0x6001"
        raise AssertionError(method)


class TestRadar(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8"))
    def test_wrong_chain_fails_closed(self):
        def request(url, **k):
            return {"jsonrpc": "2.0", "result": "0x53b"} # 1339
        with self.assertRaises(RuntimeError):
            RPC(["https://safe.invalid"], request=request).check()
    def test_probability_without_replacement(self):
        p = calculate_odds({"rare": 1, "normal": 3}, 2)
        self.assertAlmostEqual(p["rare"]["one_draw_pct"], 25)
        self.assertAlmostEqual(p["rare"]["at_least_one_pct"], 50)
    def test_missing_market_numbers_never_zero(self):
        market = machine_snapshot({"market": {"price_usd": "123.45"}})
        self.assertEqual(market["price_usd"], 123.45)
        self.assertIsNone(market["holders"])
        with self.assertRaises(ValueError):
            machine_snapshot({"market": {}})
    def test_dexscreener_exact_identity_and_quote(self):
        token = "0x" + "a" * 40
        data = {"pairs": [
            {"chainId":"bsc", "baseToken":{"address":token}, "priceUsd":"100"},
            {"chainId":"elysium", "baseToken":{"address":token}, "priceUsd":"20", "priceNative":"1.23", "liquidity":{"usd":10000}, "volume":{"h24":123}},
        ]}
        snap = dexscreener_snapshot(data, token)
        self.assertEqual(snap["price_usd"], 20)
        self.assertIsNone(snap["price_hype"])
        self.assertEqual(snap["volume_24h_usd"], 123)
        with self.assertRaises(ValueError):
            dexscreener_snapshot({"pairs":data["pairs"][:1]}, token)

    def test_machine_alert_delta(self):
        current = {"price_usd": 112.4, "price_hype": 1.2, "holders": 200, "top10_pct": 40}
        prev = {"price_usd": 100, "price_hype": 1.0, "holders": 100, "top10_pct": 20}
        alerts = compare_machine(prev, current, self.cfg["machine"])
        self.assertEqual(len(alerts), 3)
    def test_launch_log_validate_factory(self):
        addr = "a" * 40
        base = {"removed": False, "address": list(FACTORIES.values())[0],
                "blockNumber": hex(123), "transactionHash": "0x"+"f"*64,
                "topics": [TOPIC_V3_LAUNCHED, "0x"+"0"*24+addr, "0x"+"0"*64]}
        parsed = extract_factory_launch(base)
        self.assertEqual(parsed["token"].lower(), "0x" + addr)
        base["address"] = "0x" + "f" * 40
        self.assertIsNone(extract_factory_launch(base))
    def test_roi_only_given_real_inputs(self):
        self.assertIsNone(reward_model({}, 2.0))
        v = reward_model({"daily_distributed_hype_verified": 10, "total_active_weight_verified": 100, "machine_buy_price_hype_verified": 2}, 1)
        self.assertAlmostEqual(v["theoretical_payback_days"], 20)
    def test_bootstrap_no_old_alert_replay_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            rpc = MockRPC()
            logs = []
            def http(url, **kwargs):
                if url.endswith("/tokens"):
                    return {"items": [{"address":"0x"+"a"*40, "symbol":"AAA", "quote_token":{"symbol":"HYPE"}}]}
                return {"market":{"price_usd": 5.0}}
            radar = Radar(self.cfg, http_get=http, rpc=rpc, state_path=path/"state.json", events_path=path/"events.jsonl", on_message=logs.append)
            first = radar.once()
            self.assertEqual(first["alerts"], [])
            self.assertEqual(radar.state["last_block"], 497)
            self.assertTrue(radar.state["api_initialized"])
            rpc.head = 510
            new = "0x"+"b"*40
            rpc.logs = [{"removed":False,"address":list(FACTORIES.values())[0],"blockNumber": hex(499),
                         "transactionHash":"0x"+"1"*64,
                         "topics":[TOPIC_V3_LAUNCHED,"0x"+"0"*24+"b"*40,"0x"+"0"*64]}]
            def http_new(url, **kwargs):
                if url.endswith("/tokens"):
                    return {"items":[{"address":new,"symbol":"BBB", "quote_token":{"symbol":"HYPE"}},
                                     {"address":"0x"+"a"*40,"symbol":"AAA"}]}
                return {"market":{"price_usd":5.0}}
            radar.http = http_new
            second = radar.once()
            self.assertEqual(len(second["alerts"]), 1) # one event, API deduped
            third = radar.once()
            self.assertEqual(third["alerts"], [])
            self.assertEqual(len((path/"events.jsonl").read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
