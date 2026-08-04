# MTF→F&O Zerodha System — Plan & Requirements

**Status:** ✅ Requirements frozen → ✅ **Design accepted for dry-run/demo** (`DESIGN.md`)  
**In progress:** Implementation **C0** — `mtf_nursery/` scaffold + math unit tests  
**Not started:** C1+ ledger/broker, live trading  
**Accepted non-negotiables:** no client SL; handle Zerodha margin force-square; fault tolerant; instant Telegram  
**Broker:** Zerodha only  
**Source of truth:** Course Classes 1–5 + CAR averaging transcript + uploaded Excel/Colab artifacts

> Educational system recreation for personal use. Not investment advice. No guaranteed returns.

---

## 1. Goal

Build a **Zerodha-only** operating system that follows the course method as closely as possible:

1. Scan FO stocks daily  
2. Buy ≤1 MTF position/day at current step size  
3. Manage margin via 10% buffer + 16 weekly EMIs (Zerodha workaround)  
4. Sell ≤1 winner/day at ≥6.28%  
5. Track Force / Reserve Force and compound ticket size  
6. Manage losers with weekly CAR Average Out (1/10th adds)  
7. Later: covered-call stage when holdings ≈ F&O lot  

**Delivery order (agreed):**
1. **Requirements** (this doc) → clarify & freeze  
2. **Design** → architecture, data model, flows, non-goals  
3. **Code** → implement against signed design (dry-run first)

No automation code until requirements + design are accepted.

---

## 2. Artifacts already available

| Artifact | File | Role |
|---|---|---|
| Class transcripts | `MCK_MTF_FO_CLASS_1` … `_5` | Narrative rules |
| Playbook draft | `MTF_FO_TRADING_SYSTEM.md` | Working summary |
| Compounding Excel | `MTF to F&O.xlsx` | Ticket ladder, 6.28%, tax/split, Force/RF |
| EMI / margin Excel | `Copy of MTF SIP Tracker (Smart Margin System).xlsx` | 10% buffer, 16 EMIs, status gate |
| Scanner source | `colab_code` | FO universe (210) + CAR/DMA scan |
| CAR exit/average transcript | `CAR_Avg` (= `CAR_Average`, duplicate) | Cumulative Average Reversal for losers |

---

## 3. Phases

### Phase A — Requirements (CURRENT)
- Freeze functional rules  
- Resolve open decisions (below)  
- Define inputs/outputs, constraints, non-goals  
- Acceptance criteria for “exact enough”

### Phase B — Design (NEXT, after A sign-off)
- System modules & boundaries  
- Data model (positions, EMIs, steps, ledger)  
- Zerodha integration points (what API vs manual)  
- Daily/weekly job schedules  
- Failure modes & human gates  
- Dry-run vs live modes  

### Phase C — Implementation (ONLY after B sign-off)
- C1: Ledger + rule engine (no broker)  
- C2: Scanner port  
- C3: Zerodha read-only sync  
- C4: Dry-run order intents  
- C5: Optional live orders with explicit approval  

---

## 4. Functional requirements (draft to freeze)

### FR1 — Universe & scan
- Scan **NSE F&O underlyings** from `colab_code` list (**210** tickers).  
- Compute CAR from **52-week high**, require **10 consecutive rising CAR days**.  
- Compute **30 / 50 / 200 DMA**.  
- Rank candidates by **% distance above 200 DMA** (ascending).  
- Output daily candidate list (symbol, CMP, DMAs, dist%, CAR status).

**OPEN — FR1a (must decide):** which mid-filter?
- Option A (Class 4 spoken): `30DMA > 50DMA` + hard `dist_200 ≤ 10%`
- Option B (uploaded `colab_code`): `CMP > 50DMA` + **no** hard 10% cut (sort only)
- **Recommendation pending your choice.**

### FR2 — Entry
- Max **1 new buy per trading day**.  
- Product: **MTF**.  
- Position notional ≈ current step ticket from Class 5 ladder (start **₹15,000** / averaged **3-in-1** as in Excel).  
- Only buy if status gate allows (FR4).  
- Stock must be MTF-approved on Zerodha at order time.

### FR3 — On-fill margin schedule (Zerodha path)
On each new MTF fill, create ledger row matching Smart Margin sheet:
- Inputs: buy date, symbol, total value, **actual initial margin**  
- `10%_buffer = 0.10 × total_value`  
- `total_immediate = initial_margin + 10%_buffer`  
- `broker_remaining = total_value − total_immediate`  
- `weekly_emi = broker_remaining / 16`  
- EMI due dates: buy_date+7 … +16 weeks  
- Keep buffer/EMI cash plan via **Zerodha funds + liquid ETF pool** (no ICICI-style Add Margin)

