# FIRE ETF Shop — Compounding Design Plan

**Status:** 🟢 **DECISIONS FROZEN** · signed design in `FIRE_ETF_DESIGN.md` (awaiting acceptance) · **no code until design accepted**  
**Source transcript:** `componding_v2` (Mahesh Chandra Kaushik — ETF Dukaan Updated Version, Class 3)  
**Live system today:** Oracle `fire_shop` (CNC ETF SIP)  
**Related PR (provisional):** #6 capital-double 6.28→3.14 — **superseded by D1/D2 below** (revert when coding)  
**Org / filesystem:** deferred (`FIRE_SHOP_ORG_PLAN.md`) — strategy code plan first  

### Frozen so far
| ID | Decision | Notes |
|---|---|---|
| **D1** | Profit eligibility = **6.38%** vs broker avg | Cushion for LTP−0.1% limit so fill stays ~≥6.28% |
| **D2** | **No** 3.14% capital-double half-target | “single exit threshold” only |
| **D6** | **No self-dividend** — 100% of reusable net → growth | |
| **D6b** | Brokerage = **actual Zerodha charges** on the sell | Not a manual estimate to “set aside” |
| **D6c** | **No tax haircut in compounding ledger** | Can’t segregate tax into separate funds; reuse what remains after sell |
| **B1** | “Brokerage” = **all trade charges**, not ₹0 brokerage line | CNC delivery brokerage is ₹0; count STT/txn/SEBI/GST/DP |
| **B2** | Source = Kite **`/charges/orders`** after fill | Virtual contract note `charges.total` |
| **B3** | Fallback = CNC **formula** if API fails | Rates aligned with `strategy_validator.calc_trade_costs` |
| **B4** | **Include DP** on sell | From API if present, else flat ~₹15.34 |
| **D3** | Start WC = **₹6,000 × 50 = ₹3,00,000** | Bootstrap from today’s ticket (option C) |
| **D3b** | Extra cash = **buffer** (not in WC) | Keep WC simple at ₹3L |
| **D4** | Ticket **grows** = WC / parts | Compounding takes effect after each booked growth |
| **D5** | Parts = **50** | Author Class 3 default |
| **S1** | Sells per day = **one** | |
| **S2** | Winner = **highest unrealized %** | |
| **S3** | Profit % vs **broker holding average** | Kite `average_price` (blended buys/BIDs) |
| **S4** | Qty = **full position** | |
| **S5** | Order = **LIMIT @ LTP × (1 − 0.001)** | LTP − 0.1% |
| **S6** | Scan = **ETF universe only** | |
| **S7** | Price for eligibility = **Kite LTP** | |
| **S8** | Sell cron = **15:05 IST** | Independent of buy @ 15:00 |
| **D8** | Re-enable sell cron after implement | Intent: yes · cron **15:05 IST** |
| **D7** | BID keep **current** | −4% from last_buy, max 3, size max(invested/2, ticket) |
| **R1** | Buy rank = **DMA-dip** (current) | Do **not** switch to Class 3 RSI for v1 |
| **D9** | First build = **full cutover (B)** | Sell logic + compounding + growing ticket on buys together |
| **D10** | **Revert** provisional PR #6 6.28→3.14 capital-double code | Conflicts with frozen exit rules |
| **T1** | Same-day: sell growth applies to **next** buy | Buy 15:00 / sell 15:05 → ticket bump used from next session |
| **M0** | FIRE sleeve = **ETFs only** | Exclude stocks/SGB etc. from WC / compounding |
| **M0b** | Current ETF deployed ≈ **₹2,30,000** | Snapshot from you |
| **M0c** | Funds available (cash) ≈ **₹1,19,359** | Buffer; not fully in WC |

> Educational personal system. Not investment advice. No guaranteed returns.

---

## 1. Goal of this plan

1. Capture the author’s **Class 3 compounding + capital-management** concept accurately.  
2. Map it against **what fire_shop already does**.  
3. **Melt** author rules into *our* needs (shared Zerodha cash with MTF, current ₹6k cadence, RSI rank buy, BID logic, Telegram/cron).  
4. Freeze decisions → only then design modules → only then touch code.

**Process (same as MTF):** Requirements/decisions → Design → Code.

---

## 2. Author concept (Class 3) — understood

