"""Offline tests for fx.py: python3 -m unittest discover -s tests -v"""

import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import fx  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
CHARTS = os.path.join(FIX, "charts-2026-11-06.json.gz")
CAL = os.path.join(FIX, "calendar-week-2026-11-08.json")
CFG = fx.load_config(fx.Store("/nonexistent"))
U = fx.UTC


def utc(*a):
    return dt.datetime(*a, tzinfo=U)


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = fx.main(list(argv))
    return code, out.getvalue()


class Basics(unittest.TestCase):
    def test_pairs(self):
        self.assertEqual(fx.norm_pair("eur/usd"), "EURUSD")
        self.assertEqual(fx.norm_pair("USDJPY=X"), "USDJPY")
        for bad in ("EURXYZ", "EUREUR", "EURUS", "BTCUSD", "XPTUSD"):
            with self.assertRaises(fx.FxError):
                fx.norm_pair(bad)

    def test_pips(self):
        self.assertEqual(fx.pip_size("USDJPY"), 0.01)
        self.assertEqual(fx.pip_size("EURUSD"), 0.0001)
        self.assertEqual(fx.pips("EURUSD", 1.1000, 1.0975), 25.0)
        self.assertEqual(fx.pips("GBPJPY", 211.50, 211.00), 50.0)

    def test_market_hours(self):
        self.assertFalse(fx.market_open(utc(2026, 11, 7, 12)))        # Saturday
        self.assertFalse(fx.market_open(utc(2026, 11, 8, 21, 59)))    # Sun 16:59 NY (EST)
        self.assertTrue(fx.market_open(utc(2026, 11, 8, 22, 0)))      # Sun 17:00 NY
        self.assertTrue(fx.market_open(utc(2026, 11, 6, 21, 59)))     # Fri 16:59 NY
        self.assertFalse(fx.market_open(utc(2026, 11, 6, 22, 0)))     # Fri 17:00 NY
        self.assertTrue(fx.market_open(utc(2026, 7, 3, 20, 59)))      # Fri 16:59 NY (EDT)
        self.assertFalse(fx.market_open(utc(2026, 7, 3, 21, 0)))
        self.assertEqual(fx.next_open(utc(2026, 11, 7, 12)), utc(2026, 11, 8, 22))

    def test_fx_day_rolls_at_5pm_ny(self):
        self.assertEqual(fx.fx_day(utc(2026, 11, 5, 21, 59)), dt.date(2026, 11, 5))
        self.assertEqual(fx.fx_day(utc(2026, 11, 5, 22, 0)), dt.date(2026, 11, 6))

    def test_indicators(self):
        self.assertEqual(fx.rsi([1.0 + i * 0.01 for i in range(30)]), 100.0)
        self.assertAlmostEqual(fx.rsi([1.0 - i * 0.01 for i in range(30)]), 0.0)
        bars = [(i, 1, 1.002, 0.998, 1.0) for i in range(20)]
        self.assertAlmostEqual(fx.atr(bars), 0.004)


def _bars(day, tz, start, n, f):
    """n five-minute bars from local start; f(i) -> (o, h, l, c)."""
    t0 = fx.at(day, start, tz).timestamp()
    return [(int(t0 + i * 300),) + tuple(f(i)) for i in range(n)]


