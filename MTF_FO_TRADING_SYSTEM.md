# MTF → F&O Covered Call Trading System

Cleaned playbook from transcript `MCK_MTF_FO`  
(Source: Mahesh Chandra Kaushik — educational documentary / free course Class 5: *How to Plant F&O Trees in an MTF Nursery*)

> **Disclaimer (from source):** Educational only. Fictional story framing. Not investment advice. SEBI research cited: ~9/10 F&O traders lose money. Act only with your own discretion / registered advisor. Past structure ≠ guaranteed returns. Time to goals is not guaranteed.

---

## 1. Big idea

| Stage | What you do |
|---|---|
| **Nursery (MTF)** | Grow capital with small, repeatable MTF equity positions |
| **Orchard (F&O)** | Once a name reaches ~F&O lot size, sell **covered calls** on that inventory |

MTF is treated as the “younger brother” of F&O: same risk family, but **custom lot size** (not exchange fixed lots).

---

## 2. Capital allocation (exact values from transcript)

| Rule | Value |
|---|---|
| Example starting capital | **₹6,00,000** |
| Split into | **40 parts** |
| Base ticket size | **₹6,00,000 ÷ 40 = ₹15,000** |
| Working first position (averaged early steps) | **₹15,282.82** |
| Idle cash parking | **Liquid ETFs / Debt ETFs** (savings-like return, not guaranteed) |
| Ideal buy pace | **Maximum 1 stock per day** |
| Ideal sell pace | **Maximum 1 profitable stock per day** (highest % winner first) |

Do **not** buy all scanner hits in one day even if cash allows.

---

## 3. Position rules (MTF)

### 3.1 Entry filter (scanner)
Buy only breakout stocks that are:

- Above **30-DMA**
- Above **50-DMA**
- Above **200-DMA**
- **Positive CRB / CAR** (momentum as stated in video)

Universe focus: F&O-eligible / MTF-approved names (so later covered-call stage is possible).

### 3.2 Profit target
| Item | Value |
|---|---|
| Gross profit target | **6.28%** (π-based; π×2) |
| Alternate smaller target option mentioned | **3.14%** (π×1) |
| On ₹15,000 | **6.28% ≈ ₹942** |
| Book when position value | **₹15,000 → ₹15,942** |

Notes from transcript:
- 6.28% is treated as a **minimum** target
- Sometimes exits happen at **8–9%+** if momentum continues
- Still sell **only one** name per day in the “ideal” regime

### 3.3 Max hold / delivery
| Item | Value |
|---|---|
| Max MTF hold before forced delivery mindset | **~4 months** |
| If target not hit by then | Convert to **delivery** (stop paying MTF interest on that ticket) |
| Interest reduction method referenced | **“EMI method”** (pay down funded amount over time; see creator’s separate MTF EMI video) |

### 3.4 Force vs Reserve Force (step-up engine)

| Concept | Meaning |
|---|---|
| **Force (F)** | Profit bookings that count toward advancing the step |
| **Needed to advance** | **3 Force bookings** in the current step |
| **Reserve Force (RF)** | Extra profit bookings beyond those 3 |
| RF cash use | Full self-dividend / next-step funding help; **does not** itself advance the step |
| Tracking colors (as described) | White = open, dark green + F = Force, light green + RF = Reserve Force |

Example early ladder averages from transcript:

| Step band | Approx ticket (average of next 3) |
|---|---|
| Start | **₹15,282.82** |
| Next | **₹16,160.34** |
| Later example lines | **₹17,923.63**, **₹19,133.62**, etc. (compounding ladder) |

Long-range framing in transcript:
- Treat progress as **“3-in-1” steps**
- Example path to ~₹10L ticket: about **72** such 3-in-1 steps (216 single lines ÷ 3)
- Rough pacing example: ~1 step/month → ~6 years (explicitly **not a guarantee**)

### 3.5 Self-dividend vs growth split

After booking a winner and deducting costs + tax estimate:

| Split | Rule |
|---|---|
| **50% Self-dividend** | Personal / household use |
| **50% Growth amount** | Add to next position size |

Example from first ₹15,000 ticket in transcript (at **9.85%** interest assumption):

| Line item | Amount (₹) |
|---|---:|
| Gross profit @ 6.28% | **942.00** |
| Brokerage (example) | **37.30** |
| Pledge both sides incl. GST | **59.00** |
| Interest (max ~4 months, EMI method, @ 9.85%) | **136.48** |
| Profit after trading costs | **~709.39** |
| STCG tax example (~20% + cess framing in transcript) | **~147.55** |
| Profit after tax | **~561.83** |
| Self-dividend (50%) | **~280.92** |
| Growth add to next ticket (50%) | **~280.92** |
| Next ticket | **₹15,000 + 280.92 ≈ ₹15,280.92** |

