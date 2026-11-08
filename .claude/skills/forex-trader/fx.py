#!/usr/bin/env python3
"""forex-trader: scan, plan, size, risk-check and replay forex day trades.

Standard library only (Python 3.9+, for zoneinfo). Free data: Yahoo Finance
chart JSON for prices, the FairEconomy (ForexFactory) weekly calendar for
news. It never places an order and never talks to a broker: the end of the
chain is a ticket you type into a DEMO account yourself.

    python3 fx.py init                         # -> forex/config.json (demo)
    python3 fx.py sessions                     # market open? which sessions?
    python3 fx.py calendar --hours 48          # high-impact news ahead
    python3 fx.py scan daily                   # trend / breakout / RSI per pair
    python3 fx.py scan session --range asia    # London breakout of the Asian range
    python3 fx.py plan EURUSD --side long --write
    python3 fx.py replay --write               # 10,000 replays + past days
    python3 fx.py size --write                 # lots for your risk -> order.json
    python3 fx.py risk                         # PASS / BLOCK
    python3 fx.py journal add                  # you opened it on demo: count it
"""

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("fx.py needs Python 3.9 or newer (zoneinfo).")

UTC = dt.timezone.utc
NY = ZoneInfo("America/New_York")
LON = ZoneInfo("Europe/London")
TOKYO = ZoneInfo("Asia/Tokyo")
SYDNEY = ZoneInfo("Australia/Sydney")
UA = "Mozilla/5.0 (forex-trader skill; stdlib urllib)"
CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
LOT = 100000
STEP = 300  # 5-minute bars

CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD")
DEFAULT_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
                 "EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "EURCHF", "EURAUD", "GBPCHF", "CADJPY",
                 "XAUUSD", "XAGUSD", "USOIL", "UKOIL"]

# Metals and oil, as most CFD brokers quote them. Yahoo has no free spot
# feed for these, so prices come from the front-month COMEX/NYMEX future,
# which sits a little away from a broker's spot price (and jumps at a roll).
# pip: the move this tool calls one pip; contract: units in one lot.
INSTRUMENTS = {
    "XAUUSD": {"name": "Gold", "yahoo": "GC=F", "pip": 0.1, "digits": 2, "contract": 100, "unit": "oz"},
    "XAGUSD": {"name": "Silver", "yahoo": "SI=F", "pip": 0.01, "digits": 3, "contract": 5000, "unit": "oz"},
    "USOIL": {"name": "WTI oil", "yahoo": "CL=F", "pip": 0.01, "digits": 2, "contract": 1000, "unit": "barrels", "oil": True},
    "UKOIL": {"name": "Brent oil", "yahoo": "BZ=F", "pip": 0.01, "digits": 2, "contract": 1000, "unit": "barrels", "oil": True},
}
ALIASES = {"GOLD": "XAUUSD", "XAU": "XAUUSD", "GC": "XAUUSD", "SILVER": "XAGUSD", "XAG": "XAGUSD", "SI": "XAGUSD",
           "OIL": "USOIL", "WTI": "USOIL", "CRUDE": "USOIL", "CL": "USOIL", "XTIUSD": "USOIL", "WTIUSD": "USOIL",
           "BRENT": "UKOIL", "BZ": "UKOIL", "XBRUSD": "UKOIL", "BRENTUSD": "UKOIL",
           "\u0630\u0647\u0628": "XAUUSD", "\u0641\u0636\u0629": "XAGUSD", "\u0646\u0641\u0637": "USOIL", "\u0628\u0631\u0646\u062a": "UKOIL"}

DEFAULT_CONFIG = {
    "account_currency": "USD",
    "balance": 1000,
    "demo": True,
    "risk_pct_per_trade": 1.0,
    "max_daily_loss_pct": 3.0,
    "max_trades_per_day": 3,
    "min_reward_risk": 1.5,
    "max_leverage": 10,
    "broker_leverage": 30,
    "min_lot": 0.01,
    "lot_step": 0.01,
    "news_buffer_minutes": 30,
    "news_impact": ["High"],
    "no_entries_friday_after_ny": "15:00",
    "rollover_ny": ["16:55", "17:10"],
    "spread_pips": {"default": 2.0, "EURUSD": 1.0, "USDJPY": 1.0, "GBPUSD": 1.2,
                    "AUDUSD": 1.2, "USDCAD": 1.5, "USDCHF": 1.5, "NZDUSD": 1.8,
                    "EURGBP": 1.5, "EURJPY": 1.8,
                    "XAUUSD": 3.0, "XAGUSD": 3.0, "USOIL": 3.0, "UKOIL": 4.0},
    "contract_size": {},
    "pairs": DEFAULT_PAIRS,
    "blocked": [],
    "display_tz": "Asia/Kuwait",
}

# Session ranges: (zone, range start, range end, watch until, flat by)
RANGES = {
    "asia":   (LON, "00:00", "07:00", "12:00", "16:00"),
    "london": (LON, "08:00", "08:30", "12:00", "16:00"),
    "ny":     (NY,  "08:00", "08:30", "12:00", "16:00"),
}
SESSIONS = [("Sydney", SYDNEY, "07:00", "16:00"), ("Tokyo", TOKYO, "09:00", "18:00"),
            ("London", LON, "08:00", "17:00"), ("New York", NY, "08:00", "17:00")]


class FxError(Exception):
    """A plain one-line reason the command cannot go on."""


# ----------------------------------------------------------------- basics

def norm_pair(s):
    raw = str(s).strip()
    p = raw.upper().replace("/", "").replace("=X", "").replace("_", "").replace("-", "").replace(" ", "")
    p = ALIASES.get(p, ALIASES.get(raw, p))
    if p in INSTRUMENTS:
        return p
    if len(p) != 6 or p[:3] not in CURRENCIES or p[3:] not in CURRENCIES or p[:3] == p[3:]:
        raise FxError("%r is not something this skill knows: two of %s (like EURUSD), or %s."
                      % (s, ", ".join(CURRENCIES), ", ".join(INSTRUMENTS)))
    return p


def is_cfd(pair):
    return pair in INSTRUMENTS


def pip_size(pair):
    if is_cfd(pair):
        return INSTRUMENTS[pair]["pip"]
    return 0.01 if pair.endswith("JPY") else 0.0001


def digits(pair):
    if is_cfd(pair):
        return INSTRUMENTS[pair]["digits"]
    return 3 if pair.endswith("JPY") else 5


def yahoo(pair):
    return INSTRUMENTS[pair]["yahoo"] if is_cfd(pair) else pair + "=X"


def quote_ccy(pair):
    return "USD" if is_cfd(pair) else pair[3:]


def news_ccys(pair):
    return {"USD"} if is_cfd(pair) else {pair[:3], pair[3:]}


