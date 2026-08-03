# Zerodha MTF→F&O Trading System (Exact Course Spec)

Built from playlist transcripts:
`MCK_MTF_FO_CLASS_1` … `MCK_MTF_FO_CLASS_5`

Course: **How to Plant an F&O Tree in an MTF Nursery**  
Instructor: Mahesh Chandra Kaushik (SEBI RA) — educational only.

> Not investment advice. No guaranteed returns. F&O/MTF are risky. Use own discretion / RIA.

---

## Status

| Piece | Status |
|---|---|
| Class 1–5 transcripts | ✅ In repo |
| Full rule extraction (this doc) | ✅ Done |
| Excel: Class 5 `MTF to F&O.xlsx` | ✅ In repo |
| Excel: Class 3 `Copy of MTF SIP Tracker (Smart Margin System).xlsx` | ✅ In repo |
| Google Colab scanner code (`colab_code`) | ✅ In repo (210 FO tickers) |
| CAR averaging method details | ⚠️ Referenced, not fully in these 5 classes |
| Zerodha API key/secret + DDPI/MTF enabled | ❌ Still needed |
| Your starting capital | ❌ Still needed |

---

## System in one page

```
Daily scanner (FO universe ~210)
    → ≤1 MTF buy/day (closest to 200DMA, ≤10% extension)
    → On fill: park 10% buffer + schedule 16 weekly EMIs
    → Track Force/RF + compound ticket size
    → ≤1 sell/day at ≥6.28% (best winner)
    → Losers: hold ≤16 weeks → delivery → CAR average
    → When qty ≈ F&O lot: sell OTM covered call above 6.28% target
```

Broker: **Zerodha only** (interest **0.04%/day ≈ 14.6% p.a.**)

---

## A. Capital & ticket sizing (Classes 1, 2, 5)

| Rule | Value |
|---|---|
| Example start capital | ₹6,00,000 |
| Split | **40 parts** |
| Base ticket | **₹15,000** (working avg often **₹15,282.82**) |
| Idle cash | **Liquid / debt ETFs** (~5% historical example; not guaranteed) |
| F&O graduation scale | When tickets / holdings approach **~₹8–10 lakh** lot territory |

---

## B. Entry universe & scanner (Class 4)

### Universe
- **NSE F&O underlyings only** (~**210** names)
- Not the full 4,000–5,000 cash list

### NSE FO-style criteria (as taught)
- Avg delivery value (6m) **> ₹35 crore** (₹350 million)
- Free float **> 20%** and free-float value **> ₹1,500 crore**
- Market cap among **top 500**

### Daily filters (ALL required)
1. **CAR positive** = cumulative avg from **year high** rising for **10 consecutive days**
2. **Close > 30-DMA**
3. **30-DMA > 50-DMA**
4. **Close > 200-DMA**
5. Extension: **(Close / 200DMA − 1) ≤ 10%**
6. Sort: smallest % above 200DMA **first**

### Ops caps
- Buy **≤ 1 stock/day**
- Sell **≤ 1 stock/day** (highest % winner first)
- Target **6.28%** (minimum; may book higher in strong trend)

**Missing for automation:** actual Colab Python from blog (`Copy Code` button). Transcript describes behavior but does not contain full source.

---

## C. Zerodha margin / EMI method (Classes 2–3) — CRITICAL

### Why Zerodha differs from the video’s ICICI demo
- ICICI/Groww: **Add Margin** on a specific MTF position
- **Zerodha: no per-position add-margin UI** (as taught in Class 3)
- Zerodha workaround taught:
  1. Keep **10% of purchase value** as free cash/margin in account (broker can auto-utilize)
  2. Keep the **16-week EMI pool** in a **liquid ETF**
  3. Before each weekly EMI due date, move that week’s amount into Zerodha funds
  4. Sheet **status** must show enough cushion before opening new positions

### On every MTF buy
1. Record: date, symbol, purchase value, **actual initial margin** from broker
2. Immediately reserve **10% × purchase value** buffer
3. Remaining loan ≈ `purchase − initial_margin − 10%_buffer`
4. Weekly EMI = `remaining_loan ÷ 16`
5. Max hold **16 weeks / ~4 months** → then take **delivery**
6. **No stop-loss** in this system