### FR4 — New-position gate
- Do **not** open a new MTF position unless free deployable funds **> remaining EMI obligation**  
  (sheet logic: unpaid future EMIs × EMI amount across open rows).  
- Display remaining obligation clearly (like sheet cell I1).

### FR5 — Exit / profit booking (winners)
- Target: **≥ 6.28%** from average (Class 5 / Excel `× 1.0628`).  
- Max **1 sell per day**, choose **highest unrealized %** among eligible.  
- No hard stop-loss in v1 (course rule).  
- Losers: hold up to **16 weeks / ~4 months** → convert to delivery; then manage via **FR9 CAR** (do not panic-sell).

### FR6 — Compounding / Force–RF
After a Force-eligible win, compute net like `MTF to F&O.xlsx`:
- Gross profit @ 6.28% (or actual if higher — **OPEN FR6a**)  
- − brokerage model − pledge model − estimated interest − tax model  
- Split: **50% self-dividend / 50% growth**  
- Growth adds to next ticket size  
- **3 Force bookings** advance the 3-in-1 step; extras = Reserve Force  

Interest rate default for Zerodha: **0.04%/day on funded amount** (`Interest = funded × 0.04% × days`).
Fees: Zerodha MTF brokerage + pledge/unpledge as in §7.1 (not the Class 5 Excel 9.85%/₹25 model).

### FR7 — Covered call stage (v2 / later)
- When qty ≈ 1 F&O lot: suggest/sell call at **next strike above avg×1.0628**.  
- **Not in v1** unless explicitly pulled in after design.

v1 delivery target: Rules engine + scanner (option A filters) + EMI/LIQUIDCASE funding plan + Force/RF ledger + dry-run order intents on Oracle Always Free.  
CAR averaging and live orders = later phases.

### FR9 — CAR averaging for losers (DEFERRED from v1)
Captured from `CAR_Avg` for later:
- Average Out iff last 10 CA values strictly rising; else Avoid Hold  
- Weekly 1/10th of original invested capital  
- Exit-after-average rule still unspecified in source video  

**v1:** do not implement CAR buys/exits. Losers: hold ≤16 weeks → delivery; manual handling until CAR phase.

---

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR1 | Deterministic rule evaluation from stored inputs (auditable) |
| NFR2 | Every decision logged (why buy/skip/sell) |
| NFR3 | Secrets (API key/secret/token) never committed to git |
| NFR4 | Zerodha rate limits / daily token login handled safely |
| NFR5 | Idempotent daily jobs (re-run same day doesn’t double-buy) |
| NFR6 | Human approval gate before first live order |
| NFR7 | No client stop-loss orders; handle Zerodha RMS margin crunch via monitor/alert/block |
| NFR8 | Fault tolerant cron jobs (isolate failures, retries, idempotency, safe block-on-unknown) |
| NFR9 | Instant Telegram alerts on CRITICAL/ERROR (not end-of-day only) |

---

## 6. Explicit non-goals (v1)

- Groww / multi-broker support  
- Guaranteed profit / strategy marketing  
- Live covered-call writing  
- Mobile app UI  
- Tax filing automation  
- Blind averaging without CAR signal  
- Hard stop-loss exits on MTF losers
---

## 7. Open decisions (block design until answered)

| # | Decision | Status / answer |
|---|---|---|
| **D1** | Scanner filters | ✅ **A** — `30DMA > 50DMA` + hard `dist_200 ≤ 10%` |
| **D2** | Starting capital / SIP | ✅ Start when comfortable seed ready — target **₹1,00,000** + ₹50k/month SIP; pace ~2 tickets/month |
| **D3** | Start ticket | ✅ **₹15,000** |
| **D4** | Winner sell | ✅ Min **6.28%**; **1 sell/day** = most profitable ≥ 6.28% |
| **D5** | Interest | ✅ Zerodha: `funded × 0.04% × days` (T+1 → sell) |
| **D6** | Fees | ✅ Zerodha actuals; brokerage = **min(0.3%, ₹20)** per order; pledge/unpledge **₹15+GST** |
| **D7** | Liquid ETF | ✅ Prefer **LIQUIDCASE** (Zerodha Nifty 1D Rate Liquid ETF); EMI float parked here; see §7.3 |
| **D8** | Runtime host | ✅ **Oracle Cloud** Free Tier (Always Free VM); see §7.4 |
| **D9** | Language stack | ✅ **Python** |
| **D10** | CAR in v1 | ✅ **Defer** |
| **D11** | CAR exit rule | ✅ **Defer** |
| **D12** | 1/10th base capital | ✅ Per video: **1/10 of original invested capital** on that name (first-buy cost base) |

