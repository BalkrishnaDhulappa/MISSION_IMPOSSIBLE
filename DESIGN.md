# MTF→F&O Zerodha System — Design

**Status:** ✅ **DESIGN ACCEPTED for dry-run / demo** (2026-08-04)  
**Based on:** `PLAN_AND_REQUIREMENTS.md` (frozen decisions D1–D12)  
**Reuse:** `existing_bots/daily_etf_sip/fire_shop` + live `crontab_l` on Oracle Always Free  
**Next:** Implementation C0 when you say start — dry-run only; no live MTF until separate live sign-off  

**Accepted extras from discussion:**
- **No client stop-loss**
- Must handle **Zerodha force square-off / margin crunch** (RMS)
- **Fault tolerant** jobs + **instant Telegram alerts**

> Educational personal system. Not investment advice.

---

## 1. Design goals

1. Recreate the course MTF nursery rules on **Zerodha only**, in **dry-run first**.  
2. Coexist with the existing **fire_shop** daily ₹6,000 CNC ETF bot on the same VM/account.  
3. Reuse proven patterns: Kite token file, Telegram notify, cron + `flock`, config JSON, IST calendar.  
4. Keep secrets out of git; keep venv local-only.

---

## 2. System context

```text
┌─────────────────────────────────────────────────────────┐
│ Oracle Always Free VM                                   │
│                                                         │
│  cron                                                    │
│   ├─ fire_shop (EXISTING)                               │
│   │    token gen → buy ₹6k CNC ETF → sell ≥6.28%        │
│   └─ mtf_nursery (NEW)                                  │
│        token (shared) → scan → gates → dry-run/live     │
│        intents → EMI/LIQUIDCASE funding jobs            │
│                                                         │
│  ~/.env_fire_shop  +  fire_shop/.kite_token (local)     │
│  Telegram notifications                                 │
└──────────────────────┬──────────────────────────────────┘
                       │ Kite Connect HTTP API
                       ▼
                 Zerodha account
         cash · CNC ETF holdings · MTF holdings
         LIQUIDCASE (EMI float)
```

### Shared-account cash competition (must design for)
| Consumer | Cadence | Approx cash |
|---|---|---|
| fire_shop buy | Weekdays ~15:00 IST | **₹6,000**/day |
| MTF nursery entry | ~2 tickets/month (funding-safe) | ~₹6.5k at entry each |
| MTF EMI / buffer | Weekly | EMI amounts from ledger |
| Ops reserve | Always | keep free cash above EMI obligation + next fire_shop buy |

**Cash governor rule:** before any MTF buy or LIQUIDCASE sell-for-EMI, require:
`available_cash − reserved_for_fire_shop_today − remaining_emi_obligation > ticket_immediate_need`

---

## 3. Modules (v1)

| Module | Responsibility | Notes |
|---|---|---|
| **config** | JSON settings (ticket, rates, paths, mode) | Mirror `fire_shop/config.json` style |
| **kite_client** | Load token, wrap KiteConnect | Reuse fire_shop `get_kite()` pattern |
| **calendar** | Market open/holidays | Reuse `market_calendar_YYYY.json` |
| **scanner** | FO universe + filters (D1=A) | Port `colab_code` + spoken filters |
| **ledger** | Positions, EMI schedule, Force/RF, steps | Source of truth (SQLite or JSON) |
| **gates** | EMI obligation, cash, 1-buy/1-sell, MTF-approved | Block/allow decisions |
| **pricing/costs** | Zerodha interest/fees model | §7.1 requirements |
| **compounding** | 6.28% exit net → 50/50 → next ticket | From `MTF to F&O.xlsx` logic |
| **funding** | LIQUIDCASE sell → cash for EMI/buffer | Collateral ≠ MTF cash |
| **rms_guard** | Monitor Zerodha RMS / MTM / margin shortfall risk | See §6.8 — not a client SL |
| **executor** | Place or log orders | `mode=dry_run\|live` |
| **notify** | Telegram | Reuse fire_shop sender |
| **jobs** | CLI entrypoints for cron | buy / sell / emi / scan / status / rms |

### Out of v1
- CAR averaging (deferred)  
- Covered-call writer  
- Live mode default-on  
- Groww / multi-broker  

---

## 4. Proposed repo layout

```text
mtf_nursery/
  README.md
  requirements.txt          # kiteconnect, pandas, yfinance, ...
  config.example.json
  .gitignore
  src/
    config.py
    kite_client.py
    calendar_ist.py
    scanner.py
    ledger.py
    gates.py
    costs.py
    compounding.py
    funding.py
    executor.py
    notify.py
  jobs/
    run_scan.py
    run_buy.py
    run_sell.py
    run_emi_funding.py
    run_status.py
  data/                     # gitignored runtime state
    ledger.sqlite
    last_scan.json
  tests/
    test_scanner_filters.py
    test_emi_math.py
    test_gates.py
    test_compounding.py
```

