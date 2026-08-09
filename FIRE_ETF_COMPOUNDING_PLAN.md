# FIRE ETF Shop — Compounding Design Plan

**Status:** 🟡 DRAFT — understand & agree first · **no code until accepted**  
**Source transcript:** `componding_v2` (Mahesh Chandra Kaushik — ETF Dukaan Updated Version, Class 3)  
**Live system today:** Oracle `fire_shop` (CNC ETF SIP beside `mtf_nursery`)  
**Related PR (provisional):** #6 capital-double 6.28→3.14 — **hold / revisit after this plan**; do not treat as final FIRE rule set  

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

## 4. Melt to our needs — open decisions (must freeze)

Answer each before design/code. Recommendations are starting points only.

### D1 — Which profit minimum for FIRE ETF?
- **A.** Keep **6.28%** (Nifty-style; matches current bot + MTF language)  
- **B.** Switch to author ETF **4.71%**  
- **C.** Configurable min; start at X%  
- **Recommendation:** *your call* — transcript says ETF Shop = 4.71%; our live bot has always used 6.28%.

### D2 — Capital-double half-target (3.14%)?
Author Class 3 does **not** teach 6.28→3.14. That came from CAR/MTF.  
- **A.** Drop it for FIRE; single min only  
- **B.** Keep as optional overlay when a position’s invested ≥ 2× original  
- **C.** Defer  
- **Recommendation:** **A or C** until we know if FIRE should stay pure ETF-Dukaan.

### D3 — Working capital definition
What is “₹5L” for us?
- **A.** Declared FIRE sleeve only (e.g. manual `initial_capital` in config)  
- **B.** Broker CNC ETF market value + free cash earmarked for FIRE  
- **C.** Fixed parts from current ticket: `parts=50`, `working = ticket × 50` (implies ₹3L if ticket=6k)  
- **Recommendation:** **A** (simple, avoids fighting MTF cash).

### D4 — Tranche / ticket sizing
- **A.** Stay fixed ₹6,000 forever  
- **B.** Author: `ticket = working_capital / 50`, grows after each booked growth  
- **C.** Hybrid: floor ₹6,000; grow only after growth pool ≥ threshold  
- **Recommendation:** **B or C** if we adopt Class 3 compounding.

### D5 — Parts count
- **A.** 50 (author)  
- **B.** Other (e.g. 30 / 40) to match cash + MTF reserve  
- **Recommendation:** **A** unless cash math forces otherwise.

### D6 — Post-sell split
- **A.** Full Class 3: brokerage → tax 20.8% → 50% self-div / 50% growth  
- **B.** Skip self-div (100% growth into ticket) — more aggressive compound  
- **C.** Skip tax in ledger (track gross only); tax offline  
- **Recommendation:** **A** in ledger for parity with sheet; self-div can be “logged” not auto-withdrawn.

### D7 — BID / average-down
- **A.** Keep current: **4%** drop, max **3**, size max(invested/2, ticket)  
- **B.** Align closer to author ~**3%** threshold  
- **C.** Change bid size to **1× current ticket** only (not half-invested)  
- **Recommendation:** keep **A** unless you want stricter author parity.

### D8 — Sell ops
- **A.** Re-enable sell cron after rules freeze  
- **B.** Keep sell manual / disabled  
- **Recommendation:** **A** once D1–D6 frozen (else compounding never runs).

### D9 — Scope of first code change (after accept)
- **A.** Ledger + ticket compound only (dry numbers / Telegram); buys still fixed until confidence  
- **B.** Full: ticket grows + sell min + compound on every fill  
- **C.** Docs/config only first  
- **Recommendation:** **A** then **B**.

### D10 — Relation to PR #6
- **A.** Close / revert PR #6; replace with this plan’s exit rule  
- **B.** Keep PR #6 only if D2=B  
- **C.** Leave PR #6 draft open until D1/D2 decided  
- **Recommendation:** **C** now; **A** if D2≠B.

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
| Read / understand Class 3 | Agent + you | ✅ this doc §2 |
| Freeze D1–D10 | **You** | replies on each decision |
| Design v1 modules/files | Agent | short design addendum after freeze |
| Code | Agent | **only after** you say design accepted |

**No further fire_shop strategy code until you accept this plan (or a revised one).**

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