### Example path (course numbers @ 9.85%, illustrative)
| Item | ₹ |
|---|---:|
| Position | 15,000 |
| Initial margin ~40% | 6,000 |
| Immediate +10% | 1,500 |
| Remaining loan | 7,500 |
| Weekly EMI | **468.75** |
| Interest with EMI (example) | ~120 |
| Interest without pay-down (example) | ~272 |

### Your rate (Zerodha)
Replace 9.85% with **14.6% (0.04%/day)** on **outstanding funded amount**.  
EMI pay-down still cuts interest roughly in half vs no pay-down (course claim); absolute rupees are higher at 14.6%.

### Sheet name to obtain
**“MTF Shift/SIP Tracker Smart Margin System”** (Google Sheet from Class 3 description link)

---

## D. Compounding / Force–Reserve Force (Class 5)

| Rule | Value |
|---|---|
| Gross target | **6.28%** |
| Costs | Brokerage + pledge/unpledge + interest + tax estimate |
| Split of net | **50% self-dividend / 50% growth** (add to next ticket) |
| Step advance | **3 Force (F)** profit bookings |
| Extra wins | **Reserve Force (RF)** — cash help, does not advance step alone |
| Rough long path | ~**72** three-in-one steps toward ~₹10L framing (not a guarantee) |

### Covered-call stage (when qty ≈ 1 F&O lot)
1. Target price = `avg × 1.0628`
2. Sell **next OTM call strike above target**
3. Expiry below strike → keep shares + premium  
4. Expiry above strike → deliver shares at strike + keep premium  
5. Size to **lot qty**, keep leftover cash for option margin

---

## E. Automation architecture (Zerodha)

### Modules
1. **Scanner** — daily FO universe + CAR/DMA filters → ranked candidates  
2. **Risk gate** — block buy if liquid/Zerodha cushion < remaining EMI obligations  
3. **Execution** — Kite Connect `product=MTF`, ≤1 buy/day  
4. **Margin scheduler** — on fill create 16 EMI dues; weekly fund reminders/transfers  
5. **Exit engine** — ≥6.28%, ≤1 sell/day; mark F/RF; update ticket size  
6. **Tenure guard** — alert at ~16 weeks → CNC/delivery conversion  
7. **Options module** (later) — lot-sized covered call suggestions/orders  
8. **Ledger** — Excel/Sheet-compatible state (positions, EMIs, Force/RF, PnL)

### Kite Connect notes
- Free Personal tier: orders/GTT/portfolio OK; **no live websocket/history** (need paid Connect or alternate data for scanner)
- Scanner data in course = **Yahoo Finance via Colab** (can keep that, independent of Kite)
- Daily API token requires login flow (local/manual or approved oauth redirect)

### Human gates (recommended even in “automation mode”)
- Final approve on first live orders
- Capital top-ups / liquid ETF sells for EMI week
- Tax lot handling

---

## F. Still required from you (priority order)

### 1. Excel / Google Sheet (must)
- Download from Class 3 video description: **MTF SIP/Shift Tracker Smart Margin System**
- File → Make a copy → export and commit to this repo (`.xlsx` or link)

### 2. Scanner code (must)
- From instructor blog Colab tutorial (**Copy Code** button)
- Save as e.g. `scanner_colab.py` or `.ipynb` in repo

### 3. Optional but important
- CAR method video transcript(s) (loser averaging / exit)
- Any other Excel from Class 5 (compounding / interest sheet)

### 4. Account / ops inputs
- Starting capital for this system
- Confirm Zerodha: **MTF on + DDPI on**
- Kite Connect API key & secret (when ready to code)
- Preference: **paper/sim first** vs live

---

## G. What I can do next as soon as Sheet + Colab code land

1. Recreate Sheet logic in code (EMI schedule, status gate, Zerodha 14.6%)  
2. Port scanner filters 1:1  
3. Design order + state machine for Force/RF + 1-buy/1-sell  
4. Build a dry-run bot that logs intended orders without placing them  

Until Sheet + Colab code arrive, automation would be **approximate**, not **exact as specified**.
