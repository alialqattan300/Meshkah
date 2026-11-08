"""A stand-in for the MetaTrader5 package, enough for the bridge tests."""

from types import SimpleNamespace as NS

ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
ORDER_TIME_GTC = 0
TRADE_ACTION_DEAL = 1
SYMBOL_TRADE_MODE_FULL = 4
TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL = 10009, 10010


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = ACCOUNT_TRADE_MODE_DEMO
    ACCOUNT_TRADE_MODE_CONTEST = ACCOUNT_TRADE_MODE_CONTEST
    ACCOUNT_TRADE_MODE_REAL = ACCOUNT_TRADE_MODE_REAL
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = ORDER_TYPE_BUY, ORDER_TYPE_SELL
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN
    ORDER_TIME_GTC = ORDER_TIME_GTC
    TRADE_ACTION_DEAL = TRADE_ACTION_DEAL
    SYMBOL_TRADE_MODE_FULL = SYMBOL_TRADE_MODE_FULL
    TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL = TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL

    def __init__(self, trade_mode=0, currency="USD", suffix="m", bid=1.11800, ask=1.11810, contract=100000,
                 tick_value=1.0, tick_size=0.00001, digits=5, filling=2, check_retcode=0, send_retcode=10009, login_ok=True):
        self.mode, self.cur, self.suffix = trade_mode, currency, suffix
        self.bid, self.ask = bid, ask
        self.contract, self.tick_value, self.tick_size, self.digits = contract, tick_value, tick_size, digits
        self.filling, self.check_rc, self.send_rc, self.login_ok = filling, check_retcode, send_retcode, login_ok
        self.sent, self.shutdowns, self.positions = [], 0, []

    def initialize(self, path=None):
        return True

    def shutdown(self):
        self.shutdowns += 1

    def last_error(self):
        return (1, "fake")

    def account_info(self):
        if not self.login_ok:
            return None
        return NS(trade_mode=self.mode, login=123456, server="Exness-MT5Trial9", company="Exness Technologies Ltd",
                  currency=self.cur, balance=1000.0, equity=1000.0, margin_free=1000.0, leverage=200)

    def terminal_info(self):
        return NS(connected=True, trade_allowed=True)

    def _known(self, s):
        return s in ("EURUSD" + self.suffix, "XAUUSD" + self.suffix, "USOIL" + self.suffix)

    def symbol_info(self, s):
        if not self._known(s):
            return None
        return NS(name=s, digits=self.digits, trade_contract_size=self.contract, volume_min=0.01, volume_step=0.01,
                  volume_max=200.0, trade_tick_value=self.tick_value, trade_tick_size=self.tick_size,
                  filling_mode=self.filling, trade_mode=SYMBOL_TRADE_MODE_FULL)

    def symbol_select(self, s, on):
        return self._known(s)

    def symbols_get(self, group=None):
        return []

    def symbol_info_tick(self, s):
        return NS(bid=self.bid, ask=self.ask, time=1794000000) if self._known(s) else None

    def order_check(self, req):
        return NS(retcode=self.check_rc, comment="Done" if self.check_rc == 0 else "No money")

    def order_send(self, req):
        self.sent.append(req)
        return NS(retcode=self.send_rc, comment="Request executed", order=555001, deal=777001,
                  volume=req["volume"], price=req["price"])

    def positions_get(self, ticket=None, symbol=None):
        return [p for p in self.positions if ticket is None or p.ticket == ticket]