def label(pair):
    if is_cfd(pair):
        return "%s (%s)" % (INSTRUMENTS[pair]["name"], pair)
    return "%s/%s" % (pair[:3], pair[3:])


def contract(pair, cfg=None):
    over = ((cfg or {}).get("contract_size") or {}).get(pair)
    if over:
        return float(over)
    return float(INSTRUMENTS[pair]["contract"]) if is_cfd(pair) else float(LOT)


def fmt(pair, x):
    return ("%%.%df" % digits(pair)) % x


def round_price(pair, x, mode):
    q = Decimal(1).scaleb(-digits(pair))
    # round off float noise first, so 90.84 + 0.87 does not ceil to 91.72
    return float(Decimal(repr(round(x, digits(pair) + 4))).quantize(q, rounding=mode))


def pips(pair, a, b):
    return round(abs(a - b) / pip_size(pair), 1)


def hm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def at(day, hhmm, tz):
    return dt.datetime.combine(day, hm(hhmm), tzinfo=tz)


def parse_now(s, tz):
    if not s:
        return dt.datetime.now(UTC)
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=tz)
    return t.astimezone(UTC)


def fx_day(t):
    """The forex trading day rolls over at 17:00 New York."""
    return (t.astimezone(NY) + dt.timedelta(hours=7)).date()


def market_open(t, pair=None):
    """Forex: Sun 17:00 to Fri 17:00 New York. Metals and oil (CME Globex):
    Sun 18:00 to Fri 17:00, with a daily break from 17:00 to 18:00."""
    n = t.astimezone(NY)
    wd = n.weekday()  # Mon 0 .. Sun 6
    sunday_open = dt.time(18, 0) if pair and is_cfd(pair) else dt.time(17, 0)
    if wd == 5:
        return False
    if wd == 4 and n.time() >= dt.time(17, 0):
        return False
    if wd == 6 and n.time() < sunday_open:
        return False
    if pair and is_cfd(pair) and dt.time(17, 0) <= n.time() < dt.time(18, 0):
        return False
    return True


def next_open(t):
    n = t.astimezone(NY)
    while True:
        if n.weekday() == 6:
            o = at(n.date(), "17:00", NY)
            if o > t:
                return o
        n = (n + dt.timedelta(days=1)).replace(hour=0, minute=0)


# ----------------------------------------------------------------- files

class Store:
    def __init__(self, root):
        self.root = root

    def path(self, name):
        return os.path.join(self.root, name)

    def read(self, name, default=None):
        try:
            with open(self.path(name)) as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except ValueError:
            raise FxError("%s is not valid JSON; fix or delete it." % self.path(name))

    def write(self, name, data):
        os.makedirs(self.root, exist_ok=True)
        tmp = self.path(name) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, self.path(name))
        return self.path(name)


def load_config(store):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    user = store.read("config.json", {}) or {}
    for k, v in user.items():
        if k == "spread_pips" and isinstance(v, dict):
            cfg["spread_pips"].update(v)
        else:
            cfg[k] = v
    cfg["account_currency"] = str(cfg["account_currency"]).upper()
    return cfg


def spread_of(cfg, pair):
    sp = cfg.get("spread_pips", {})
    return float(sp.get(pair, sp.get("default", 2.0)))


def local(cfg, t):
    return t.astimezone(ZoneInfo(cfg.get("display_tz", "Asia/Kuwait")))


def tzlabel(cfg):
    z = cfg.get("display_tz", "Asia/Kuwait")
    return {"Asia/Kuwait": "Kuwait"}.get(z, z)


def show_t(cfg, t, with_day=False):
    lt = local(cfg, t)
    return lt.strftime("%a %d %b %H:%M" if with_day else "%H:%M")


# ----------------------------------------------------------------- data