class SessionBreakout(unittest.TestCase):
    day = dt.date(2026, 11, 4)

    def make(self, after):
        rng = _bars(self.day, fx.LON, "00:00", 84, lambda i: (1.1000, 1.1020, 1.0980, 1.1000))
        post = _bars(self.day, fx.LON, "07:00", len(after), lambda i: after[i])
        return rng + post

    def test_long(self):
        b = self.make([(1.10, 1.1015, 1.0990, 1.1010), (1.101, 1.1030, 1.1005, 1.1025)])
        r = fx.session_breakout("EURUSD", b, "asia", self.day, utc(2026, 11, 4, 9))
        self.assertEqual(r["status"], "long")
        self.assertEqual((r["range_high"], r["range_low"], r["range_pips"]), (1.1020, 1.0980, 40.0))
        self.assertEqual(r["trigger_time"], "2026-11-04T07:10:00+00:00")
        self.assertEqual(r["beyond_pips"], 5.0)

    def test_short_and_incomplete_bar(self):
        b = self.make([(1.10, 1.1000, 1.0970, 1.0975)])
        r = fx.session_breakout("EURUSD", b, "asia", self.day, utc(2026, 11, 4, 7, 4))
        self.assertEqual(r["status"], "not_yet")
        r = fx.session_breakout("EURUSD", fx.bars_from(_chart(b), 300, utc(2026, 11, 4, 7, 4)), "asia", self.day, utc(2026, 11, 4, 7, 6))
        self.assertEqual(r["status"], "waiting")
        r = fx.session_breakout("EURUSD", b, "asia", self.day, utc(2026, 11, 4, 7, 6))
        self.assertEqual(r["status"], "short")

    def test_expired_and_missing(self):
        b = self.make([(1.10, 1.1010, 1.0990, 1.1000)] * 70)
        self.assertEqual(fx.session_breakout("EURUSD", b, "asia", self.day, utc(2026, 11, 4, 13))["status"], "expired")
        self.assertEqual(fx.session_breakout("EURUSD", b[40:], "asia", self.day, utc(2026, 11, 4, 13))["status"], "no_data")

    def test_dst_ny(self):
        # NY 08:00 is 12:00 UTC in summer, 13:00 UTC in winter
        s = fx.session_breakout("EURUSD", [], "ny", dt.date(2026, 7, 1), utc(2026, 7, 1, 12, 10))
        self.assertEqual(s["range_start"], "2026-07-01T08:00:00-04:00")
        w = fx.session_breakout("EURUSD", [], "ny", dt.date(2026, 11, 4), utc(2026, 11, 4, 13, 10))
        self.assertEqual(w["range_start"], "2026-11-04T08:00:00-05:00")


def _chart(bars):
    return {"chart": {"result": [{"timestamp": [b[0] for b in bars], "indicators": {"quote": [
        {"open": [b[1] for b in bars], "high": [b[2] for b in bars], "low": [b[3] for b in bars],
         "close": [b[4] for b in bars], "volume": [0] * len(bars)}]}}]}}


class PlanAndSize(unittest.TestCase):
    def test_plan_long_range_mid(self):
        p = fx.make_plan("EURUSD", "long", 1.10250, "range-mid", "2R", CFG, (1.1020, 1.0980))
        self.assertEqual(p["stop"], 1.1)
        self.assertEqual(p["stop_pips"], 25.0)
        self.assertEqual(p["target"], 1.1075)
        self.assertEqual(p["reward_risk"], 2.0)

    def test_plan_short_rounds_away(self):
        p = fx.make_plan("USDJPY", "short", 157.5004, "pips:20", "1.5R", CFG)
        self.assertEqual(p["stop"], 157.701)   # ceiling for a short
        self.assertLess(p["target"], 157.5004)
        self.assertGreaterEqual(p["reward_risk"], 1.49)

    def test_plan_refusals(self):
        with self.assertRaises(fx.FxError):
            fx.make_plan("EURUSD", "long", 1.1, "price:1.1010", "2R", CFG)      # stop above entry
        with self.assertRaises(fx.FxError):
            fx.make_plan("EURUSD", "long", 1.1, "pips:2", "2R", CFG)            # tighter than 3 spreads
        with self.assertRaises(fx.FxError):
            fx.make_plan("EURUSD", "short", 1.1, "pips:20", "price:1.1050", CFG)  # target wrong side
        with self.assertRaises(fx.FxError):
            fx.make_plan("EURUSD", "long", 1.1, "range-mid", "2R", CFG)         # no range

    def test_size_usd_quote(self):
        p = fx.make_plan("EURUSD", "long", 1.10250, "range-mid", "2R", CFG, (1.1020, 1.0980))
        o = fx.size_order(p, CFG, 1.0, balance=1000, risk_pct=1)
        # 10 USD / ((25 + 1 spread) pips * 0.0001) = 3846 units -> 0.03 lots
        self.assertEqual(o["lots"], 0.03)
        self.assertEqual(o["pip_value_per_lot"], 10.0)
        self.assertAlmostEqual(o["loss_at_stop"], 7.8)
        self.assertEqual(o["decided_by"], "max loss per trade")
        self.assertTrue(o["demo"])

    def test_size_jpy_quote_and_leverage_cap(self):
        p = fx.make_plan("USDJPY", "long", 150.0, "pips:20", "2R", CFG)
        rate = 1 / 150.0
        o = fx.size_order(p, CFG, rate, balance=10000, risk_pct=1)
        self.assertAlmostEqual(o["pip_value_per_lot"], 6.6667, places=3)
        self.assertLessEqual(o["loss_at_stop"], 100)
        o2 = fx.size_order(p, CFG, rate, balance=10000, risk_pct=50)
        self.assertEqual(o2["decided_by"], "max leverage")
        self.assertLessEqual(o2["effective_leverage"], 10)

    def test_size_refuses_under_min_lot(self):
        p = fx.make_plan("EURUSD", "long", 1.1, "pips:100", "2R", CFG)
        with self.assertRaises(fx.FxError):
            fx.size_order(p, CFG, 1.0, balance=50, risk_pct=1)


