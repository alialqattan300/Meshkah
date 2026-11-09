---
name: forex-trader
description: Forex, gold, silver and oil day-trading toolkit for demo accounts, in Kuwait time. Market hours and sessions, high-impact news (ForexFactory calendar), scans of 15 pairs plus gold, silver, WTI and Brent (daily setups and London or New York session breakouts, long or short), stop and target in pips, a 10,000-run replay, lot sizing from balance and risk, and a risk check (news, daily loss, leverage, weekend). Ends with a demo ticket; an optional MetaTrader 5 bridge (Exness) sends it to an MT5 DEMO account only after an explicit yes. Use for "forex", "فوركس", "عملات", "EURUSD", "pips", "لوت", "كم لوت", "ذهب", "gold", "XAUUSD", "نفط", "oil", "برنت", "جلسة لندن", "جلسة نيويورك", "متى يفتح السوق", "أخبار الفوركس", "رزنامة اقتصادية", "Exness", "اكسنس", "MT5", or to scan, plan, size, check or send a currency, gold or oil trade.
---

# forex-trader

A forex version of the day-trader pack in one tool: sessions, news, scanner,
plan, time machine, sizing, risk check and journal. It reads free public
data, does the arithmetic, and stops at a ticket for a **demo account**.
`fx.py` has no broker code. The optional MT5 bridge in `mt5/` (below) is the
only part that can send an order, and only to an MT5 **demo** account.

Answer in the user's language. If they write in Arabic (Kuwaiti dialect),
answer in simple Arabic, keep pair names and numbers in Latin script
(EUR/USD, 1.11832, 28.3 pips), and give times in Kuwait time, which is what
the tool prints by default.

`$FX` below is `python3 "<this skill's folder>/fx.py"`. Run it from the
project folder: everything lands in `forex/`.

```bash
$FX init --balance 1000 --currency USD        # once -> forex/config.json (demo: true)
$FX sessions                                  # open? which sessions? next open, Kuwait time
$FX calendar --hours 48                       # High-impact news for your pairs
$FX calendar --impact High,Medium --pairs EURUSD,GBPUSD
$FX scan daily                                # 15 pairs + gold, silver, WTI, Brent
$FX scan session --range asia                 # London breakout of the Asian range
$FX scan session --range london               # London open, first 30 minutes
$FX scan session --range ny                   # New York open, first 30 minutes
$FX plan EURUSD --write                       # side, entry, range taken from the scan
$FX plan USDJPY --side long --stop pips:25 --target 2R --write
$FX replay --write                            # 10,000 replays + past days
$FX size --write                              # lots -> forex/order.json
$FX risk                                      # PASS / BLOCK + the demo ticket
$FX journal add                               # the user opened it on demo: count it
$FX journal                                   # today's trades and risk used
```

Flags: `--now "2026-11-06 10:05"` pins the clock (no zone means Kuwait
time), `--offline FILE` reads saved Yahoo charts, `--pairs A,B`,
`--no-write`, `--sims`, `--seed` (19), `--balance`, `--risk-pct`,
`--risk-amount`, `--quote-rate`, `--skip-news` (risk only, says so in the
output). The exit code is 0 when something was found or passed and 1
otherwise, with the reason in one plain line.

## Times (Kuwait, UTC+3 all year)

| | winter (from early Nov) | summer (Mar to Oct) |
| --- | --- | --- |
| market opens (Sun 17:00 New York) | Mon 01:00 | Mon 00:00 |
| market closes (Fri 17:00 New York) | Sat 01:00 | Sat 00:00 |
| Asian range (00:00 to 07:00 London) | 03:00 to 10:00 | 02:00 to 09:00 |
| London open (08:00 London) | 11:00 | 10:00 |
| New York open (08:00 New York) | 16:00 | 15:00 |
| London and New York overlap | 16:00 to 20:00 | 15:00 to 19:00 |

The tool does the daylight-saving shifts itself with zoneinfo; never work
them out by hand.

## Gold, silver and oil

Names: `gold`, `XAUUSD`, `silver`, `XAGUSD`, `oil` / `WTI` / `USOIL`,
`brent` / `UKOIL` (also ذهب, فضة, نفط, برنت).