def _open_json(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as f:
        return json.load(f)


class Data:
    """Yahoo chart JSON, live or from saved files (--offline)."""

    def __init__(self, offline=None, pause=0.25):
        self.offline = None
        self.pause = pause
        if offline:
            d = _open_json(offline)
            self.offline = d if "chart" not in d else {"*": d}

    def chart(self, symbol, rng, interval):
        if self.offline is not None:
            key = "%s|%s" % (symbol, interval)
            for k in (key, symbol, "*"):
                if k in self.offline:
                    return self.offline[k]
            raise FxError("no saved chart for %s %s in the offline file." % (symbol, interval))
        path = "/v8/finance/chart/%s?range=%s&interval=%s" % (symbol, rng, interval)
        last = None
        for host in YAHOO_HOSTS:
            for attempt in (0, 1):
                try:
                    req = urllib.request.Request(host + path, headers={"User-Agent": UA, "Accept": "application/json"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        out = json.load(r)
                    time.sleep(self.pause)
                    return out
                except urllib.error.HTTPError as e:
                    last = "HTTP %s" % e.code
                    if e.code == 429 and attempt == 0:
                        time.sleep(5)
                        continue
                    if e.code == 404:
                        raise FxError("Yahoo has no chart for %s." % symbol)
                    break
                except (urllib.error.URLError, OSError, ValueError) as e:
                    last = str(getattr(e, "reason", e))
                    break
        raise FxError("could not reach Yahoo Finance for %s (%s)." % (symbol, last))


def bars_from(chart, step, now=None):
    """[(ts, o, h, l, c)] on the step grid, nulls dropped, completed bars only."""
    try:
        res = chart["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        raise FxError("Yahoo returned no chart data.")
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    o, h, l, c = (q.get(k) or [] for k in ("open", "high", "low", "close"))
    cut = now.timestamp() if now else None
    out = []
    for i, t in enumerate(ts):
        try:
            row = (o[i], h[i], l[i], c[i])
        except IndexError:
            continue
        if None in row:
            continue
        if step < 86400 and t % step:
            continue
        if cut is not None and t + step > cut:
            continue
        out.append((int(t), float(row[0]), float(max(row)), float(min(row)), float(row[3])))
    return out


def last_price(data, symbol):
    ch = data.chart(symbol, "5d", "1d")
    try:
        p = ch["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except (KeyError, IndexError, TypeError):
        p = None
    if not p:
        b = bars_from(ch, 86400)
        if not b:
            raise FxError("no price for %s." % symbol)
        p = b[-1][4]
    return float(p)


def quote_rate(data, pair, acct, override=None):
    """How many account-currency units one unit of the quote currency is worth."""
    q = quote_ccy(pair)
    if override:
        return float(override)
    if q == acct:
        return 1.0
    return last_price(data, "%s%s=X" % (q, acct))


# ----------------------------------------------------------------- indicators

def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def atr(bars, n=14):
    if len(bars) < n + 1:
        return None
    trs = [max(b[2] - b[3], abs(b[2] - p[4]), abs(b[3] - p[4])) for p, b in zip(bars, bars[1:])]
    a = sum(trs[:n]) / n
    for x in trs[n:]:
        a = (a * (n - 1) + x) / n
    return a


def daily_setups(pair, bars):
    c = [b[4] for b in bars]
    if len(c) < 51:
        return None
    last, prev = c[-1], c[-2]
    ma20, ma20p, ma50 = sma(c, 20), sma(c[:-1], 20), sma(c, 50)
    r = rsi(c)
    a = atr(bars)
    s = []
    if last > max(c[-21:-1]):
        s.append(("breakout_20d", "long"))
    if last < min(c[-21:-1]):
        s.append(("breakdown_20d", "short"))
    if prev < ma20p and last > ma20:
        s.append(("reclaim_ma20", "long"))
    if prev > ma20p and last < ma20:
        s.append(("lost_ma20", "short"))
    if r is not None and r < 30:
        s.append(("oversold", "long"))
    if r is not None and r > 70:
        s.append(("overbought", "short"))
    trend = "up" if ma20 > ma50 and last > ma50 else "down" if ma20 < ma50 and last < ma50 else "sideways"
    return {"pair": pair, "price": last, "change_pct": round((last / prev - 1) * 100, 2),
            "rsi": round(r, 1) if r is not None else None,
            "atr_pips": round(a / pip_size(pair), 1) if a else None, "trend": trend,
            "close_date": dt.datetime.fromtimestamp(bars[-1][0], UTC).strftime("%Y-%m-%d"),
            "setups": [x[0] for x in s], "bias": sorted(set(x[1] for x in s))}


def session_day(rtype, now):
    tz = RANGES[rtype][0]
    return now.astimezone(tz).date()


def session_breakout(pair, bars, rtype, day, now):
    """First completed 5-minute close beyond the session range, long or short."""
    tz, r0, r1, w1, flat = RANGES[rtype]
    rs, re_, we = at(day, r0, tz), at(day, r1, tz), at(day, w1, tz)
    out = {"pair": pair, "range": rtype, "day": day.isoformat(),
           "range_start": rs.isoformat(), "range_end": re_.isoformat(),
           "watch_until": we.isoformat(), "flat_by": at(day, flat, tz).isoformat()}
    if now < re_ + dt.timedelta(seconds=STEP):
        out["status"] = "not_yet"
        return out
    a, b, cut = rs.timestamp(), re_.timestamp(), now.timestamp()
    rng = [x for x in bars if a <= x[0] < b]
    need = int((b - a) // STEP)
    if len(rng) < max(1, int(need * 0.9)):
        out.update(status="no_data", reason="%d of %d range bars" % (len(rng), need))
        return out
    hi, lo = max(x[2] for x in rng), min(x[3] for x in rng)
    out.update(range_high=hi, range_low=lo, range_pips=pips(pair, hi, lo))
    for x in bars:
        if x[0] < b or x[0] >= we.timestamp() or x[0] + STEP > cut:
            continue
        if x[4] > hi or x[4] < lo:
            side = "long" if x[4] > hi else "short"
            edge = hi if side == "long" else lo
            out.update(status=side, side=side, trigger_price=x[4],
                       trigger_time=dt.datetime.fromtimestamp(x[0] + STEP, UTC).isoformat(),
                       beyond_pips=pips(pair, x[4], edge))
            return out
    out["status"] = "expired" if now >= we else "waiting"
    last = [x for x in bars if x[0] + STEP <= cut]
    if last:
        p = last[-1][4]
        out.update(last_price=p, to_high_pips=pips(pair, hi, p) if p <= hi else 0.0,
                   to_low_pips=pips(pair, p, lo) if p >= lo else 0.0)
    return out


# ----------------------------------------------------------------- plan

def make_plan(pair, side, entry, stop_rule, target_rule, cfg, rng=None, atr_val=None):
    if side not in ("long", "short"):
        raise FxError("side must be long or short.")
    sgn = 1 if side == "long" else -1
    kind, _, arg = stop_rule.partition(":")
    if kind in ("range-mid", "range-far"):
        if not rng:
            raise FxError("stop %s needs a session range: run scan session first or give --stop pips:N." % kind)
        hi, lo = rng
        stop = (hi + lo) / 2 if kind == "range-mid" else (lo if side == "long" else hi)
    elif kind == "pips":
        stop = entry - sgn * float(arg) * pip_size(pair)
    elif kind == "atr":
        if not atr_val:
            raise FxError("no ATR for %s; give --stop pips:N instead." % pair)
        stop = entry - sgn * float(arg or 1) * atr_val
    elif kind == "price":
        stop = float(arg)
    else:
        raise FxError("unknown stop %r (range-mid, range-far, pips:N, atr:K, price:X)." % stop_rule)
    stop = round_price(pair, stop, ROUND_FLOOR if side == "long" else ROUND_CEILING)
    risk = (entry - stop) * sgn
    if risk <= 0:
        raise FxError("the stop %s is on the wrong side of the entry %s for a %s." % (fmt(pair, stop), fmt(pair, entry), side))
    spread = spread_of(cfg, pair)
    stop_p = pips(pair, entry, stop)
    if stop_p < 3 * spread:
        raise FxError("a %.1f-pip stop is too tight for a %.1f-pip spread (needs at least %.1f)." % (stop_p, spread, 3 * spread))
    tk, _, targ = target_rule.partition(":")
    if tk.upper().endswith("R") and not targ:
        mult = float(tk[:-1] or 1)
        target = entry + sgn * mult * risk
    elif tk == "price":
        target = float(targ)
    elif tk == "pips":
        target = entry + sgn * float(targ) * pip_size(pair)
    else:
        raise FxError("unknown target %r (2R, 1.5R, pips:N, price:X)." % target_rule)
    target = round_price(pair, target, ROUND_FLOOR if side == "long" else ROUND_CEILING)
    reward = (target - entry) * sgn
    if reward <= 0:
        raise FxError("the target %s is on the wrong side of the entry for a %s." % (fmt(pair, target), side))
    return {"pair": pair, "side": side, "entry": entry, "stop": stop, "target": target,
            "stop_pips": stop_p, "target_pips": pips(pair, target, entry),
            "reward_risk": round(reward / risk, 2), "spread_pips": spread,
            "stop_rule": stop_rule, "target_rule": target_rule}


# ----------------------------------------------------------------- size

def size_order(plan, cfg, rate, balance=None, risk_pct=None, risk_amount=None):
    pair = plan["pair"]
    bal = float(balance if balance is not None else cfg["balance"])
    if bal <= 0:
        raise FxError("the balance must be above zero.")
    rp = float(risk_pct if risk_pct is not None else cfg["risk_pct_per_trade"])
    budget = float(risk_amount) if risk_amount is not None else bal * rp / 100
    spread = float(plan.get("spread_pips", spread_of(cfg, pair)))
    loss_pips = plan["stop_pips"] + spread
    pv_unit = pip_size(pair) * rate
    units_risk = budget / (loss_pips * pv_unit)
    units_lev = float(cfg["max_leverage"]) * bal / (plan["entry"] * rate)
    step, mn = float(cfg["lot_step"]), float(cfg["min_lot"])
    limit = "max loss per trade" if units_risk <= units_lev else "max leverage"
    units = min(units_risk, units_lev)
    size = contract(pair, cfg)
    lots = math.floor(units / size / step + 1e-9) * step
    lots = round(lots, 8)
    acct = cfg["account_currency"]
    if lots < mn:
        min_loss = mn * size * loss_pips * pv_unit
        raise FxError("the smallest lot (%.2f) would lose %.2f %s at the stop, over your %.2f %s (%s). "
                      "Use a closer stop or skip it." % (mn, min_loss, acct, budget, acct, limit))
    u = lots * size
    notional = u * plan["entry"] * rate
    order = dict(plan)
    order.update({
        "lots": round(lots, 2), "units": int(round(u)), "account_currency": acct, "balance": bal,
        "risk_budget": round(budget, 2), "decided_by": limit,
        "pip_value_per_lot": round(pip_size(pair) * size * rate, 4), "contract": size,
        "loss_at_stop": round(u * loss_pips * pv_unit, 2),
        "gain_at_target": round(u * (plan["target_pips"] - spread) * pv_unit, 2),
        "notional": round(notional, 2), "effective_leverage": round(notional / bal, 2),
        "margin": round(notional / float(cfg["broker_leverage"]), 2),
        "quote_rate": rate, "demo": bool(cfg.get("demo", True)),
    })
    order["fingerprint"] = fingerprint(order)
    return order


def fingerprint(o):
    key = "%s|%s|%.2f|%s|%s|%s" % (o["pair"], o["side"], o["lots"], o["entry"], o["stop"], o["target"])
    return hashlib.sha256(key.encode()).hexdigest()[:12]


# ----------------------------------------------------------------- calendar

def parse_calendar(raw):
    out = []
    for e in raw or []:
        try:
            t = dt.datetime.fromisoformat(e["date"]).astimezone(UTC)
        except (KeyError, ValueError, TypeError):
            continue
        out.append({"time": t.isoformat(), "currency": e.get("country", ""), "impact": e.get("impact", ""),
                    "title": e.get("title", ""), "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
    out.sort(key=lambda x: x["time"])
    return out


def fetch_calendar(store, now, offline=None, max_age_h=6):
    if offline:
        return parse_calendar(_open_json(offline)), "offline"
    cache = store.read("calendar.json")
    if cache:
        age = now - dt.datetime.fromisoformat(cache["fetched_at"])
        if age < dt.timedelta(hours=max_age_h) and fx_week(now) == cache.get("week"):
            return cache["events"], "cache"
    try:
        req = urllib.request.Request(CAL_URL, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            ev = parse_calendar(json.load(r))
    except (urllib.error.URLError, OSError, ValueError) as e:
        if cache:
            return cache["events"], "stale cache (%s)" % getattr(e, "reason", e)
        raise FxError("could not read the news calendar (%s)." % getattr(e, "reason", e))
    store.write("calendar.json", {"fetched_at": now.isoformat(), "week": fx_week(now), "events": ev})
    return ev, "live"


def fx_week(t):
    n = t.astimezone(NY)
    sunday = n.date() - dt.timedelta(days=(n.weekday() + 1) % 7)
    return sunday.isoformat()


def news_near(events, pair, now, buffer_min, impacts):
    cur = news_ccys(pair)
    oil = is_cfd(pair) and INSTRUMENTS[pair].get("oil")
    lo, hi = now - dt.timedelta(minutes=buffer_min), now + dt.timedelta(minutes=buffer_min)
    hits = []
    for e in events:
        t = dt.datetime.fromisoformat(e["time"])
        oil_news = oil and any(w in e["title"].lower() for w in ("crude", "opec"))
        if (oil_news or (e["currency"] in cur and e["impact"] in impacts)) and lo <= t <= hi:
            hits.append(e)
    return hits


# ----------------------------------------------------------------- replay

def _days(bars, tz):
    d = {}
    for b in bars:
        d.setdefault(dt.datetime.fromtimestamp(b[0], tz).date(), []).append(b)
    return d


def _tod(ts, tz):
    t = dt.datetime.fromtimestamp(ts, tz)
    return t.hour * 60 + t.minute


def _walk(path, side, entry, stop, target):
    """path: [(h, l, c)]. Stop wins when a bar touches both (the cautious read)."""
    for h, l, c in path:
        if side == "long":
            if l <= stop:
                return "stop", None
            if h >= target:
                return "target", None
        else:
            if h >= stop:
                return "stop", None
            if l <= target:
                return "target", None
    return "open", path[-1][2] if path else entry


def replay(plan, bars, start, flat_by, sims=10000, seed=19, rtype=None):
    tz = RANGES.get(rtype or "", (LON,))[0]
    pair, side = plan["pair"], plan["side"]
    entry, stop, target = plan["entry"], plan["stop"], plan["target"]
    sgn = 1 if side == "long" else -1
    risk = (entry - stop) * sgn
    rr = plan["reward_risk"]
    today = start.astimezone(tz).date()
    by_day = _days(bars, tz)
    past = [d for d in sorted(by_day) if d < today and d.weekday() < 5]
    # relative bars per time-of-day slot: (h/pc-1, l/pc-1, c/pc-1)
    pool = {}
    for d in past:
        db = by_day[d]
        for p, b in zip(db, db[1:]):
            if b[0] - p[0] != STEP:
                continue
            pool.setdefault(_tod(b[0], tz), []).append((b[2] / p[4] - 1, b[3] / p[4] - 1, b[4] / p[4] - 1))
    first = int(math.ceil(start.timestamp() / STEP) * STEP)
    slots = [_tod(t, tz) for t in range(first, int(flat_by.timestamp()), STEP)]
    slots = [s for s in slots if pool.get(s)]
    if not slots:
        raise FxError("no past bars cover %s to %s; nothing to replay." % (start.isoformat(), flat_by.isoformat()))
    rnd = random.Random(seed)
    tally = {"target": 0, "stop": 0, "open": 0}
    rsum = 0.0
    closes = []
    for _ in range(sims):
        p = entry
        res, last = "open", entry
        for s in slots:
            rh, rl, rc = rnd.choice(pool[s])
            h, l, c = p * (1 + rh), p * (1 + rl), p * (1 + rc)
            if side == "long":
                if l <= stop:
                    res = "stop"
                    break
                if h >= target:
                    res = "target"
                    break
            else:
                if h >= stop:
                    res = "stop"
                    break
                if l <= target:
                    res = "target"
                    break
            p = c
        tally[res] += 1
        if res == "target":
            rsum += rr
        elif res == "stop":
            rsum -= 1
        else:
            r = (p - entry) * sgn / risk
            rsum += r
            closes.append(r)
    out = {"sims": sims, "seed": seed, "slots": len(slots), "days_in_pool": len(past),
           "target_pct": round(100.0 * tally["target"] / sims, 1),
           "stop_pct": round(100.0 * tally["stop"] / sims, 1),
           "open_pct": round(100.0 * tally["open"] / sims, 1),
           "avg_r": round(rsum / sims, 3)}
    if closes:
        closes.sort()
        out["open_median_r"] = round(closes[len(closes) // 2], 2)
    out["history"] = history(plan, by_day, past, rtype) if rtype else None
    out["verdict"] = verdict(out)
    return out


def history(plan, by_day, past, rtype):
    """Re-run the same session rule on every past day."""
    tz, r0, r1, w1, flat = RANGES[rtype]
    pair, cfg = plan["pair"], {"spread_pips": {"default": plan.get("spread_pips", 0)}}
    fired = {"target": 0, "stop": 0, "open": 0}
    rows = []
    for d in past:
        db = by_day[d]
        end = at(d, flat, tz) + dt.timedelta(minutes=1)
        sb = session_breakout(pair, db, rtype, d, end)
        if sb.get("status") != plan["side"]:
            continue
        try:
            p = make_plan(pair, plan["side"], sb["trigger_price"], plan["stop_rule"], plan["target_rule"],
                          cfg, (sb["range_high"], sb["range_low"]))
        except FxError:
            continue
        t0 = dt.datetime.fromisoformat(sb["trigger_time"]).timestamp()
        t1 = at(d, flat, tz).timestamp()
        path = [(b[2], b[3], b[4]) for b in db if t0 <= b[0] < t1]
        res, _ = _walk(path, plan["side"], p["entry"], p["stop"], p["target"])
        fired[res] += 1
        rows.append({"day": d.isoformat(), "result": res})
    n = sum(fired.values())
    return {"days": len(past), "fired": n, **fired, "rows": rows}


def verdict(r):
    t, s, o, a = r["target_pct"], r["stop_pct"], r["open_pct"], r["avg_r"]
    if t > s + 10 and a > 0.15:
        v = "leans your way in the replays (target first %.0f%% vs stop first %.0f%%), a reality check, not a promise" % (t, s)
    elif s > t + 10:
        v = "the stop comes first more often than the target (%.0f%% vs %.0f%%)" % (s, t)
    elif t > s + 10:
        v = "the target comes first more often (%.0f%% vs %.0f%%), but the average R is thin" % (t, s)
    else:
        v = "close to a coin flip (target %.0f%%, stop %.0f%%)" % (t, s)
    if o >= 50:
        v += "; most replays (%.0f%%) hit neither by the flat-by time, so the target may be far for the time left" % o
    h = r.get("history")
    if h:
        if h["fired"] < 10:
            v += "; the history is thin (%d past days fired)" % h["fired"]
        elif h["target"] + h["stop"]:
            v += "; on real past days it hit the target first %d times and the stop first %d" % (h["target"], h["stop"])
    return v


# ----------------------------------------------------------------- commands

def cmd_init(a, store, cfg):
    if store.read("config.json") is not None and not a.force:
        print("%s already exists; edit it, or --force to reset it." % store.path("config.json"))
        return 0
    c = json.loads(json.dumps(DEFAULT_CONFIG))
    if a.balance:
        c["balance"] = a.balance
    if a.currency:
        c["account_currency"] = a.currency.upper()
    print("Wrote %s (demo: true, risk %.1f%% a trade, balance %s %s)." % (
        store.write("config.json", c), c["risk_pct_per_trade"], c["balance"], c["account_currency"]))
    return 0


def cmd_sessions(a, store, cfg):
    now = parse_now(a.now, ZoneInfo(cfg["display_tz"]))
    lab = tzlabel(cfg)
    print("Now %s %s (%s New York)." % (show_t(cfg, now, True), lab, now.astimezone(NY).strftime("%H:%M")))
    if not market_open(now):
        o = next_open(now)
        print("The forex market is closed for the weekend. It opens %s %s (Sun 17:00 New York)." % (show_t(cfg, o, True), lab))
        return 1
    rows, active = [], []
    for name, tz, s, e in SESSIONS:
        d = now.astimezone(tz).date()
        cands = []
        for k in (-1, 0, 1):
            dd = d + dt.timedelta(days=k)
            if dd.weekday() < 5:
                cands.append((at(dd, s, tz), at(dd, e, tz)))
        on = any(o <= now < c for o, c in cands)
        nxt = min((o for o, c in cands if o > now), default=None)
        if on:
            active.append(name)
            c = [c for o, c in cands if o <= now < c][0]
            rows.append("  %-9s open   until %s %s" % (name, show_t(cfg, c), lab))
        else:
            rows.append("  %-9s closed%s" % (name, ("  opens %s %s" % (show_t(cfg, nxt, True), lab)) if nxt else ""))
    print("Market open. Sessions now: %s." % (", ".join(active) or "none (a quiet gap)"))
    print("\n".join(rows))
    if "London" in active and "New York" in active:
        print("London and New York overlap: usually the busiest hours.")
    return 0


def cmd_calendar(a, store, cfg):
    now = parse_now(a.now, ZoneInfo(cfg["display_tz"]))
    ev, src = fetch_calendar(store, now, a.offline)
    imp = [x.strip().title() for x in a.impact.split(",")]
    cur, oil = set(), False
    for p in (a.pairs.split(",") if a.pairs else cfg["pairs"]):
        p = norm_pair(p)
        cur |= news_ccys(p)
        oil = oil or (is_cfd(p) and INSTRUMENTS[p].get("oil"))
    hi = now + dt.timedelta(hours=a.hours)
    sel = [e for e in ev if ((e["impact"] in imp and e["currency"] in cur)
                             or (oil and any(w in e["title"].lower() for w in ("crude", "opec"))))
           and now - dt.timedelta(minutes=30) <= dt.datetime.fromisoformat(e["time"]) <= hi]
    print("News calendar (%s, ForexFactory week %s): %d %s events for %s in the next %d hours."
          % (src, fx_week(now), len(sel), "/".join(imp), ",".join(sorted(cur)), a.hours))
    for e in sel:
        t = dt.datetime.fromisoformat(e["time"])
        extra = "  forecast %s, previous %s" % (e["forecast"] or "-", e["previous"] or "-") if (e["forecast"] or e["previous"]) else ""
        print("  %s %s  %-3s %-6s %s%s" % (show_t(cfg, t, True), tzlabel(cfg), e["currency"], e["impact"], e["title"], extra))
    if hi.date() > dt.date.fromisoformat(fx_week(now)) + dt.timedelta(days=6):
        print("The free feed covers this week only; events after Saturday are not in it yet.")
    return 0 if sel else 1


def _pairs(a, cfg):
    return [norm_pair(p) for p in (a.pairs.split(",") if a.pairs else cfg["pairs"])]


def cmd_scan(a, store, cfg):
    data = Data(a.offline)
    now = parse_now(a.now, ZoneInfo(cfg["display_tz"]))
    pairs = _pairs(a, cfg)
    if a.mode == "daily":
        hits, failed = [], []
        for p in pairs:
            try:
                b = bars_from(data.chart(yahoo(p), "1y", "1d"), 86400, now)
                r = daily_setups(p, b)
            except FxError as e:
                failed.append(p)
                continue
            if r is None:
                failed.append(p)
                continue
            hits.append(r)
        if not hits:
            raise FxError("no daily charts came back (%d failed)." % len(failed))
        last = max(h["close_date"] for h in hits)
        print("Daily scan of %d pairs, closes through %s (Yahoo, daily candles). %d came back%s."
              % (len(pairs), last, len(hits), (", failed: " + ",".join(failed)) if failed else ""))
        m = [h for h in hits if h["setups"]]
        print("%d matched a rule:" % len(m))
        for h in sorted(hits, key=lambda x: (-len(x["setups"]), x["pair"])):
            print("  %-7s %-9s %+6.2f%%  RSI %5s  ATR %5s pips  trend %-8s %s" % (
                h["pair"], fmt(h["pair"], h["price"]), h["change_pct"], h["rsi"], h["atr_pips"], h["trend"],
                ", ".join(h["setups"]) or "-"))
        if not a.no_write:
            store.write("watchlist.json", {"mode": "daily", "as_of": now.isoformat(), "hits": hits, "failed": failed})
            print("Wrote %s." % store.path("watchlist.json"))
        return 0 if m else 1
    # session
    if not market_open(now):
        print("The forex market is closed (weekend); a session scan needs a trading day. Opens %s %s."
              % (show_t(cfg, next_open(now), True), tzlabel(cfg)))
        return 1
    rtype = a.range
    day = session_day(rtype, now)
    if day.weekday() >= 5:
        print("No %s session today." % rtype)
        return 1
    if a.at:
        tz = RANGES[rtype][0]
        now = min(now, at(day, a.at, tz))
    rows, failed = [], []
    for p in pairs:
        try:
            b = bars_from(data.chart(yahoo(p), "5d", "5m"), STEP, now)
            rows.append(session_breakout(p, b, rtype, day, now))
        except FxError:
            failed.append(p)
    tz, r0, r1, w1, flat = RANGES[rtype]
    rs, re_, we = at(day, r0, tz), at(day, r1, tz), at(day, w1, tz)
    print("Session scan %s, %s range %s-%s %s, watching until %s %s; bars up to %s %s." % (
        day.isoformat(), rtype, show_t(cfg, rs), show_t(cfg, re_), tzlabel(cfg), show_t(cfg, we), tzlabel(cfg),
        show_t(cfg, now), tzlabel(cfg)))
    if rows and all(r["status"] == "not_yet" for r in rows):
        print("The range is not finished yet; run again after %s %s." % (show_t(cfg, re_ + dt.timedelta(seconds=STEP)), tzlabel(cfg)))
        return 1
    hits = [r for r in rows if r["status"] in ("long", "short")]
    print("%d of %d pairs closed beyond their range%s:" % (len(hits), len(rows), (" (failed: %s)" % ",".join(failed)) if failed else ""))
    for r in sorted(hits, key=lambda x: x["trigger_time"]):
        t = dt.datetime.fromisoformat(r["trigger_time"])
        print("  %s %s  %-7s %-5s at %s  range %s-%s (%.1f pips), %.1f pips beyond" % (
            show_t(cfg, t), tzlabel(cfg), r["pair"], r["side"], fmt(r["pair"], r["trigger_price"]),
            fmt(r["pair"], r["range_low"]), fmt(r["pair"], r["range_high"]), r["range_pips"], r["beyond_pips"]))
    for r in rows:
        if r["status"] == "waiting":
            print("  waiting %-7s range %s-%s, %.1f pips to the high, %.1f to the low" % (
                r["pair"], fmt(r["pair"], r["range_low"]), fmt(r["pair"], r["range_high"]), r["to_high_pips"], r["to_low_pips"]))
    if not a.no_write:
        store.write("watchlist.json", {"mode": "session", "range": rtype, "as_of": now.isoformat(),
                                       "hits": hits, "all": rows, "failed": failed})
        print("Wrote %s." % store.path("watchlist.json"))
    return 0 if hits else 1


def cmd_plan(a, store, cfg):
    pair = norm_pair(a.pair)
    wl = store.read("watchlist.json", {}) or {}
    hit = next((h for h in wl.get("hits", []) if h.get("pair") == pair), None)
    rng, rtype = None, None
    if hit and wl.get("mode") == "session":
        rng, rtype = (hit["range_high"], hit["range_low"]), wl.get("range")
    side = a.side or (hit or {}).get("side")
    if not side:
        raise FxError("say --side long or --side short.")
    entry = a.entry or ((hit or {}).get("trigger_price") if hit and hit.get("side") == side else None)
    data = Data(a.offline)
    atr_val = None
    if not entry or a.stop.startswith("atr"):
        b = bars_from(data.chart(yahoo(pair), "6mo", "1d"), 86400)
        atr_val = atr(b)
        if not entry:
            entry = last_price(data, yahoo(pair))
    stop_rule = a.stop or ("range-mid" if rng else "atr:1")
    plan = make_plan(pair, side, float(entry), stop_rule, a.target, cfg, rng, atr_val)
    if rtype and hit:
        plan.update(range=rtype, range_high=rng[0], range_low=rng[1], day=hit["day"],
                    entry_time=hit.get("trigger_time"), flat_by=hit["flat_by"])
    print("Plan %s %s: entry %s, stop %s (%.1f pips), target %s (%.1f pips), reward:risk %.2f, spread %.1f pips." % (
        pair, side, fmt(pair, plan["entry"]), fmt(pair, plan["stop"]), plan["stop_pips"],
        fmt(pair, plan["target"]), plan["target_pips"], plan["reward_risk"], plan["spread_pips"]))
    if plan.get("flat_by"):
        print("Flat by %s %s either way." % (show_t(cfg, dt.datetime.fromisoformat(plan["flat_by"])), tzlabel(cfg)))
    if plan["reward_risk"] < float(cfg["min_reward_risk"]):
        print("Note: reward:risk %.2f is under your minimum %.2f; the risk check will block it." % (plan["reward_risk"], cfg["min_reward_risk"]))
    if a.write:
        print("Wrote %s. Next: replay it, then size it." % store.write("plan.json", plan))
    return 0


def cmd_replay(a, store, cfg):
    plan = store.read("plan.json")
    if not plan:
        raise FxError("no forex/plan.json; run plan --write first.")
    data = Data(a.offline)
    now = parse_now(a.now, ZoneInfo(cfg["display_tz"]))
    pair = plan["pair"]
    bars = bars_from(data.chart(yahoo(pair), "60d", "5m"), STEP, now)
    rtype = plan.get("range")
    if plan.get("entry_time"):
        start = dt.datetime.fromisoformat(plan["entry_time"])
    else:
        start = now
    if plan.get("flat_by"):
        flat = dt.datetime.fromisoformat(plan["flat_by"])
    else:
        flat = at(start.astimezone(LON).date(), "16:00", LON)
        if flat <= start:
            flat = start + dt.timedelta(hours=a.hours)
    r = replay(plan, bars, start, flat, a.sims, a.seed, rtype)
    print("Time machine, %s %s from %s to %s %s: %d replays (seed %d) from %d past days, %d five-minute steps." % (
        pair, plan["side"], show_t(cfg, start), show_t(cfg, flat), tzlabel(cfg), r["sims"], r["seed"],
        r["days_in_pool"], r["slots"]))
    print("  target first %.1f%%, stop first %.1f%%, still open at the end %.1f%%; average %.2f R (stop counts first on a two-touch bar)." % (
        r["target_pct"], r["stop_pct"], r["open_pct"], r["avg_r"]))
    h = r.get("history")
    if h:
        print("  history: the same %s rule fired %s on %d of %d past days: %d target, %d stop, %d neither." % (
            rtype, plan["side"], h["fired"], h["days"], h["target"], h["stop"], h["open"]))
    elif not rtype:
        print("  history: skipped (the plan did not come from a session range).")
    print("Verdict: %s." % r["verdict"])
    print("A simulation built from the last 60 days, not a prediction: news, gaps and spikes can do what those days never did.")
    if a.write:
        rr = dict(r)
        if rr.get("history"):
            rr["history"] = {k: v for k, v in rr["history"].items()}
        store.write("replay.json", rr)
        print("Wrote %s." % store.path("replay.json"))
    return 0


def cmd_size(a, store, cfg):
    plan = store.read("plan.json")
    if not plan:
        raise FxError("no forex/plan.json; run plan --write first.")
    data = Data(a.offline)
    rate = quote_rate(data, plan["pair"], cfg["account_currency"], a.quote_rate)
    o = size_order(plan, cfg, rate, a.balance, a.risk_pct, a.risk_amount)
    acct = o["account_currency"]
    pair = o["pair"]
    unit = INSTRUMENTS[pair]["unit"] if is_cfd(pair) else "units"
    print("Size: %.2f lots %s %s (%s %s; one lot = %s %s), decided by your %s." % (
        o["lots"], o["side"], label(pair), format(o["units"], ","), unit, format(int(o["contract"]), ","), unit, o["decided_by"]))
    print("  entry %s, stop %s, target %s; pip value %.2f %s a lot." % (
        fmt(pair, o["entry"]), fmt(pair, o["stop"]), fmt(pair, o["target"]), o["pip_value_per_lot"], acct))
    print("  loss at the stop %.2f %s (spread included, budget %.2f), gain at the target %.2f %s." % (
        o["loss_at_stop"], acct, o["risk_budget"], o["gain_at_target"], acct))
    print("  notional %s %s, leverage %.1fx, margin about %.2f %s at %s:1." % (
        format(o["notional"], ",.0f"), acct, o["effective_leverage"], o["margin"], acct, cfg["broker_leverage"]))
    print("  %s. Nothing is placed." % ("DEMO account" if o["demo"] else "WARNING: demo is off in your config"))
    if a.write:
        print("Wrote %s. Next: risk." % store.write("order.json", o))
    return 0


def risk_check(o, cfg, journal, events, now, skip_news=False):
    """[(ok, line)] for every rule, and the overall verdict."""
    checks = []
    pair, side = o["pair"], o["side"]
    sgn = 1 if side == "long" else -1

    def ck(ok, msg):
        checks.append((bool(ok), msg))

    ck(pair not in [norm_pair(x) for x in cfg.get("blocked", [])], "pair not on your blocked list")
    ck((o["entry"] - o["stop"]) * sgn > 0, "stop on the right side of the entry")
    ck((o["target"] - o["entry"]) * sgn > 0, "target on the right side of the entry")
    ck(o["reward_risk"] >= float(cfg["min_reward_risk"]), "reward:risk %.2f >= %.2f" % (o["reward_risk"], float(cfg["min_reward_risk"])))
    bal = float(cfg["balance"])
    cap = bal * float(cfg["risk_pct_per_trade"]) / 100
    ck(o["loss_at_stop"] <= cap * 1.0001, "loss at stop %.2f <= %.2f (%.1f%% of %.0f)" % (o["loss_at_stop"], cap, float(cfg["risk_pct_per_trade"]), bal))
    day = fx_day(now).isoformat()
    today = [j for j in journal if j.get("fx_day") == day]
    used = sum(float(j.get("loss_at_stop", 0)) for j in today)
    dcap = bal * float(cfg["max_daily_loss_pct"]) / 100
    ck(used + o["loss_at_stop"] <= dcap * 1.0001, "today's risk %.2f + %.2f <= daily limit %.2f" % (used, o["loss_at_stop"], dcap))
    ck(len(today) < int(cfg["max_trades_per_day"]), "trades today %d < %d" % (len(today), int(cfg["max_trades_per_day"])))
    ck(o["effective_leverage"] <= float(cfg["max_leverage"]) * 1.0001, "leverage %.1fx <= %sx" % (o["effective_leverage"], cfg["max_leverage"]))
    ck(o["lots"] >= float(cfg["min_lot"]), "at least the minimum lot")
    ck(market_open(now, pair), "the market is open for %s" % pair)
    n = now.astimezone(NY)
    fri = n.weekday() == 4 and n.time() >= hm(cfg["no_entries_friday_after_ny"])
    ck(not fri, "not Friday after %s New York (weekend gap)" % cfg["no_entries_friday_after_ny"])
    r0, r1 = cfg["rollover_ny"]
    ck(not (hm(r0) <= n.time() < hm(r1)), "outside the %s-%s New York rollover (wide spreads)" % (r0, r1))
    if skip_news:
        ck(True, "news check SKIPPED by you")
    elif events is None:
        ck(False, "news calendar could not be read")
    else:
        near = news_near(events, pair, now, int(cfg["news_buffer_minutes"]), cfg["news_impact"])
        if near:
            e = near[0]
            ck(False, "%s news within %d min: %s %s at %s UTC" % (e["impact"], int(cfg["news_buffer_minutes"]), e["currency"], e["title"], e["time"][11:16]))
        else:
            ck(True, "no %s news for %s within %d min" % ("/".join(cfg["news_impact"]) + (" or crude/OPEC" if is_cfd(pair) and INSTRUMENTS[pair].get("oil") else ""), "/".join(sorted(news_ccys(pair))), int(cfg["news_buffer_minutes"])))
    return checks, all(ok for ok, _ in checks)


def cmd_risk(a, store, cfg):
    o = store.read("order.json")
    if not o:
        raise FxError("no forex/order.json; run size --write first.")
    now = parse_now(a.now, ZoneInfo(cfg["display_tz"]))
    events = None
    if not a.skip_news:
        try:
            events, _ = fetch_calendar(store, now, a.calendar)
        except FxError as e:
            print("Calendar: %s" % e)
    checks, ok = risk_check(o, cfg, store.read("journal.json", []) or [], events, now, a.skip_news)
    for good, msg in checks:
        print("  %s  %s" % ("ok  " if good else "FAIL", msg))
    bad = [m for g, m in checks if not g]
    print("%s %.2f lots %s %s%s" % ("PASS" if ok else "BLOCK", o["lots"], o["side"], o["pair"], "" if ok else ": " + "; ".join(bad)))
    store.write("risk.json", {"fingerprint": o["fingerprint"], "verdict": "PASS" if ok else "BLOCK",
                              "reasons": bad, "checked_at": now.isoformat(), "fx_day": fx_day(now).isoformat()})
    if ok:
        print("Ticket for your DEMO account (type it in yourself; this skill never sends orders):")
        print(ticket(o, cfg))
    return 0 if ok else 1


def ticket(o, cfg):
    p = o["pair"]
    return "\n".join([
        "  %s %.2f lots %s at market (about %s)" % ("BUY" if o["side"] == "long" else "SELL", o["lots"], label(p), fmt(p, o["entry"])),
        "  stop-loss   %s  (%.1f pips = %s away, about -%.2f %s)" % (fmt(p, o["stop"]), o["stop_pips"], fmt(p, abs(o["entry"] - o["stop"])), o["loss_at_stop"], o["account_currency"]),
        "  take-profit %s  (%.1f pips = %s away)" % (fmt(p, o["target"]), o["target_pips"], fmt(p, abs(o["target"] - o["entry"]))),
        ("  prices here are from the %s future; your broker's %s price differs a little, so set the stop and target by the distances above from your own entry"
         % (INSTRUMENTS[p]["yahoo"], p)) if is_cfd(p) else "  (check the price and spread on your broker before you enter)",
        "  close it by %s %s if neither hits" % (show_t(cfg, dt.datetime.fromisoformat(o["flat_by"])), tzlabel(cfg)) if o.get("flat_by") else "  close it before the session ends if neither hits",
    ])


def cmd_journal(a, store, cfg):
    j = store.read("journal.json", []) or []
    now = parse_now(a.now, ZoneInfo(cfg["display_tz"]))
    day = fx_day(now).isoformat()
    if a.action == "add":
        o = store.read("order.json")
        rk = store.read("risk.json")
        if not o or not rk or rk.get("fingerprint") != o.get("fingerprint") or rk.get("verdict") != "PASS" or rk.get("fx_day") != day:
            raise FxError("this order has no PASS from the risk check today; run risk first.")
        j.append({"fx_day": day, "time": now.isoformat(), "pair": o["pair"], "side": o["side"], "lots": o["lots"],
                  "entry": o["entry"], "stop": o["stop"], "target": o["target"], "loss_at_stop": o["loss_at_stop"],
                  "fingerprint": o["fingerprint"], "demo": o.get("demo", True)})
        store.write("journal.json", j)
        print("Logged %.2f lots %s %s for %s (max loss %.2f)." % (o["lots"], o["side"], o["pair"], day, o["loss_at_stop"]))
    today = [x for x in j if x["fx_day"] == day]
    used = sum(x["loss_at_stop"] for x in today)
    print("Forex day %s: %d of %d trades, %.2f of %.2f %s daily risk used." % (
        day, len(today), int(cfg["max_trades_per_day"]), used,
        float(cfg["balance"]) * float(cfg["max_daily_loss_pct"]) / 100, cfg["account_currency"]))
    for x in today:
        print("  %s %-5s %.2f lots %s, stop %s, max loss %.2f" % (x["pair"], x["side"], x["lots"], fmt(x["pair"], x["entry"]), fmt(x["pair"], x["stop"]), x["loss_at_stop"]))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="fx.py", description="Forex day-trade tools. Never places an order.")
    ap.add_argument("--dir", default="forex", help="data folder (default forex/)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, offline=True):
        p.add_argument("--now", help="pin the clock, ISO time; no zone means your display_tz")
        if offline:
            p.add_argument("--offline", help="saved Yahoo chart JSON (.gz ok)")

    p = sub.add_parser("init"); p.add_argument("--balance", type=float); p.add_argument("--currency"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("sessions"); common(p, False)
    p = sub.add_parser("calendar"); common(p); p.add_argument("--hours", type=int, default=24)
    p.add_argument("--impact", default="High"); p.add_argument("--pairs")
    p = sub.add_parser("scan"); common(p); p.add_argument("mode", choices=["daily", "session"])
    p.add_argument("--range", choices=sorted(RANGES), default="asia"); p.add_argument("--at", help="HH:MM in the session's own zone")
    p.add_argument("--pairs"); p.add_argument("--no-write", action="store_true")
    p = sub.add_parser("plan"); common(p); p.add_argument("pair"); p.add_argument("--side", choices=["long", "short"])
    p.add_argument("--entry", type=float); p.add_argument("--stop", default="")
    p.add_argument("--target", default="2R"); p.add_argument("--write", action="store_true")
    p = sub.add_parser("replay"); common(p); p.add_argument("--sims", type=int, default=10000)
    p.add_argument("--seed", type=int, default=19); p.add_argument("--hours", type=float, default=6); p.add_argument("--write", action="store_true")
    p = sub.add_parser("size"); common(p); p.add_argument("--balance", type=float); p.add_argument("--risk-pct", type=float)
    p.add_argument("--risk-amount", type=float); p.add_argument("--quote-rate", type=float); p.add_argument("--write", action="store_true")
    p = sub.add_parser("risk"); common(p, False); p.add_argument("--calendar", help="saved calendar JSON")
    p.add_argument("--skip-news", action="store_true")
    p = sub.add_parser("journal"); common(p, False); p.add_argument("action", choices=["add", "list"], nargs="?", default="list")
    return ap


CMDS = {"init": cmd_init, "sessions": cmd_sessions, "calendar": cmd_calendar, "scan": cmd_scan,
        "plan": cmd_plan, "replay": cmd_replay, "size": cmd_size, "risk": cmd_risk, "journal": cmd_journal}


def main(argv=None):
    a = build_parser().parse_args(argv)
    store = Store(a.dir)
    try:
        cfg = load_config(store)
        return CMDS[a.cmd](a, store, cfg)
    except FxError as e:
        print("fx.py: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
