# MTF→F&O Zerodha System — Plan & Requirements

**Status:** Requirements / planning phase  
**Not started:** Design sign-off, implementation, live trading  
**Broker:** Zerodha only  
**Source of truth:** Course Classes 1–5 + uploaded Excel/Colab artifacts

> Educational system recreation for personal use. Not investment advice. No guaranteed returns.

---

## 1. Goal

Build a **Zerodha-only** operating system that follows the course method as closely as possible:

1. Scan FO stocks daily  
2. Buy ≤1 MTF position/day at current step size  
3. Manage margin via 10% buffer + 16 weekly EMIs (Zerodha workaround)  
4. Sell ≤1 winner/day at ≥6.28%  
5. Track Force / Reserve Force and compound ticket size  
6. Later: covered-call stage when holdings ≈ F&O lot  

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

Interest rate default for Zerodha: **14.6%** (`B2 = 0.146`), not sheet’s 9.85%.

### FR7 — Covered call stage (v2 / later)
- When qty ≈ 1 F&O lot: suggest/sell call at **next strike above avg×1.0628**.  
- **Not in v1** unless explicitly pulled in after design.

### FR8 — Modes
- **Rules-only / paper ledger** (no broker)  
- **Dry-run** (read portfolio + emit intended orders, no place)  
- **Live** (place orders) — requires explicit future enablement  

### FR9 — CAR averaging for losers (from `CAR_Avg`)
**Definition — Cumulative Average Reversal (CAR):**
- Find **52-week / year-high date** for the symbol.  
- From that date through today, CA_n = mean(closes from high day … day n).  
- **Average Out / Buy** iff the **last 10 CA values are strictly increasing** every day.  
- Else **Avoid Hold** (if already held → hold; if not held → don’t buy).  
- Any single CA dip (even ₹0.02) in that 10-day window → Avoid Hold.  
- Price bounce alone does **not** override a falling CA.

**Averaging rules:**
- Cadence: evaluate losers **weekly** (not necessarily daily).  
- On Average Out: buy **1/10 of original invested capital** (round up to whole shares; min 1 share if price > budget).  
- Next week: re-check; average again only if still Average Out; else skip.  
- Narrative cap: up to ~**10** such weekly averages (~original capital again).

**Also applies as gate for new DMA-breakout candidates:** skip new buy that week if CA is Avoid Hold.

**Gap / OPEN:** transcript goal is “exit losing stocks,” but **exact sell/% exit after averaging is not specified** (speaker defers to “next video”).  
→ **FR9-EXIT** remains TBD until that follow-up transcript or your rule is provided.

v1 delivery target (updated): Rules engine + scanner + EMI ledger + **CAR weekly signals** + dry-run intents.  
(Live CAR average buys still behind approval gate.)

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

---

## 6. Explicit non-goals (v1)

- Groww / multi-broker support  
- Guaranteed profit / strategy marketing  
- Full CAR averaging automation  
- Live covered-call writing  
- Mobile app UI  
- Tax filing automation  

---

## 7. Open decisions (block design until answered)

| # | Decision | Options | Needed from you |
|---|---|---|---|
| D1 | Scanner mid-filter + 10% rule | A spoken / B colab_code / hybrid | Choose |
| D2 | Starting capital | e.g. ₹6L as course, or your amount | Number |
| D3 | Start ticket | Keep ₹15,000 or custom | Confirm |
| D4 | Book only at exactly 6.28% or allow higher same-day | Exact vs ≥6.28% best | Confirm |
| D5 | Interest estimate model | Use Excel G formula with 14.6%, or actual Zerodha funded interest later | Prefer for v1 |
| D6 | Brokerage/pledge model | Keep Excel (`0.12%` both sides, ₹25×2×1.18) vs Zerodha actuals | Prefer for v1 |
| D7 | Liquid ETF | Which ETF for EMI float (symbol) | Optional for v1 |
| D8 | Runtime host | Local PC / VPS / only manual daily run | Prefer |
| D9 | Language stack | Python recommended (matches Colab + kiteconnect) | Confirm or alternate |
| D10 | CAR averaging in v1? | Include weekly Average Out signals (FR9) / defer live CAR buys | Confirm |
| D11 | CAR exit after averaging | Wait for next CAR video / define your own exit % / defer | Choose |
| D12 | “Original capital” for 1/10th | Freeze at first buy cost / update after each average | Choose |

---

## 8. Acceptance criteria for “Requirements done”

Requirements are frozen when:
1. All **D1–D10** answered (or explicitly deferred)  
2. FR1–FR8 agreed as v1 / v2 split  
3. You confirm: **no coding until Design doc is also accepted**

---

## 9. Next actions

**You:** answer Section 7 decisions (even short replies).  
**Me (after that):** write **Design doc** (modules, schemas, sequences, Zerodha touchpoints) — still no trading code.  
**Then:** implement only what Design specifies, dry-run first.

---

## 10. Suggested reply template

```text
D1: A / B / hybrid (describe)
D2: capital = ₹____
D3: start ticket = ₹15000 (yes/no)
D4: sell rule = >=6.28% best of day
D5: interest = excel formula @ 14.6% for v1
D6: fees = excel model / zerodha actuals
D7: liquid ETF = ____ or defer
D8: run on = local / vps / manual
D9: python = yes
D10: CAR averaging = include weekly signals in v1 (yes/no)
D11: CAR exit rule = wait next video / my rule: ____ / defer
D12: 1/10th base = first buy cost (yes/no)
```