| | price from | 1 pip | 1 lot | pip value a lot | default spread |
| --- | --- | --- | --- | --- | --- |
| XAUUSD gold | GC=F future | 0.10 | 100 oz | $10 | 3 pips ($0.30) |
| XAGUSD silver | SI=F future | 0.01 | 5,000 oz | $50 | 3 pips ($0.03) |
| USOIL WTI | CL=F future | 0.01 | 1,000 barrels | $10 | 3 pips ($0.03) |
| UKOIL Brent | BZ=F future | 0.01 | 1,000 barrels | $10 | 4 pips ($0.04) |

- Yahoo has no free spot price for these, so the tool reads the front-month
  future. A broker's spot or CFD price sits a little away from it and the
  future jumps at a monthly roll. That is why the ticket gives the stop and
  target as **distances**: set them from your own entry price on the broker.
- Lot sizes differ between brokers (some oil CFDs are 100 barrels a lot).
  If the user's broker differs, set `"contract_size": {"USOIL": 100}` in
  `forex/config.json`. Check the broker's contract spec.
- Hours (CME Globex): Sun 18:00 to Fri 17:00 New York, closed every day
  17:00 to 18:00 New York (01:00 to 02:00 Kuwait in winter). The risk check
  blocks entries in that break.
- News: gold and silver follow USD news. Oil also counts any crude
  inventories or OPEC item in the calendar, whatever its impact rating.
- Sizes get big fast: one lot of gold is over $400,000 of metal. With a
  small account, expect 0.01 lot and a leverage cap.

## What each step does

1. **sessions**: weekend closed from Fri 17:00 to Sun 17:00 New York. Shows
   which of the four sessions are open and the London and New York overlap.
2. **calendar**: the FairEconomy weekly feed (`nfs.faireconomy.media`),
   cached in `forex/calendar.json` for 6 hours. It covers this week only.
3. **scan daily**: one year of daily candles a pair. Rules on closes:
   `breakout_20d` / `breakdown_20d` (above or below every close of the 20
   before), `reclaim_ma20` / `lost_ma20` (crossed the 20-day average),
   `oversold` / `overbought` (RSI 14 under 30, over 70). Also the trend (20
   vs 50-day average) and the ATR in pips. Run it any time; on a weekend it
   reads Friday's closes.
4. **scan session**: 5-minute bars. The range is the high and low of the
   session window; a hit is the first completed 5-minute bar that CLOSES
   above the high (long) or below the low (short) before the watch-until
   time (12:00 London for asia and london, 12:00 New York for ny). A range
   with over 10 % of its bars missing is skipped. It says waiting, with the
   pips to each side, or expired.
5. **plan**: stop `range-mid` (default after a session scan), `range-far`
   (the other side of the range), `pips:N`, `atr:K` (default without a
   range, 1 x daily ATR) or `price:X`; target `2R` (default), `1.5R`,
   `pips:N`, `price:X`. Rounds the stop away from the entry. Refuses a stop
   on the wrong side, a target on the wrong side, or a stop under 3 x the
   spread. Flat by 16:00 in the session's city.
6. **replay**: the Time Machine. Rebuilds the rest of the day 10,000 times,
   each 5-minute step drawn from the pair's own bars at that time of day
   over the last ~60 days, and re-runs the same session rule on every past
   day. When one bar touches both the stop and the target, it counts the
   stop (the cautious read). It prints target first, stop first, neither,
   the average R and a plain verdict.
7. **size**: lots = risk budget / ((stop pips + spread) x pip value),
   rounded DOWN to 0.01 lot, and capped by `max_leverage`. The pip value
   comes from the quote currency in your account currency (Yahoo
   `JPYUSD=X` and so on). A stop too far for the smallest lot is a plain
   refusal, never a bigger risk.
8. **risk**: PASS only when every rule holds: blocked pairs, stop and
   target on the right side, reward:risk >= `min_reward_risk` (1.5), loss
   at the stop <= `risk_pct_per_trade` (1 %) of the balance, today's summed
   max losses within `max_daily_loss_pct` (3 %), under `max_trades_per_day`
   (3), leverage <= `max_leverage` (10x), market open, not Friday after
   15:00 New York, not in the 16:55 to 17:10 New York rollover, and no
   High-impact news for either currency within 30 minutes. If the
   calendar cannot be read, it BLOCKS. A PASS prints the demo ticket.
9. **journal add**: only for the order that passed today. It counts toward
   the daily limits. The forex day rolls over at 17:00 New York.

## How to talk about the results

