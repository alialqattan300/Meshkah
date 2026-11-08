"""MetaTrader 5 bridge for the forex-trader skill: DEMO accounts only.

It talks to an MT5 terminal running on the same Windows PC through the
official MetaTrader5 Python package. It refuses anything that is not a demo
account, and it only sends an order that fx.py sized and the risk check
passed, matched by its fingerprint, once.

The logic lives here so it can be tested without MT5; server.py exposes it
to Claude as MCP tools.
"""

import datetime as dt
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import fx  # noqa: E402

MAGIC = 260611
RISK_FRESH_MIN = 15
SYMBOL_FILLING_FOK = 1   # bit flags in symbol_info().filling_mode
SYMBOL_FILLING_IOC = 2
SUFFIXES = ("", "m", "c", "z", ".", "-", "_", "x", "r")


class BridgeError(Exception):
    """A plain reason nothing was sent."""


def default_forex_dir():
    if os.environ.get("FOREX_DIR"):
        return os.environ["FOREX_DIR"]
    # <repo>/.claude/skills/forex-trader/mt5/bridge.py -> <repo>/forex
    root = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
    return os.path.join(root, "forex")


def _round(x, nd, mode):
    q = 10 ** nd
    v = round(x * q, 4)
    return (math.floor(v) if mode == "down" else math.ceil(v)) / q