Deploy on VM beside fire_shop, e.g. `/home/ubuntu/mtf_nursery`, sharing env/token approach.

---

## 5. Data model (ledger)

### 5.1 `positions`
| Field | Type | Meaning |
|---|---|---|
| id | PK | |
| symbol | text | NSE tradingsymbol |
| buy_date | date | |
| qty | int | |
| avg_price | float | |
| buy_value | float | qty×avg |
| initial_margin | float | actual from broker/estimate |
| buffer_10pct | float | 0.10×buy_value |
| broker_remaining0 | float | buy_value − initial − buffer |
| weekly_emi | float | remaining0 / 16 |
| status | enum | `open_mtf` / `delivered` / `closed` |
| step_id | int | compounding step |
| force_tag | enum/null | `F` / `RF` / null while open |
| product | text | `MTF` |

### 5.2 `emi_schedule`
| Field | Meaning |
|---|---|
| position_id | FK |
| installment_no | 1..16 |
| due_date | date |
| amount | float |
| paid | bool |
| paid_at | datetime/null |
| funding_order_id | text/null | LIQUIDCASE sell / cash move ref |

### 5.3 `steps` / Force–RF
| Field | Meaning |
|---|---|
| step_no | 1..N |
| ticket_amount | from ladder / 3-in-1 average |
| force_count | 0..3 |
| advanced | bool |

### 5.4 `orders_log`
Every intended or live order: ts, side, symbol, qty, product, mode, reason, gate_results, broker_order_id/null.

### 5.5 `cash_reservations`
Daily reservation for fire_shop ₹6k (and optional ops reserve).

**Storage choice:** SQLite for relational EMI queries; JSON export compatible with Smart Margin sheet columns for human audit.

---

## 6. Core algorithms

### 6.1 Scanner (D1 = A)
Universe: 210 FO tickers from `colab_code`.

Keep from Colab:
- CA from 52w high; Positive iff last 10 CA strictly rising  
- CMP > 30 DMA, CMP > 200 DMA  
- Sort by `% above 200 DMA` ascending  

Change vs Colab to match spoken Class 4:
- Require **`dma_30 > dma_50`** (not merely CMP > 50 DMA)  
- Hard filter **`dist_200 <= 10`**

Output: ranked candidates JSON for the day.

### 6.2 EMI math (Smart Margin sheet)
On fill:
```text
buffer = 0.10 * buy_value
immediate = initial_margin + buffer
remaining = buy_value - immediate
weekly_emi = remaining / 16
due[i] = buy_date + 7*i  for i=1..16
```
Status gate:
```text
remaining_obligation = sum over open rows:
  weekly_emi * count(due_dates >= today and not paid)
allow_new_buy iff free_cash > remaining_obligation + fire_shop_reserve
```

### 6.3 Zerodha costs
```text
interest = funded_amount * 0.0004 * holding_days   # from T+1
brokerage_per_order = min(0.003 * value, 20)
pledge_one_side = 15 * 1.18
```
Used in compounding net and dry-run P&L estimates.  
`funded_amount` tracked as declining with EMI pay-downs when possible; else approximate from broker margins API if available.

### 6.4 Sell rule (D4)
Among open MTF positions with unrealized % ≥ 6.28%:
- pick **max %**
- **at most one sell/day**
- on success: mark F or RF; run compounding; update next ticket

### 6.5 Compounding (Class 5 Excel, Zerodha fees)
```text
gross = exit_value - buy_value
net = gross - brokerage_both - pledge_both - interest - tax_model
self_div = 0.5 * net
growth = 0.5 * net
next_ticket = current_ticket + growth
```
Tax model v1: keep sheet-style estimate `(net_before_tax * 0.2) * 1.04` as configurable; document as estimate only.

### 6.6 LIQUIDCASE funding
Job `run_emi_funding.py` (morning, before market or early session):
1. List EMIs due today/overdue  
2. Compute cash shortfall  
3. If shortfall > 0: dry-run/live **CNC sell LIQUIDCASE** qty covering shortfall + small buffer  
4. Mark funding intent; rely on cash for Zerodha MTF margin utilization  
5. Never treat pledged collateral as MTF cash

### 6.7 Pace governor
Even if scanner has many names:
- hard cap **1 MTF buy/day**
- funding-safe default **~2 new MTF tickets/month** until seed/SIP allow more  
- config: `max_mtf_buys_per_month`

---

### 6.8 RMS guard — Zerodha margin crunch / force square-off (NOT client SL)

**Policy:** system never places a client price stop-loss on MTF stocks.  
**Must handle:** Zerodha RMS force-square when margin/MTM blows through broker rules.