### 7.1 Zerodha MTF cost model (from your FAQ screenshots)

| Item | Rule |
|---|---|
| Interest | `Interest = Funded amount × 0.04% × Holding days` (₹40 / lakh / day), from **T+1** until sold |
| Brokerage | **₹20 or 0.3% per executed order, whichever lower** |
| Pledge | **₹15 + GST** per ISIN per pledge |
| Unpledge | **₹15 + GST** per ISIN per unpledge |
| Square-off (broker) | **₹50 + GST** per order if Zerodha squares off |

Excel sheet rates (9.85%, ₹25 pledge, 0.12% brokerage) are **replaced** by these for Zerodha v1 economics.

### 7.2 D2 — capital stance (confirmed)
- Do **not** go live until ~**₹1L comfortable seed** is available (plus plan for ₹50k SIP).
- Design/code can proceed in dry-run/paper mode before capital arrives.

### 7.3 D7 — Liquid ETF + automation (important Zerodha constraint)

**Recommended ETF:** **LIQUIDCASE** (Zerodha Nifty 1D Rate Liquid ETF)
- Why: Growth NAV (no dividend hassle), large AUM, high liquidity on Zerodha, ~**5–6% p.a.** class returns (overnight-rate linked; not guaranteed)
- Alternatives: Kotak Nifty 1D Liquid (often low expense), Nippon LIQUIDBEES (highest AUM, dividend-style tracking friction)

**“Highest ROI” reality:** All major liquid ETFs track the same overnight/1D rate bucket. Differences are small (expense ratio + tracking). Prefer **liquidity + tax simplicity** over chasing 0.1–0.3% extra.

**Can automation fund MTF EMIs from liquid ETF?**
- **Yes, with a sell→cash step.**
- Critical Zerodha rule: **pledged collateral margin cannot be used for MTF** — MTF needs **cash** in the trading account.
- So automation design should be:
  1. Park EMI float in **LIQUIDCASE** (earn overnight-like return)
  2. On EMI / buffer due date: **CNC sell** enough LIQUIDCASE → cash credits to funds
  3. Keep/use that **cash** so Zerodha can cover MTF margin / your weekly top-up plan
  4. Optional: pledge LIQUIDCASE only for **F&O** collateral later — **not** as MTF cash substitute

**Same-day caveat:** plan sells so cash is available when needed (MTF sale proceeds timing differs; CNC liquid ETF sale is the funding path).

### 7.4 D8 — Oracle Cloud Free Tier (not “1 year only”)
Oracle Free Tier has **two** parts:
1. **Always Free** services (small VM, etc.) — **do not expire** after 1 year (limits can change; recently Ampere free size was reduced)
2. **Free Trial** — **$300 credits for ~30 days** (time-limited), not 1 year

So: you can host a small always-on Python worker on Always Free **indefinitely**, subject to Oracle’s current Always Free limits and idle/reclamation policies. Design for a **small VM** (e.g. within Always Free CPU/RAM caps).

---

## 8. Acceptance criteria for “Requirements done”

Requirements are frozen when:
1. Decisions **D1–D12** answered or explicitly deferred ✅ (done)
2. FR1–FR8 agreed as v1 / v2 split (CAR deferred)  
3. You confirm: **proceed to Design doc** (still no trading code)

---

## 9. Next actions

**You:** review `DESIGN.md` and reply with the acceptance checklist (or requested changes).  
**Me (after accept):** implement C0 scaffold + math tests — still **dry-run only**, no live MTF.

---

## 10. Locked summary (quick)

```text
D1: A (30>50 DMA + dist200<=10%)
D2: wait for ~₹1L comfortable seed + ₹50k SIP; ~2 tickets/month
D3: ₹15,000
D4: >=6.28% most profitable, 1 sell/day
D5: Zerodha funded × 0.04% × days
D6: min(0.3%, ₹20); pledge ₹15+GST
D7: LIQUIDCASE; sell→cash for MTF (collateral ≠ MTF cash)
D8: Oracle Always Free (not 1-year-only)
D9: Python
D10: defer CAR
D11: defer CAR exit
D12: 1/10th of original (first) invested capital per video
```