class News(unittest.TestCase):
    def setUp(self):
        with open(CAL) as f:
            self.ev = fx.parse_calendar(json.load(f))

    def test_parse(self):
        self.assertEqual(len(self.ev), 101)
        cpi = [e for e in self.ev if e["title"] == "CPI m/m"][0]
        self.assertEqual((cpi["currency"], cpi["impact"], cpi["time"]), ("USD", "High", "2026-11-10T13:30:00+00:00"))

    def test_near(self):
        t = utc(2026, 11, 10, 13, 10)
        self.assertTrue(fx.news_near(self.ev, "EURUSD", t, 30, ["High"]))
        self.assertFalse(fx.news_near(self.ev, "EURGBP", t, 30, ["High"]))
        self.assertFalse(fx.news_near(self.ev, "EURUSD", utc(2026, 11, 10, 12, 50), 30, ["High"]))


class Risk(unittest.TestCase):
    def order(self, **k):
        p = fx.make_plan("EURUSD", "long", 1.10250, "range-mid", "2R", CFG, (1.1020, 1.0980))
        o = fx.size_order(p, CFG, 1.0)
        o.update(k)
        return o

    def test_pass(self):
        _, ok = fx.risk_check(self.order(), CFG, [], [], utc(2026, 11, 4, 9))
        self.assertTrue(ok)

    def test_blocks(self):
        wed = utc(2026, 11, 4, 9)
        cases = [
            (self.order(reward_risk=1.0), [], [], wed),
            (self.order(loss_at_stop=11), [], [], wed),
            (self.order(), [{"fx_day": "2026-11-04", "loss_at_stop": 25}], [], wed),
            (self.order(), [{"fx_day": "2026-11-04", "loss_at_stop": 1}] * 3, [], wed),
            (self.order(), [], [], utc(2026, 11, 7, 9)),               # Saturday
            (self.order(), [], [], utc(2026, 11, 6, 20, 30)),          # Fri 15:30 NY
            (self.order(), [], [], utc(2026, 11, 4, 22, 0)),           # rollover
            (self.order(), [], None, wed),                             # no calendar
            (self.order(effective_leverage=12), [], [], wed),
        ]
        for o, j, ev, t in cases:
            _, ok = fx.risk_check(o, CFG, j, ev, t)
            self.assertFalse(ok, (o["reward_risk"], o["loss_at_stop"], j, ev, t))
        ev = [{"time": "2026-11-04T09:15:00+00:00", "currency": "EUR", "impact": "High", "title": "x"}]
        self.assertFalse(fx.risk_check(self.order(), CFG, [], ev, wed)[1])
        self.assertTrue(fx.risk_check(self.order(), CFG, [], None, wed, skip_news=True)[1])
        self.assertTrue(fx.risk_check(self.order(), CFG, [{"fx_day": "2026-11-03", "loss_at_stop": 30}], [], wed)[1])