**Primary recovery:** **sell LIQUIDCASE (CNC) → free cash**  
MTF needs **cash**. Pledged collateral cannot fund MTF. Liquid ETF sell is the designed fix for margin crunch.

**Detect / estimate**
| Scenario | What we watch | Severity |
|---|---|---|
| Loss vs **funded** approaching ~20% / ~80% policy bands | position MTM vs funded estimate | WARN → CRITICAL |
| Margin shortfall / Additional Maintenance Margin (VAR up) | margins / ledger / available cash | CRITICAL |
| Account debit / inability to cover EMI+buffer+fire_shop reserve | cash governor | CRITICAL |
| Broker already squared a position | order/trade sync vs ledger | CRITICAL |
| Token/API/job failure | exceptions, missing token, flock timeout | CRITICAL |

**Response ladder**
1. Instant Telegram `[MTF][CRITICAL]` / `[MTF][WARN]`  
2. `buy_blocked=true` until risk clears  
3. Compute cash shortfall to restore safe buffer  
4. **Sell LIQUIDCASE** for shortfall + small cushion (respect min reserve)  
5. Re-check; if still short → alert “deposit cash / human intervention”  
6. Log everything in `rms_events`

**Dry-run / demo (v1):** do 1–3 and **log the exact LIQUIDCASE sell intent** (qty/₹); no live order until `live_liquid_topup=true`.  
**Live top-up (separate flag):** actually place CNC LIQUIDCASE sell. Still never SL the MTF stock.

**Safety limits**
| Limit | Rule |
|---|---|
| Min reserve | Keep `liquid_etf_min_reserve` for future EMIs |
| Max per event | Cap `liquid_etf_max_sell_per_event` |
| Fire_shop guard | Don’t strip cash needed for same-day ₹6k buy |
| Cooldown | One top-up intent per breach window |
| Exhaustion | If LIQUIDCASE too low → CRITICAL deposit alert |

---

### 6.9 Fault tolerance + instant Telegram

| Requirement | Design rule |
|---|---|
| Instant alerts | CRITICAL/ERROR → Telegram **immediately** on detect (not only end-of-day status) |
| Job isolation | Each cron job independent; one failure must not stop others |
| Locks | Keep `flock -n`; if lock busy → alert “skipped overlapping run” |
| Retries | Transient Kite/network errors: limited retry with backoff; then alert |
| Idempotency | Same day/job/symbol cannot double-intend buys/sells |
| Safe defaults | On unknown state → **block new buys**, alert, continue monitoring |
| Heartbeat | Daily status job still runs; if token/scan/buy job missing → alert |
| Secrets | Fail loud if token/env missing; never crash silently |

Alert channels: reuse fire_shop Telegram bot/chat. Message prefix tags: `[MTF][CRITICAL]`, `[MTF][WARN]`, `[MTF][INFO]`.

---

## 7. Execution modes

| Mode | Behavior |
|---|---|
| `paper` | No Kite orders; ledger simulates fills at CMP |
| `dry_run` | Read Kite holdings/margins/quotes; **log** intended orders only |
| `live` | Place orders; requires `LIVE_CONFIRM=YES` env + config flag |

v1 ship target: **`dry_run` + `paper`**. Live behind explicit second sign-off.

---

## 8. Cron plan (IST → UTC) — aligned to live `crontab_l`

### 8.1 Currently ACTIVE on VM (`crontab_l`)
| Job | Cron (UTC) | IST | Command |
|---|---|---|---|
| Token gen | `30 3 * * 1-5` | **09:00** | `server_generate_token.py` |
| Daily notify | `0 4 * * 1-5` | **09:30** | `daily_notify.py` |
| **Buy ₹6k ETF** | `30 9 * * 1-5` | **15:00** | `buy_engine.py` (+ flock) |
| Weekly summary | `30 12 * * 0` | Sun **18:00** | `weekly_market_summary.py` |

### 8.2 Currently DISABLED on VM
| Job | Notes |
|---|---|
| `sell_engine.py` (was 15:15 IST) | `# DISABLED 2026-06-29 stop sell engine` |
| Legacy `pkp_engine.py` | commented |
| All `algo_trader` / crypto / gold / delta jobs | `# DISABLED 2026-06-29` |

**Design implication:** fire_shop is currently **buy-only** for the daily ₹6k CNC SIP. MTF nursery must not assume an automatic fire_shop sell frees cash the same day.

Paths confirmed: `/home/ubuntu/fire_shop`, env `/home/ubuntu/.env_fire_shop`, venv `/home/ubuntu/fire_shop/venv/bin/python3`.