Tax caveat from transcript:
- MTF **interest may not be deductible** if gains are reported as **capital gains**
- Deduction typically only if treated as **business income**
- Creator’s workaround narrative: treat interest as paid from self-dividend / offset by >6.28% wins

---

## 4. Cost template (editable)

Use this for every ticket:

```
Gross profit     = Position × 6.28%
− Brokerage
− Pledge/unpledge (both sides + GST)
− MTF interest (funded amount × daily rate × days)
= Pre-tax net
− Tax (as applicable)
= Distributable
→ 50% self-dividend / 50% growth
```

### Pledge formula used in transcript
- ₹25 one side × 2 sides × 1.18 GST ≈ **₹59**

(Your actual Zerodha/Groww pledge fees differ — see Section 6.)

---

## 5. F&O covered-call stage (after MTF compounding)

### 5.1 When to switch a name into call-writing
Only when holdings in that stock ≈ **1 F&O lot** (or you size MTF buys to lot size once large enough).

Examples from transcript:
- Alkem (LKM): lot **125**
- REC: lot **1575**

Rule: once near ₹10L capacity, **do not greed to max cash** — buy **lot quantity only**, keep leftover cash for option margins.

### 5.2 Strike selection rule
1. Compute MTF average buy price
2. Compute target = average × **1.0628**
3. In option chain, pick the **next OTM call strike above that target**
4. Sell that call against the shares (covered)

Examples from transcript:

| Stock | Avg | 6.28% target | Call sold |
|---|---:|---:|---|
| Alkem | 5543.50 | 5891.63 | **5900 CE** |
| REC | 358.25 | 380.74 | **385 CE** |

### 5.3 Expiry outcomes (as explained)

| Outcome | Result |
|---|---|
| Spot **below** strike at expiry | Keep shares + keep premium |
| Spot **above** strike at expiry | Deliver shares at strike + keep premium (assignment/exercise path) |

Important correction in transcript:
- If you **hold the shares** and deliver on assignment, you do **not** settle like a naked short call (buy-back mark-to-market loss framing)
- Opportunity cost can still exist if stock runs far above strike (capped upside)

### 5.4 Premium examples (illustrative numbers from video day)
- Alkem 5900 CE ask ~₹19.60 → on 125 lots ≈ **₹13,700** premium
- REC 385 CE ~₹3.75 → on 1575 lots ≈ **₹5,906.25** premium

These are **date-specific market quotes**, not constants.

---

## 6. Your brokers only: Zerodha vs Groww — what changes?

The **system rules stay the same**.  
What changes is **all-in cost**, mainly **MTF interest + pledge + brokerage**.

### 6.1 Interest rates

| Broker | MTF interest | Daily rate | Notes |
|---|---:|---:|---|
| Video example | **9.85% p.a.** | ~0.027%/day | ICICI-style plan rate used in Excel |
| **Zerodha** | **14.6% p.a.** | **0.04%/day** | Flat |
| **Groww** | **14.95% p.a.** | **0.041%/day** | Flat |

Interest is charged on the **funded portion**, not necessarily full buy value  
(e.g. if you pay 25% margin / ~4x, funded ≈ 75%).

### 6.2 Same ₹15,000 ticket — interest comparison

Assumptions for a transparent apples-to-apples view:
- Buy value = **₹15,000**
- Your margin = **25%** → funded = **₹11,250**
- Hold = **120 days** (~4 months max in system)
- No EMI pay-down (worst case). EMI method would lower all three.

| Broker | Rate | Interest on ₹11,250 for 120 days | vs video 9.85% |
|---|---:|---:|---:|
| Video @ 9.85% | 9.85% | **₹364** | baseline |
| **Zerodha** | 14.6% | **₹540** | **+₹176** |
| **Groww** | 14.95% | **₹553** | **+₹189** |

Quick formulas:
- Zerodha: `funded × 0.0004 × days`
- Groww: `funded × 0.00041 × days`

### 6.3 Scale the video’s own “max EMI interest” figure

Transcript max interest on ₹15k @ 9.85% with EMI method ≈ **₹136.48**.

If the **same EMI behaviour** is kept and only the rate changes:

| Broker | Scaled interest | Extra drag vs 9.85% |
|---|---:|---:|
| Video 9.85% | ₹136.48 | — |
| Zerodha 14.6% | **≈ ₹202** | **+₹66** |
| Groww 14.95% | **≈ ₹207** | **+₹71** |

### 6.4 Impact on the first-ticket P&L (using video’s other costs)

Hold video’s non-interest costs fixed for comparison:

| | @ 9.85% (video) | @ Zerodha 14.6% (scaled EMI) | @ Groww 14.95% (scaled EMI) |
|---|---:|---:|---:|
| Gross @ 6.28% | 942 | 942 | 942 |
| Brokerage (video example) | 37 | *use your actual* | *use your actual* |
| Pledge (video ₹59) | 59 | *see real fees below* | *see real fees below* |
| Interest | 136 | **202** | **207** |
| Pre-tax approx | 709 | **~643** | **~638** |
| After ~20% tax framing | ~562 | **~510** | **~506** |
| 50% growth add | ~281 | **~255** | **~253** |

**Meaning:** same 6.28% rule still works, but compounding is **slower** on Zerodha/Groww than on a 9.85% plan because each win leaves less growth capital.

Rough drag:
- Zerodha vs 9.85%: about **1.5×** interest cost
- Groww vs Zerodha: almost the same (Groww only ~**0.35% p.a.** higher)

### 6.5 Real fee differences on your accounts (beyond interest)

| Fee | Zerodha | Groww |
|---|---|---|
| MTF interest | **0.04%/day** | **0.041%/day** |
| MTF brokerage | 0.3% or ₹20 (lower) | ~0.1% of order value (check current) |
| Pledge / unpledge | **₹15 + GST / ISIN / request** | **₹20 / order** (as commonly listed) |
| Equity delivery (non-MTF) | ₹0 | charged |
| API for MTF automation | Free Personal order API | Limited / not Zerodha-class |

For this system:
- **Interest:** Zerodha slightly cheaper than Groww; both much costlier than 9.85% example
- **Pledge:** Zerodha usually cheaper per ISIN
- **Automation / GTT / API:** Zerodha is the better fit if you later systematize exits
- **Practical pick for this playbook:** prefer **Zerodha as primary MTF book**; Groww optional secondary

### 6.6 How to adapt the Excel logic for Zerodha / Groww

In the creator’s sheet, replace interest rate:

| Field | Set to |
|---|---|
| Zerodha | **14.6%** (or daily **0.04%**) |
| Groww | **14.95%** (or daily **0.041%**) |
| Max hold days | **120** (or actual days) |
| Funded amount | `Buy value − your margin cash` |

Optional compensation levers if interest is higher (system still intact):
1. Enforce **EMI pay-down** aggressively (biggest lever)
2. Prefer **shorter holds** (don’t wait full 4 months if near target)
3. Keep **strict 1 buy / 1 sell per day** (reduces overlapping interest days)
4. Do **not** raise leverage recklessly to “offset” interest — that raises risk, not edge
5. If using Groww + Zerodha together: put **longer holds on Zerodha**, avoid duplicate interest on both

---

## 7. Daily operating checklist

**Before market**
- [ ] Run breakout scanner (30/50/200 DMA + positive momentum)
- [ ] Confirm stock is MTF-approved on **your** broker
- [ ] Check cash vs liquid-ETF reserve
- [ ] Confirm today’s open Force count for current step

**Entries**
- [ ] Buy **at most 1** new name today
- [ ] Size = current step ticket (start **₹15,282.82** unless already stepped up)
- [ ] Product = **MTF**

**Exits**
- [ ] Rank open names by unrealized %
- [ ] If any ≥ **6.28%**, sell **only the best one** today
- [ ] Mark F or RF correctly
- [ ] Split net profit 50/50 (self-dividend / growth)
- [ ] Update next ticket size

**Risk / hygiene**
- [ ] Names open > ~4 months → plan delivery conversion
- [ ] Pay EMI / reduce funded amount on open MTF
- [ ] No naked F&O until lot-sized covered inventory exists
- [ ] When lot-sized: sell call **one strike above 6.28% target**

---

## 8. What stays identical vs what you must customize

| Keep exactly | Customize for Zerodha / Groww |
|---|---|
| 40-part capital split | Actual MTF margin % per stock |
| 6.28% target | Interest rate 14.6% / 14.95% |
| 3 Force to step up | Real brokerage + pledge fees |
| 50/50 self-dividend / growth | Tax treatment with your CA |
| 1 buy / 1 sell per day | Broker-approved MTF list |
| Covered call above target strike | Actual lot sizes / option liquidity |
| Idle cash in liquid/debt ETFs | Which account holds cash reserve |

---

## 9. Bottom line for your setup

- You **can** run this system on **Zerodha and/or Groww**.
- The method does **not** require a 9.85% broker.
- At Zerodha/Groww rates, **interest eats more of each 6.28% win**, so:
  - net compounding per cycle is slower
  - EMI discipline + shorter holds matter more
- Between your two accounts, **Zerodha is slightly better on MTF interest and usually better for systematic execution**.

---

## Source file
- Raw transcript: `MCK_MTF_FO`
- Video referenced in transcript: F&O call-writing educational story / MTF nursery course Class 5