### 2.1 Capital management
| Idea | Author rule |
|---|---|
| Split | Working capital ÷ **50** = one “soldier” / tranche |
| Pace | Buy **one tranche/day** (not all capital at once) |
| Idle cash | Remainder stays in **liquid** (soldiers not exhausted in a long drawdown) |
| Universe | ~75 high-volume ETFs; daily pick = **lowest RSI rank** (comparative; not “wait for RSI&lt;30”) |
| Re-buy after sell | Allowed if back at top rank (“potato seller” — new trade) |

### 2.2 Exit (ETF Dukaan vs Nifty Dukaan)
| Method | Min profit booking |
|---|---|
| **ETF Dukaan** (this transcript) | **≥ 4.71%** |
| **Nifty Dukaan** | **≥ 6.28%** |

- 4.71% / 6.28% are **minimums**, not hard caps.  
- Prefer **open & choose** the best winner above min; **≤ 1 sell/day** so a rising market can run.  
- Do **not** pre-feed blind limit exits (author preference).

### 2.3 Average-down (from Classes 1–2, referenced here)
- If same ETF falls enough (author cites **~3%+**), buy again to average.  
- Exit is on the **combined** book (all lots sold together when target hit).

### 2.4 Compounding after a sell (heart of Class 3)
```text
gross_profit
  → minus brokerage (estimate / broker report)
  → minus tax ≈ 20% income tax + 4% education cess  (= 20.8% of after-brokerage)
  → net_profit
  → 50% self-dividend (withdraw / lifestyle)
  → 50% growth
growth → add to working_capital
next_tranche = working_capital / 50
```

- Growth is shared across **all 50 parts** (every future buy steps up slightly).  
- Different from older Nifty-shop “pile capital at lower levels” criticism.

### 2.5 Worked numbers (author Excel, educational)
Assume start capital ₹5,00,000 → tranche ₹10,000.

| Step | After-brokerage profit | Tax (20.8%) | Net | Self-div | Growth | Working capital | Next tranche |
|---|---|---|---|---|---|---|---|
| Start | — | — | — | — | — | 5,00,000 | 10,000.00 |
| Sell 1 | 850 | 176.80 | 673.20 | 336.60 | 336.60 | 5,00,336.60 | **10,006.73** |
| Sell 2 | 925 | ~192.40 | 732.60 | 366.30 | 366.30 | 5,00,702.90 | **10,014.06** |

*(ASR in transcript garbles “₹10,006.73” as “₹10,6.73” — math above matches the sheet narrative.)*

---

## 3. What we run today (fire_shop)

| Area | Current behavior |
|---|---|
| Buy size | Fixed **`investment_per_tx` = ₹6,000** |
| Universe / rank | RSI-ranked ETF list (`fire_shop_automation.rank_instruments`) |
| Sell target | Historically **6.28%** with bid **decay → floor 3.14%**; PR #6 draft = **6.28 until capital 2× then 3.14** |
| Sell selection | One sell/run; PR #6 = most profitable eligible |
| BID | Drop ≥ **4%** from last buy, max **3** bids; add ≈ max(invested/2, ticket) |
| Compounding | **None** on working capital / ticket after sell |
| Tax / self-dividend | **Not modeled** in live engine |
| Cron | Buy ~15:00 IST; **sell cron disabled** since 2026-06-29 |
| Coexistence | MTF nursery reserves **fire_shop_daily_reserve ≈ ₹6k** |

---

## 4b. Compounding plan (in discussion — you want this to take effect)

**Intent:** After each qualifying sell (≥6.28%), book growth into FIRE working capital so the **next daily buy ticket rises**. Without sells + this ledger, compounding cannot run.

### Proposed flow (Class 3 melted to FIRE)

```text
SELL fill (one/day, most profitable ≥ 6.28%)
    │
    ├─ sell_credit = what actually lands after Zerodha brokerage/charges
    │                 (from order charges / broker net — not a side fund)
    ├─ cost_basis  = avg × qty (broker holding cost)
    ├─ reusable_profit = sell_credit − cost_basis
    │       (if ≤ 0, no growth bump)
    └─ growth = reusable_profit              ← 100%; no self-div; no tax set-aside
            │
            ▼
    working_capital += growth
    ticket = working_capital / parts         (parts TBD — D5)
            │
            ▼
    next BUY uses new ticket (NEW + BID sizing)
    # Practical meaning: all money that remains after the sell is reusable
```