class Replay(unittest.TestCase):
    def test_reproducible_on_real_bars(self):
        ch = fx.Data(CHARTS).chart("EURUSD=X", "60d", "5m")
        bars = fx.bars_from(ch, 300)
        plan = fx.make_plan("EURUSD", "short", 1.11832, "range-mid", "2R", CFG, (1.12372, 1.11857))
        plan["range"] = "asia"
        start, flat = utc(2026, 11, 6, 7, 5), utc(2026, 11, 6, 16)
        a = fx.replay(plan, bars, start, flat, sims=2000, seed=19, rtype="asia")
        b = fx.replay(plan, bars, start, flat, sims=2000, seed=19, rtype="asia")
        self.assertEqual(a, b)
        self.assertAlmostEqual(a["target_pct"] + a["stop_pct"] + a["open_pct"], 100.0, places=0)
        h = a["history"]
        self.assertEqual(h["fired"], h["target"] + h["stop"] + h["open"])
        self.assertGreater(h["fired"], 0)
        self.assertIn(a["verdict"].split(" ")[0], ("leans", "the", "close"))

    def test_walk_stop_first_on_two_touch(self):
        self.assertEqual(fx._walk([(1.2, 1.0, 1.1)], "long", 1.1, 1.05, 1.15)[0], "stop")
        self.assertEqual(fx._walk([(1.2, 1.0, 1.1)], "short", 1.1, 1.15, 1.05)[0], "stop")
        self.assertEqual(fx._walk([(1.11, 1.09, 1.1)], "long", 1.1, 1.05, 1.15), ("open", 1.1))


class EndToEnd(unittest.TestCase):
    def test_chain_offline(self):
        with tempfile.TemporaryDirectory() as d:
            D = os.path.join(d, "forex")
            self.assertEqual(run("--dir", D, "init", "--balance", "1000")[0], 0)
            code, out = run("--dir", D, "scan", "daily", "--offline", CHARTS, "--pairs", "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,EURJPY",
                            "--now", "2026-11-08T05:00:00+00:00")
            self.assertIn("closes through 2026-11-06", out)
            self.assertIn("AUDUSD", out)
            code, out = run("--dir", D, "scan", "session", "--offline", CHARTS, "--pairs", "EURUSD", "--now", "2026-11-06T13:00:00+00:00")
            self.assertEqual(code, 0, out)
            self.assertIn("EURUSD  short at 1.11832", out)
            code, out = run("--dir", D, "plan", "EURUSD", "--offline", CHARTS, "--write")
            self.assertIn("stop 1.12115 (28.3 pips)", out)
            code, out = run("--dir", D, "replay", "--offline", CHARTS, "--sims", "1000", "--write")
            self.assertEqual(code, 0, out)
            self.assertIn("history: the same asia rule fired short", out)
            code, out = run("--dir", D, "size", "--offline", CHARTS, "--write")
            self.assertIn("0.03 lots short EUR/USD", out)
            code, out = run("--dir", D, "risk", "--calendar", CAL, "--now", "2026-11-10T13:20:00+00:00")
            self.assertEqual(code, 1)
            self.assertIn("High news within 30 min: USD", out)
            code, out = run("--dir", D, "journal", "add", "--now", "2026-11-10T13:20:00+00:00")
            self.assertEqual(code, 1)
            self.assertIn("no PASS", out)
            code, out = run("--dir", D, "risk", "--calendar", CAL, "--now", "2026-11-10T10:00:00+00:00")
            self.assertEqual(code, 0, out)
            self.assertIn("SELL 0.03 lots EUR/USD", out)
            self.assertEqual(run("--dir", D, "journal", "add", "--now", "2026-11-10T10:01:00+00:00")[0], 0)
            code, out = run("--dir", D, "sessions", "--now", "2026-11-07T12:00:00+00:00")
            self.assertIn("closed for the weekend", out)

    def test_never_talks_to_a_broker(self):
        with open(os.path.join(os.path.dirname(HERE), "fx.py")) as f:
            src = f.read().lower()
        for word in ("alpaca", "oanda", "metatrader", "place_order", "api_key", "password"):
            self.assertNotIn(word, src)


