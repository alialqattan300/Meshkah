"""MT5 bridge tests against a fake terminal: python3 -m unittest discover -s tests -v"""

import datetime as dt
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "mt5"))
sys.path.insert(0, HERE)
import fx  # noqa: E402
from bridge import Bridge, BridgeError  # noqa: E402
from fake_mt5 import FakeMT5  # noqa: E402

NOW = dt.datetime(2026, 11, 10, 10, 0, tzinfo=fx.UTC)
CFG = fx.load_config(fx.Store("/nonexistent"))


def setup_order(d, pair="EURUSD", side="short", entry=1.11832, stop="pips:28", rate=1.0, now=NOW, verdict="PASS"):
    st = fx.Store(d)
    plan = fx.make_plan(pair, side, entry, stop, "2R", CFG)
    o = fx.size_order(plan, CFG, rate)
    st.write("order.json", o)
    st.write("risk.json", {"fingerprint": o["fingerprint"], "verdict": verdict, "reasons": [],
                           "checked_at": now.isoformat(), "fx_day": fx.fx_day(now).isoformat()})
    return o


class Bridge_(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def br(self, mt5, now=NOW):
        return Bridge(mt5, self.d, clock=lambda: now)

    def test_refuses_real_account(self):
        m = FakeMT5(trade_mode=2)
        o = setup_order(self.d)
        for call in (lambda b: b.status(), lambda b: b.place(o["fingerprint"]), lambda b: b.positions()):
            with self.assertRaises(BridgeError) as e:
                call(self.br(m))
            self.assertIn("REAL", str(e.exception))
        self.assertEqual(m.sent, [])
        self.assertGreater(m.shutdowns, 0)

    def test_refuses_contest_and_no_login(self):
        with self.assertRaises(BridgeError):
            self.br(FakeMT5(trade_mode=1)).status()
        with self.assertRaises(BridgeError):
            self.br(FakeMT5(login_ok=False)).status()

    def test_status_and_suffix(self):
        b = self.br(FakeMT5())
        self.assertEqual(b.status()["account"], "DEMO")
        q = b.quote("eur/usd")
        self.assertEqual((q["symbol"], q["spread_pips"]), ("EURUSDm", 1.0))

    def test_place_short(self):
        m = FakeMT5()
        o = setup_order(self.d)
        r = self.br(m).place(o["fingerprint"])
        req = m.sent[0]
        self.assertEqual((req["symbol"], req["type"], req["volume"]), ("EURUSDm", m.ORDER_TYPE_SELL, o["lots"]))
        self.assertEqual(req["price"], 1.118)                       # a short sells at the bid
        self.assertEqual(req["sl"], round(1.118 + o["stop_pips"] * 0.0001, 5))
        self.assertEqual(req["tp"], round(1.118 - o["target_pips"] * 0.0001, 5))
        self.assertEqual(req["type_filling"], m.ORDER_FILLING_IOC)
        self.assertLessEqual(r["loss_at_stop"], o["risk_budget"] * 1.05)
        st = fx.Store(self.d)
        self.assertEqual(len(st.read("sent.json")), 1)
        self.assertEqual(st.read("journal.json")[0]["fingerprint"], o["fingerprint"])
        with self.assertRaises(BridgeError) as e:                   # never twice
            self.br(m).place(o["fingerprint"])
        self.assertIn("already sent", str(e.exception))
        self.assertEqual(len(m.sent), 1)

    def test_needs_fresh_pass_and_matching_fingerprint(self):
        m = FakeMT5()
        o = setup_order(self.d, verdict="BLOCK")
        with self.assertRaises(BridgeError):
            self.br(m).place(o["fingerprint"])
        o = setup_order(self.d)
        with self.assertRaises(BridgeError):
            self.br(m).place("deadbeef0000")
        with self.assertRaises(BridgeError) as e:
            self.br(m, NOW + dt.timedelta(minutes=16)).place(o["fingerprint"])
        self.assertIn("older than", str(e.exception))
        self.assertEqual(m.sent, [])

    def test_wide_spread_and_currency(self):
        o = setup_order(self.d)
        with self.assertRaises(BridgeError) as e:
            self.br(FakeMT5(ask=1.11840)).place(o["fingerprint"])  # 4 pips vs 1 planned
        self.assertIn("spread", str(e.exception))
        with self.assertRaises(BridgeError):
            self.br(FakeMT5(currency="EUR")).place(o["fingerprint"])

    def test_contract_mismatch_blocks(self):
        # Gold sized at 100 oz a lot; a broker quoting 1,000 oz a lot would lose 10x
        o = setup_order(self.d, pair="XAUUSD", side="long", entry=4074.9, stop="pips:60")
        m = FakeMT5(bid=4075.00, ask=4075.20, contract=1000, tick_value=10.0, tick_size=0.01, digits=2)
        with self.assertRaises(BridgeError) as e:
            self.br(m).place(o["fingerprint"])
        self.assertIn("contract_size", str(e.exception))
        ok = FakeMT5(bid=4075.00, ask=4075.20, contract=100, tick_value=1.0, tick_size=0.01, digits=2)
        r = self.br(ok).place(o["fingerprint"])
        self.assertEqual(ok.sent[0]["symbol"], "XAUUSDm")
        self.assertEqual(ok.sent[0]["sl"], 4069.2)
        self.assertLessEqual(r["loss_at_stop"], 10.5)

    def test_broker_refusals(self):
        o = setup_order(self.d)
        with self.assertRaises(BridgeError):
            self.br(FakeMT5(check_retcode=10019)).place(o["fingerprint"])
        with self.assertRaises(BridgeError) as e:
            self.br(FakeMT5(send_retcode=10027)).place(o["fingerprint"])
        self.assertIn("Algo Trading", str(e.exception))
        self.assertIsNone(fx.Store(self.d).read("sent.json"))

    def test_close(self):
        m = FakeMT5()
        m.positions = [NS(ticket=42, symbol="EURUSDm", type=m.ORDER_TYPE_SELL, volume=0.03)]
        r = self.br(m).close(42)
        self.assertEqual((r["closed"], m.sent[0]["type"], m.sent[0]["position"], m.sent[0]["price"]), (42, m.ORDER_TYPE_BUY, 42, 1.1181))
        with self.assertRaises(BridgeError):
            self.br(m).close(99)


if __name__ == "__main__":
    unittest.main()