**Rationale (your words):** brokerage is whatever Zerodha deducts; you can’t track a separate tax/self-div pocket — so whatever accumulates after selling, reuse all of it for the next tickets.

### Still need your call (compounding-critical)

| ID | Question | Options | Suggest |
|---|---|---|---|
| **D3** | Starting working capital? | A declared · B broker · **C `6k×50=₹3L` FROZEN** | |
| **D4** | Ticket grows with WC/parts? | A fixed · **B grow FROZEN** · C floor then grow | |
| **D5** | Parts? | **A 50 FROZEN** · B other | |
| **D6** | Post-sell split? | **B FROZEN** — no self-div; + **D6b/D6c**: actual Zerodha net, no tax set-aside | |
| **D8** | Sell cron? | **A re-enable** · B stay off | **A** — else no compounds |

### Bootstrap example (if D3=C, D5=A)
- Today ticket ₹6,000 → implied `initial_capital = 6,000 × 50 = ₹3,00,000`
- First sell: say growth ₹300 → WC = 3,00,300 → ticket = **₹6,006**
- Grows slowly at first, then accelerates (same as Class 3 sheet)

### Code touchpoints (later — not now)
1. Persist `working_capital`, `ticket`, `Σ growth`, `Σ self_div` (JSON or small ledger file)
2. On sell fill → run compound math → update ticket
3. Buy/BID use `ticket` instead of fixed `investment_per_tx`
4. Telegram: sold / growth / self-div / next ticket
5. Revert provisional 3.14% logic (D2)

### Non-goals for compounding v1
- Auto withdraw self-dividend to bank  
- Filesystem reorg  
- Changing rank signal (keep DMA-dip unless you say otherwise)  

---

### Existing holdings (clarified)

- **ETF capital already bought ≈ ₹2,30,000** (your number; matches state ETF-like sum ≈ ₹2.30L).
- **Funds available (cash) ≈ ₹1,19,359** (your number).
- Arithmetic check: ETFs + cash ≈ **₹3,49,359** (before non-ETF holdings).
- **D3 / D3b FROZEN:** keep sleeve WC = **₹3,00,000** (ticket starts ₹6,000). Treat cash above what’s needed for daily FIRE buys as a **buffer** — do **not** inflate WC to ₹3.49L.
- Non-ETF names in the same demat/state (e.g. IRCTC, AWL, SGB) are **not** part of FIRE WC / growth.
- Existing ETF positions **stay as-is**; they do not reset ticket.
- When an **ETF** holding is sold ≥ 6.28%, growth still increases WC → ticket.

---

## 4c. Brokerage / charges freeze (discuss before D3–D5)

**Why pause here:** “Brokerage” on Zerodha **CNC equity/ETF delivery is ₹0**. What actually leaves the account is **statutory + DP charges**. If we only subtract the brokerage line, growth would be wrong.

### What Zerodha deducts on FIRE sells (CNC delivery)

| Item | CNC delivery (typical) | Notes |
|---|---|---|
| Brokerage | **₹0** | Discount broker delivery |
| STT | 0.1% of sell value | Sell side |
| Exchange txn | ~0.00297% | NSE |
| SEBI | ~₹10/crore | Tiny |
| GST | 18% on (brokerage + txn charges) | |
| DP charges | ~₹13.5 + GST / scrip / day | On **sell** (demat debit); may not always appear in every API payload |
| Stamp duty | buy-side only | Already paid when you bought |

You already have a formula copy of this in `strategy_validator.py` (`calc_trade_costs`).

### How Kite can give “whatever Zerodha calculates”

After a fill, call **virtual contract note**:

`kite.get_virtual_contract_note([...])` → `POST /charges/orders`

Pass the filled order (`order_id`, symbol, CNC, qty, `average_price`).  
Response includes `charges.total` plus breakup (brokerage, STT, txn, SEBI, GST, stamp).

That matches your intent: **don’t invent a side fund — use Zerodha’s charge math**.

### Proposed growth formula (sell event)

```text
sell_value   = fill_price × qty
cost_basis   = holding_avg × qty          # already paid at buy time
sell_charges = Kite charges.total for THIS sell   (+ DP if missing from API)
gross_pnl    = sell_value − cost_basis
growth       = max(0, gross_pnl − sell_charges)   # 100% to WC; no tax set-aside
```