class MetalsAndOil(unittest.TestCase):
    def test_names(self):
        for k, v in (("gold", "XAUUSD"), ("XAU/USD", "XAUUSD"), ("silver", "XAGUSD"), ("oil", "USOIL"),
                     ("WTI", "USOIL"), ("brent", "UKOIL"), ("\u0630\u0647\u0628", "XAUUSD"), ("\u0646\u0641\u0637", "USOIL")):
            self.assertEqual(fx.norm_pair(k), v)
        self.assertEqual(fx.yahoo("XAUUSD"), "GC=F")
        self.assertEqual(fx.yahoo("UKOIL"), "BZ=F")
        self.assertEqual(fx.yahoo("EURUSD"), "EURUSD=X")
        self.assertEqual(fx.label("USOIL"), "WTI oil (USOIL)")
        self.assertEqual(fx.quote_ccy("XAGUSD"), "USD")

    def test_hours_have_a_daily_break(self):
        wed_1730_ny = utc(2026, 11, 4, 22, 30)
        self.assertTrue(fx.market_open(wed_1730_ny, "EURUSD"))
        self.assertFalse(fx.market_open(wed_1730_ny, "XAUUSD"))
        self.assertTrue(fx.market_open(utc(2026, 11, 4, 23, 0), "XAUUSD"))
        sun_1730_ny = utc(2026, 11, 8, 22, 30)
        self.assertTrue(fx.market_open(sun_1730_ny, "EURUSD"))
        self.assertFalse(fx.market_open(sun_1730_ny, "USOIL"))

    def test_gold_size(self):
        p = fx.make_plan("XAUUSD", "long", 4074.90, "pips:60", "2R", CFG)
        self.assertEqual((p["stop"], p["stop_pips"]), (4068.9, 60.0))
        o = fx.size_order(p, CFG, 1.0, balance=1000, risk_pct=1)
        # 10 USD / ((60 + 3) pips * 0.1 * 1 oz) = 1.58 oz -> 0.01 lot (1 oz)
        self.assertEqual((o["lots"], o["units"], o["pip_value_per_lot"]), (0.01, 1, 10.0))
        self.assertAlmostEqual(o["loss_at_stop"], 6.3)

    def test_oil_size_and_contract_override(self):
        p = fx.make_plan("USOIL", "short", 90.84, "pips:87", "2R", CFG)
        self.assertEqual(p["stop"], 91.71)
        o = fx.size_order(p, CFG, 1.0, balance=1000, risk_pct=1)
        self.assertEqual((o["lots"], o["units"]), (0.01, 10))
        cfg = dict(CFG, contract_size={"USOIL": 100})
        o2 = fx.size_order(p, cfg, 1.0, balance=1000, risk_pct=1)
        self.assertEqual((o2["lots"], o2["units"]), (0.11, 11))  # 10 / (90 pips * 0.01) = 11.1 barrels

    def test_oil_news(self):
        with open(CAL) as f:
            ev = fx.parse_calendar(json.load(f))
        crude = [e for e in ev if "Crude" in e["title"]][0]
        t = dt.datetime.fromisoformat(crude["time"])
        self.assertTrue(fx.news_near(ev, "USOIL", t, 30, ["High"]))
        self.assertFalse(fx.news_near(ev, "XAUUSD", t, 30, ["High"]))
        self.assertFalse(fx.news_near(ev, "EURGBP", t, 30, ["High"]))


if __name__ == "__main__":
    unittest.main()