- Say what was scanned: how many pairs, how many came back, and the time of
  the data. Data is Yahoo's free feed: delayed and unofficial. The user's
  broker price and spread are what count.
- Say "matched a rule", never "a buy", "a sure trade" or "a winner". Never
  pick the pair for the user. They choose; then hand it to `plan`.
- Give the Time Machine's numbers and verdict as they are, including when it
  says the stop comes first. It is a simulation, not a prediction.
- Show the ticket from `risk` as is, in the user's language, and remind them
  to type it into their **demo** account themselves, or offer the MT5 bridge
  if its tools are connected.

## Sending to an Exness demo account (MetaTrader 5)

`mt5/server.py` is a small MCP server that talks to the MT5 terminal on the
user's own Windows PC through the official `MetaTrader5` Python package.
Exness has no public trading API; MT5 is the way in. Works with any MT5
broker. It cannot run in a cloud session: it needs MT5 open on the same PC.

Tools (server name `exness-mt5`): `mt5_status`, `mt5_quote`,
`mt5_positions`, `mt5_preview_order` (read only); `mt5_place_order` and
`mt5_close_position` (need approval every time, set in
`.claude/settings.json`).

The flow, every time:

1. `plan`, `size --write`, `risk`: PASS. The risk check must be under 15
   minutes old when the order is sent; run it again if not.
2. `mt5_status`: it must say `"account": "DEMO"`. On a real or contest
   account the bridge disconnects and sends nothing.
3. `mt5_preview_order` with the fingerprint from forex/order.json. Show the
   user, in plain words: buy or sell, lots, symbol, the live price, stop
   and target, the loss at the stop in their currency (from Exness's own
   contract spec), and that it is the demo account.
4. Ask: "Send this exact order to your demo account? (yes / no)". Only an
   explicit yes for this one order counts. Anything else: do not send.
5. `mt5_place_order` with the fingerprint. Claude Code then asks once more
   (the approval card). Report the result as it comes back, including any
   "NOT SENT" reason.

What the bridge enforces on its own: demo accounts only (checked again right
before sending); a fresh risk PASS for that exact fingerprint; each order
sent once; the account currency matches; the live spread is within 2x the
planned spread; stop and target are set by distance from the live price;
lots round down to the symbol's step; the loss at the stop, from the
broker's tick value, stays within the risk budget (else it names the
`contract_size` to set); `order_check` before `order_send`. Sent orders go
into forex/sent.json and the journal, so daily limits count them. Closing a
position is never blocked by a limit, but still needs the user's yes.

Setup on the user's Windows PC (tell them, step by step, when they ask):

1. Exness Personal Area: open a **demo** MT5 account. Install MetaTrader 5
   from Exness and log in to the demo account.
2. In MT5: Tools > Options > Expert Advisors: tick "Allow algorithmic
   trading", and turn on the "Algo Trading" button in the toolbar.
3. Install Python 3.11 to 3.14 from python.org (tick "Add to PATH"), then in
   a terminal: `pip install MetaTrader5 "mcp>=2.3,<3"`.
4. Install Claude Code (or the Claude desktop app's Code tab), and clone
   this repository on the PC.
5. In the repository folder:
   `claude mcp add exness-mt5 -- python .claude/skills/forex-trader/mt5/server.py`
6. Start Claude Code in that folder and ask for the MT5 status. It must say
   DEMO. Never set the order tools to "always allow", and do not run in a
   mode that skips permission prompts.

Logins and passwords stay in MT5. Never ask for them in the chat and never
put them in a file.

## Safety

- Education and tooling, not financial advice. Most retail forex accounts
  lose money; leverage makes losses fast. A PASS is a rules check, not a
  recommendation.
- Demo only: `"demo": true` is the default. `fx.py` never connects to a
  broker. The MT5 bridge only works on demo accounts and only sends an
  order the risk check passed and the user said yes to. Never ask for or
  store broker logins, passwords or API keys. If the user asks to trade a
  live account or real money, say this skill does not do that and do not
  change the bridge to allow it; that decision and that account are theirs.
- Never raise the user's risk limits on your own. Change `forex/config.json`
  only when the user asks, and say what changed.
- A stop is a plan, not a guarantee: news and the weekend can gap past it.
  The loss shown is the loss at the stop price plus the spread.
- Not covered: platinum and other metals, natural gas, crypto, exotics,
  swaps or overnight financing, and holidays. Close it the same day.