Buy-side charges were already cash-out when the position was built; we don’t re-deduct them on sell (avg cost stays broker avg).

### Decisions to freeze (B1–B4)

| ID | Question | Options | Recommend |
|---|---|---|---|
| **B1** | Meaning of “brokerage” for FIRE | A only brokerage line (₹0) · **B all Zerodha/statutory charges on the trade** | **B FROZEN** |
| **B2** | Source of truth | **A** Kite `/charges/orders` after fill · B formula only · C Console/manual | **A FROZEN** |
| **B3** | Fallback if API fails | **A** CNC formula (from `strategy_validator` rates) · B growth=gross (ignore charges) · C skip compound that day | **A FROZEN** |
| **B4** | DP charges | **A** include (API if present, else flat ~₹15.34) · B ignore DP | **A FROZEN** |

**Charges locked (2026-08-09).** Next: D3–D5 (starting capital / parts / ticket growth), then D8 (sell cron).

---

## 4d. Sell logic (decide before re-enabling cron)

**Already frozen that affect sells**
- Eligibility **6.38%** vs broker avg on Kite LTP (D1′); no 3.14% half-target (D2)
- Limit **LTP − 0.1%**; FIRE sleeve = **ETFs only** (M0)
- On fill → charges via Kite (B1–B4) → **100% growth** into WC (D6*) → ticket = WC/50

**What live code does today (baseline)**
- `sell_engine.py` → `engine.main(run_sell=True)`
- Eligible if `cmp >= avg × 1.0628` (provisional branch also had capital-double — **to revert**)
- Provisional: pick **most profitable %** among eligible; **one sell per run**
- Full qty CNC limit sell; price = cmp × (1 − limit_buffer)
- Cron was **15:15 IST**; currently disabled

### Decisions (S1–S8)

| ID | Decision | Status |
|---|---|---|
| **S1** | **One** sell per day | **FROZEN** |
| **S2** | Pick **highest unrealized %** among eligible | **FROZEN** |
| **S3** | Profit % vs **broker holding average price** | **FROZEN A** |
| **S4** | Sell **full position** qty | **FROZEN** |
| **S5** | **LIMIT @ LTP − 0.1%** (`price = LTP × 0.999`) | **FROZEN** |
| **S6** | Only symbols in **ETF universe** | **FROZEN** |
| **S7** | Use **Kite LTP** for eligibility + limit ref | **FROZEN** |
| **S8** | Cron **15:05 IST** (buy remains 15:00) | **FROZEN** |
| **D1′** | Eligibility threshold **6.38%** (`LTP >= avg × 1.0638`) | **FROZEN** (cushion for −0.1% limit ≈ ~6.27–6.28% if filled at limit) |
| **D8** | Re-enable sell cron after code ships | **Intent yes** |

**Note:** Eligibility uses **6.38% on LTP**; limit is **LTP − 0.1%**. If filled at the limit:  
`1.0638 × 0.999 − 1 ≈ 6.27%` — effectively protects ~**6.28%** on the print.

### S3 (locked)
**A — broker holding avg** = Zerodha blended average on the full position.

---

## 4. Melt to our needs — open decisions (must freeze)

Answer each before design/code. Recommendations are starting points only.

### D1 — Which profit minimum for FIRE ETF?
- **A.** Keep **6.28%** on the *economic* target  
- **A′.** Eligibility gate **6.38%** on LTP + sell limit LTP−0.1% ← **FROZEN** (realized ~6.28% if filled at limit)  
- **B.** Switch to author ETF **4.71%**  
- **C.** Configurable min; start at X%  

### D2 — Capital-double half-target (3.14%)?
- **A.** Drop it for FIRE; single min only ← **FROZEN** (“6.28 only”)  
- **B.** Keep as optional overlay when a position’s invested ≥ 2× original  
- **C.** Defer

### D3 — Working capital definition
What is “₹5L” for us?
- **A.** Declared FIRE sleeve only (e.g. manual `initial_capital` in config)  
- **B.** Broker CNC ETF market value + free cash earmarked for FIRE  
- **C.** Fixed parts from current ticket: `parts=50`, `working = ticket × 50` (₹3L if ticket=6k) ← **FROZEN**  
  - `initial_capital = 300000`  
  - `investment_per_tx` becomes derived: `ticket = working_capital / 50` (starts at 6000)