### 8.3 Proposed mtf_nursery cron (add beside existing; do not break buy)
| Job | IST | UTC | Purpose |
|---|---|---|---|
| `run_emi_funding` | 09:45 | `15 4` | LIQUIDCASE→cash if EMI/buffer due |
| `run_rms_guard` | 10:00 | `30 4` | MTM/funded/margin risk alerts; may block buys |
| `run_scan` | 14:30 | `0 9` | Candidate list |
| `run_sell` | 14:40 | `10 9` | ≤1 MTF winner ≥6.28% |
| `run_buy` | 14:50 | `20 9` | ≤1 MTF buy if gates + RMS OK (**before** 15:00 fire_shop buy) |
| `run_status` | 16:00 | `30 10` | Telegram summary |

All mtf jobs: `flock`, `cd /home/ubuntu/mtf_nursery`, source same `.env_fire_shop` (or dedicated env), shared/synced `.kite_token` after `30 3` token job.

Rationale unchanged: finish MTF cash decisions **before** the 15:00 ₹6k CNC buy.

---

## 9. Reuse map from fire_shop

| fire_shop piece | Reuse how |
|---|---|
| `server_generate_token.py` | Share token generation; mtf_nursery reads same `.kite_token` path or symlink |
| `.env_fire_shop` | Same `KITE_API_KEY`, Telegram vars |
| `send_telegram` | Copy/adapt helper |
| `market_calendar_*.json` | Shared calendar file |
| `config.json` pattern | New `mtf_nursery/config.json` |
| `flock` cron wrappers | Same style |
| `PRODUCT_CNC` orders | fire_shop only; nursery uses `PRODUCT_MTF` for entries |
| 6.28% target | Already aligned philosophically |

**Do not** merge bots into one process in v1 — separate cron entries, shared auth/notify utilities only.

---

## 10. Config sketch

```json
{
  "mode": "dry_run",
  "ticket_start": 15000,
  "max_buys_per_day": 1,
  "max_sells_per_day": 1,
  "max_mtf_buys_per_month": 2,
  "profit_target_pct": 0.0628,
  "buffer_pct": 0.10,
  "emi_weeks": 16,
  "interest_daily": 0.0004,
  "brokerage_rate": 0.003,
  "brokerage_cap": 20,
  "pledge_per_side": 15,
  "gst": 1.18,
  "fire_shop_daily_reserve": 6000,
  "liquid_etf_symbol": "LIQUIDCASE",
  "live_liquid_topup": false,
  "liquid_etf_min_reserve": 10000,
  "liquid_etf_max_sell_per_event": 25000,
  "scanner": {
    "require_dma30_gt_dma50": true,
    "max_dist_200_pct": 10.0,
    "car_rising_days": 10
  }
}
```

---

## 11. Security & ops

- Never commit `.kite_token`, `.env`, venv  
- Token job remains fire_shop’s; nursery fails safe if token missing/expired  
- Idempotency keys: `date+job+symbol` in orders_log  
- Hard stop flag file / Telegram command optional later  
- On Oracle Always Free: keep footprint small (no venv in git; one shared venv OK)

---

## 12. Testing strategy (before any live)

1. Unit: EMI math vs Smart Margin sheet examples  
2. Unit: scanner filters on fixture OHLC  
3. Unit: gates (cash, 1/day, monthly cap, obligation)  
4. Unit: compounding with Zerodha fee model  
5. Integration paper: simulated week with fake quotes  
6. Dry-run on VM reading real portfolio **without** placing MTF orders  

---

## 13. Implementation phases (after design sign-off)

| Phase | Deliverable |
|---|---|
| C0 | Repo scaffold + config + tests for pure math |
| C1 | Ledger + EMI + gates (no broker) |
| C2 | Scanner port (D1=A) |
| C3 | Kite read-only + dry-run executor + Telegram |
| C4 | LIQUIDCASE funding dry-run |
| C5 | Cron on Oracle beside fire_shop |
| C6 | Live MTF (optional, separate approval) |

---

## 14. Design acceptance — ACCEPTED (dry-run / demo)

Accepted **2026-08-04** after discussion:

```text
Design:
- [x] Module split OK
- [x] Coexistence with fire_shop ₹6k OK
- [x] Scanner D1=A encoding OK
- [x] EMI + LIQUIDCASE→cash flow OK
- [x] Cron order (before fire_shop) OK
- [x] v1 = dry_run / demo only OK
- [x] SQLite ledger OK
- [x] No client stop-loss
- [x] Handle Zerodha margin-crunch / force square-off via RMS guard
- [x] **Auto LIQUIDCASE→cash** as primary margin-crunch recovery (dry-run logs intent; live behind flag)
- [x] Fault tolerant jobs + instant Telegram alerts
```

**Next:** say **start C0** to begin scaffold + math/RMS tests (still no live MTF).