class Bridge:
    def __init__(self, mt5, forex_dir=None, clock=None, terminal_path=None):
        self.mt5 = mt5
        self.store = fx.Store(forex_dir or default_forex_dir())
        self.clock = clock or (lambda: dt.datetime.now(fx.UTC))
        self.path = terminal_path or os.environ.get("MT5_PATH")
        self.ready = False

    # ---------------------------------------------------------- connection

    def connect(self):
        if not self.ready:
            ok = self.mt5.initialize(path=self.path) if self.path else self.mt5.initialize()
            if not ok:
                raise BridgeError("could not attach to MetaTrader 5 (%s). Open MT5 and log in to your Exness DEMO account first."
                                  % (self.mt5.last_error(),))
            self.ready = True
        return self.demo_account()

    def demo_account(self):
        ai = self.mt5.account_info()
        if ai is None:
            raise BridgeError("MT5 is open but not logged in to an account (%s)." % (self.mt5.last_error(),))
        if ai.trade_mode != self.mt5.ACCOUNT_TRADE_MODE_DEMO:
            kind = {self.mt5.ACCOUNT_TRADE_MODE_REAL: "REAL", self.mt5.ACCOUNT_TRADE_MODE_CONTEST: "CONTEST"}.get(ai.trade_mode, "non-demo")
            try:
                self.mt5.shutdown()
            finally:
                self.ready = False
            raise BridgeError("MT5 is logged in to a %s account (%s, %s). This bridge only works on a DEMO account, "
                              "so it disconnected and sent nothing." % (kind, ai.login, ai.server))
        return ai

    # ---------------------------------------------------------- read

    def status(self):
        ai = self.connect()
        ti = self.mt5.terminal_info()
        return {"account": "DEMO", "login": ai.login, "server": ai.server, "company": ai.company,
                "currency": ai.currency, "balance": ai.balance, "equity": ai.equity,
                "free_margin": ai.margin_free, "leverage": ai.leverage,
                "terminal_connected": bool(getattr(ti, "connected", False)),
                "algo_trading_on": bool(getattr(ti, "trade_allowed", False))}

    def resolve(self, name):
        pair = fx.norm_pair(name)
        tried = []
        for s in [pair + x for x in SUFFIXES] + [str(name)]:
            if s in tried:
                continue
            tried.append(s)
            info = self.mt5.symbol_info(s)
            if info is not None:
                self.mt5.symbol_select(s, True)
                return pair, s, self.mt5.symbol_info(s) or info
        found = self.mt5.symbols_get(group="*%s*" % pair) or []
        if found:
            s = sorted((f.name for f in found), key=len)[0]
            self.mt5.symbol_select(s, True)
            return pair, s, self.mt5.symbol_info(s)
        raise BridgeError("this account has no symbol for %s (tried %s). Check Market Watch in MT5." % (pair, ", ".join(tried)))

    def quote(self, name):
        self.connect()
        pair, sym, info = self.resolve(name)
        t = self.mt5.symbol_info_tick(sym)
        if t is None:
            raise BridgeError("no price for %s right now (%s)." % (sym, self.mt5.last_error()))
        pip = fx.pip_size(pair)
        return {"pair": pair, "symbol": sym, "bid": t.bid, "ask": t.ask,
                "spread_pips": round((t.ask - t.bid) / pip, 1), "digits": info.digits,
                "contract_size": info.trade_contract_size, "volume_min": info.volume_min,
                "volume_step": info.volume_step, "time": dt.datetime.fromtimestamp(t.time, fx.UTC).isoformat()}

    def positions(self):
        self.connect()
        out = []
        for p in self.mt5.positions_get() or []:
            out.append({"ticket": p.ticket, "symbol": p.symbol, "side": "long" if p.type == self.mt5.ORDER_TYPE_BUY else "short",
                        "volume": p.volume, "open_price": p.price_open, "sl": p.sl, "tp": p.tp,
                        "price_now": p.price_current, "profit": p.profit, "comment": p.comment})
        return out

    # ---------------------------------------------------------- send

    def _filling(self, info):
        fm = int(getattr(info, "filling_mode", 0) or 0)
        if fm & SYMBOL_FILLING_IOC:
            return self.mt5.ORDER_FILLING_IOC
        if fm & SYMBOL_FILLING_FOK:
            return self.mt5.ORDER_FILLING_FOK
        return self.mt5.ORDER_FILLING_RETURN

    def _approved_order(self, fingerprint):
        now = self.clock()
        o = self.store.read("order.json")
        rk = self.store.read("risk.json")
        if not o:
            raise BridgeError("there is no sized order (forex/order.json); run plan, size and risk first.")
        if o.get("fingerprint") != fingerprint:
            raise BridgeError("fingerprint %s is not the current order (%s); nothing sent." % (fingerprint, o.get("fingerprint")))
        if not rk or rk.get("fingerprint") != fingerprint or rk.get("verdict") != "PASS":
            raise BridgeError("this order has no PASS from the risk check; run risk first.")
        checked = dt.datetime.fromisoformat(rk["checked_at"])
        if rk.get("fx_day") != fx.fx_day(now).isoformat() or now - checked > dt.timedelta(minutes=RISK_FRESH_MIN):
            raise BridgeError("the risk check is older than %d minutes; run risk again right before sending." % RISK_FRESH_MIN)
        sent = self.store.read("sent.json", []) or []
        if any(s.get("fingerprint") == fingerprint for s in sent):
            raise BridgeError("this exact order was already sent once; nothing sent again.")
        return o, sent, now

    def preview(self, fingerprint):
        """Everything place() would send, without sending it."""
        o, _, now = self._approved_order(fingerprint)
        ai = self.connect()
        if str(ai.currency).upper() != o["account_currency"]:
            raise BridgeError("the MT5 account is in %s but the order was sized in %s; set account_currency in forex/config.json and size again."
                              % (ai.currency, o["account_currency"]))
        pair, sym, info = self.resolve(o["pair"])
        if getattr(info, "trade_mode", self.mt5.SYMBOL_TRADE_MODE_FULL) != self.mt5.SYMBOL_TRADE_MODE_FULL:
            raise BridgeError("%s is not open for full trading on this account right now." % sym)
        t = self.mt5.symbol_info_tick(sym)
        if t is None or not t.bid or not t.ask:
            raise BridgeError("no live price for %s (%s)." % (sym, self.mt5.last_error()))
        pip = fx.pip_size(pair)
        long = o["side"] == "long"
        price = t.ask if long else t.bid
        spread = (t.ask - t.bid) / pip
        allowed = max(2 * o["spread_pips"], o["spread_pips"] + 2)
        if spread > allowed:
            raise BridgeError("the spread on %s is %.1f pips now, over the %.1f this order allows (planned %.1f). Wait, then run risk again."
                              % (sym, spread, allowed, o["spread_pips"]))
        nd = int(info.digits)
        sgn = 1 if long else -1
        sl = _round(price - sgn * o["stop_pips"] * pip, nd, "down" if long else "up")
        tp = _round(price + sgn * o["target_pips"] * pip, nd, "down" if long else "up")
        step = float(info.volume_step)
        vol = math.floor(round(o["lots"] / step, 6)) * step
        vol = round(vol, 8)
        if vol < info.volume_min:
            raise BridgeError("%.2f lots is under this symbol's minimum %.2f." % (o["lots"], info.volume_min))
        if vol > info.volume_max:
            raise BridgeError("%.2f lots is over this symbol's maximum %.2f." % (o["lots"], info.volume_max))
        # the loss at the stop, from the broker's own contract spec
        tick_val, tick_size = float(info.trade_tick_value), float(info.trade_tick_size)
        loss = vol * (abs(price - sl) + (t.ask - t.bid)) / tick_size * tick_val
        if loss > o["risk_budget"] * 1.05:
            raise BridgeError("at Exness's contract size (%s a lot) this order would lose about %.2f %s at the stop, over your %.2f budget. "
                              "Set \"contract_size\": {\"%s\": %s} in forex/config.json, then size and risk again."
                              % (info.trade_contract_size, loss, ai.currency, o["risk_budget"], pair, info.trade_contract_size))
        req = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": vol,
               "type": self.mt5.ORDER_TYPE_BUY if long else self.mt5.ORDER_TYPE_SELL,
               "price": price, "sl": sl, "tp": tp, "deviation": 20, "magic": MAGIC,
               "comment": "fx %s" % fingerprint, "type_time": self.mt5.ORDER_TIME_GTC,
               "type_filling": self._filling(info)}
        return {"pair": pair, "symbol": sym, "side": o["side"], "volume": vol, "price": price, "sl": sl, "tp": tp,
                "spread_pips": round(spread, 1), "loss_at_stop": round(loss, 2), "currency": ai.currency,
                "account": "DEMO %s @ %s" % (ai.login, ai.server), "request": req}

    def place(self, fingerprint):
        p = self.preview(fingerprint)
        chk = self.mt5.order_check(p["request"])
        if chk is None or chk.retcode != 0:
            raise BridgeError("MT5 rejected the check: %s %s. Nothing sent." % (getattr(chk, "retcode", self.mt5.last_error()), getattr(chk, "comment", "")))
        self.demo_account()  # once more, right before sending
        res = self.mt5.order_send(p["request"])
        if res is None:
            raise BridgeError("MT5 returned nothing (%s); check the Trade tab in MT5 before trying again." % (self.mt5.last_error(),))
        if res.retcode not in (self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_DONE_PARTIAL):
            hint = " Turn on Algo Trading in MT5." if res.retcode == 10027 else ""
            raise BridgeError("MT5 refused the order: retcode %s, %s.%s" % (res.retcode, res.comment, hint))
        now = self.clock()
        o = self.store.read("order.json")
        sent = self.store.read("sent.json", []) or []
        rec = {"fingerprint": fingerprint, "time": now.isoformat(), "symbol": p["symbol"], "side": p["side"],
               "volume": res.volume or p["volume"], "price": res.price or p["price"], "sl": p["sl"], "tp": p["tp"],
               "order": res.order, "deal": res.deal, "account": p["account"]}
        sent.append(rec)
        self.store.write("sent.json", sent)
        journal = self.store.read("journal.json", []) or []
        journal.append({"fx_day": fx.fx_day(now).isoformat(), "time": now.isoformat(), "pair": o["pair"], "side": o["side"],
                        "lots": rec["volume"], "entry": rec["price"], "stop": p["sl"], "target": p["tp"],
                        "loss_at_stop": p["loss_at_stop"], "fingerprint": fingerprint, "demo": True, "mt5_order": res.order})
        self.store.write("journal.json", journal)
        return dict(rec, loss_at_stop=p["loss_at_stop"], currency=p["currency"])

    def close(self, ticket):
        """Getting out is never blocked by a limit; it still needs the user's yes."""
        self.connect()
        ps = self.mt5.positions_get(ticket=int(ticket)) or []
        if not ps:
            raise BridgeError("no open position with ticket %s." % ticket)
        pos = ps[0]
        info = self.mt5.symbol_info(pos.symbol)
        t = self.mt5.symbol_info_tick(pos.symbol)
        long = pos.type == self.mt5.ORDER_TYPE_BUY
        req = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume, "position": pos.ticket,
               "type": self.mt5.ORDER_TYPE_SELL if long else self.mt5.ORDER_TYPE_BUY,
               "price": t.bid if long else t.ask, "deviation": 20, "magic": MAGIC, "comment": "fx close",
               "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(info)}
        self.demo_account()
        res = self.mt5.order_send(req)
        if res is None or res.retcode not in (self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_DONE_PARTIAL):
            raise BridgeError("MT5 did not close it: %s %s." % (getattr(res, "retcode", self.mt5.last_error()), getattr(res, "comment", "")))
        return {"closed": pos.ticket, "symbol": pos.symbol, "volume": pos.volume, "price": res.price}


def to_text(d):
    return json.dumps(d, indent=2, default=str)