### D4 — Tranche / ticket sizing
- **A.** Stay fixed ₹6,000 forever  
- **B.** Author: `ticket = working_capital / 50`, grows after each booked growth ← **FROZEN**  
- **C.** Hybrid: floor ₹6,000; grow only after growth pool ≥ threshold  

### D5 — Parts count
- **A.** 50 (author) ← **FROZEN**  
- **B.** Other (e.g. 30 / 40) to match cash + MTF reserve

### D6 — Post-sell split / what counts as growth
- **A.** Full Class 3: brokerage estimate → tax 20.8% → 50% self-div / 50% growth  
- **B.** **No self-div** — 100% of reusable net into growth ← **FROZEN**  
- **D6b FROZEN:** brokerage = **actual Zerodha deduction** on the fill (charges API / net credit), not a parallel estimate you fund separately  
- **D6c FROZEN:** **no tax haircut** in the compounding ledger — you don’t segregate tax into another pot; reuse what remains after the sell  
- **C.** (absorbed into D6c for FIRE)  

**Implement hint (later):** prefer broker-reported charges on the order; fallback only if API missing.

### D7 — BID / average-down
- **A.** Keep current: **4%** drop, max **3**, size max(invested/2, ticket) ← **FROZEN**  
- **B.** Align closer to author ~**3%** threshold  
- **C.** Change bid size to **1× current ticket** only (not half-invested)  

### R1 — Buy ranking
- **A.** Keep **DMA-dip** (Yahoo CMP vs 20 DMA, volume filter) ← **FROZEN** for v1  
- **B.** Switch to Class 3 lowest-RSI rank  

### D8 — Sell ops
- **A.** Re-enable sell cron after rules freeze ← **FROZEN** (15:05 IST)  
- **B.** Keep sell manual / disabled  

### D9 — Scope of first code change (after accept)
- **A.** Ledger + ticket compound only (dry numbers / Telegram); buys still fixed until confidence  
- **B.** Full: ticket grows + sell min + compound on every fill ← **FROZEN**  

### D10 — Relation to PR #6 provisional engine tweak
- **A.** Revert capital-double / 3.14% code when we implement ← **FROZEN**  
- **B.** Keep PR #6 only if D2=B  
- **C.** Leave draft open until D1/D2 decided  

### T1 — When does growth affect ticket?
- **A.** Next buy session (sell 15:05 → ticket used from next day 15:00) ← **FROZEN**  
- **B.** Same day (would require sell before buy)

---

## 5. Design sketch (only after decisions — not for coding yet)

```text
FIRE sleeve
  initial_capital (config)
  working_capital = initial + Σ growth
  ticket = working_capital / parts          # default parts=50

Daily buy (~15:00)
  rank ETFs → try NEW at `ticket` → else BID per D7
  respect MTF cash governor (reserve ≥ next ticket)

Sell (≤1/day)
  eligible if pnl% ≥ profit_min (D1) [+ optional D2]
  pick most profitable eligible
  on fill → compound(D6) → update working_capital & ticket
  Telegram: sold / net / self-div / growth / next ticket
```

### Non-goals (v1)
- Auto bank withdrawal of self-dividend  
- Changing MTF nursery compounding  
- Guaranteed-return claims / marketing sheet automation beyond our ledger  

### Risks
- Growing ticket competes with MTF EMI + LIQUIDCASE — cash governor must use **live ticket**, not hard-coded 6k  
- Tax estimate ≠ actual ITR; ledger is planning math only  
- Existing holdings have no clean “original tranche history” — migration rule needed at implement time  

---

## 6. Acceptance gate

| Gate | Owner | Done when |
|---|---|---|
| Read / understand Class 3 | Agent + you | ✅ |
| Freeze decisions (D/B/S/R/T) | **You** | ✅ frozen |
| Design v1 modules/files | Agent | **next** — short signed design |
| Code | Agent | **only after** you say design accepted |

**Strategy leftovers cleared.** Filesystem org remains deferred. Next step: write signed design (still no code).

---

## 7. Your turn

Reply with decisions, e.g.:

```text
D1 B
D2 A
D3 A  initial_capital=300000
D4 B
D5 A
D6 A
D7 A
D8 A
D9 A
D10 A
```

Or override any recommendation. After that I’ll write the signed design (still no code until you say go).
