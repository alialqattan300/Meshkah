---
name: forex-trader
description: Forex day-trading toolkit, demo account only. Tells you if the forex market is open and which sessions (Sydney, Tokyo, London, New York) are on, in Kuwait time. Lists high-impact news (CPI, NFP, rate decisions) from the free ForexFactory calendar. Scans 15 major and cross pairs on daily candles (20-day breakout or breakdown, crossing the 20-day average, RSI oversold or overbought, trend) and for session-range breakouts (the London breakout of the Asian range, the London open, the New York open), long or short. Then it plans the stop and target in pips, replays the trade 10,000 times from the pair's last 60 days plus every past day the same rule fired, sizes the position in lots from your balance and risk percent, and checks it against your limits (news, daily loss, trades a day, leverage, weekend, rollover). It ends with a ticket you type into a demo account yourself, and never places an order. Use when the user says "forex", "فوركس", "عملات", "سوق العملات", "يورو دولار", "EURUSD", "GBPUSD", "USDJPY", "الين", "الباوند", "pips", "نقاط", "لوت", "lot size", "كم لوت", "London breakout", "جلسة لندن", "جلسة نيويورك", "متى يفتح سوق الفوركس", "أخبار الفوركس", "رزنامة اقتصادية", "economic calendar", or asks to scan, plan, size or check a currency trade.
---

# forex-trader

A forex version of the day-trader pack in one tool: sessions, news, scanner,
plan, time machine, sizing, risk check and journal. It reads free public
data, does the arithmetic, and stops at a ticket for a **demo account**. It
has no broker code at all: nothing here can place an order.

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
$FX scan daily                                # all 15 pairs on daily candles
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
  to type it into their **demo** account themselves.

## Safety

- Education and tooling, not financial advice. Most retail forex accounts
  lose money; leverage makes losses fast. A PASS is a rules check, not a
  recommendation.
- Demo only: `"demo": true` is the default. This skill never connects to a
  broker, never asks for or stores broker logins or API keys, and never
  places, changes or closes an order. If the user asks to connect a live
  account or trade real money, say this skill does not do that, and that
  the decision and the account are theirs.
- Never raise the user's risk limits on your own. Change `forex/config.json`
  only when the user asks, and say what changed.
- A stop is a plan, not a guarantee: news and the weekend can gap past it.
  The loss shown is the loss at the stop price plus the spread.
- Not covered: gold and metals (XAU), crypto, exotics, swaps or overnight
  financing, and holidays. Close it the same day.
