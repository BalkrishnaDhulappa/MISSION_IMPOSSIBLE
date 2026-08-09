# FIRE ETF Shop — Signed Design (v1)

**Status:** 🟢 **DESIGN ACCEPTED** (2026-08-09) · **implemented on branch** `cursor/fire-etf-profit-double-d711`  
**Based on:** `FIRE_ETF_COMPOUNDING_PLAN.md` (decisions frozen)  
**Scope:** `existing_bots/daily_etf_sip/fire_shop` live ETF SIP only  
**Out of scope v1:** filesystem reorg, RSI ranking, self-dividend, tax set-aside, MTF  

> Educational personal system. Not investment advice.

---

## 1. Goals

1. Keep daily ETF SIP running on Zerodha CNC.  
2. **Compound** after sells: reusable profit (after real Zerodha charges) raises working capital → raises ticket.  
3. **Sell** with clear rules (6.38% gate, LTP−0.1% limit, one/day, best %).  
4. Full cutover in one implementation pass; revert provisional capital-double (3.14%) logic.

---

## 2. Frozen rules (normative)

| Area | Rule |
|---|---|
| Sleeve WC start | ₹3,00,000 (= 6,000 × 50) |
| Parts | 50 |
| Ticket | `working_capital / 50` (starts ₹6,000; grows after booked growth) |
| Extra cash | Buffer — not added into WC |
| Sleeve contents | **ETFs only** (ignore stocks/SGB for WC/sell scan) |
| Buy rank | DMA-dip (current Yahoo/20DMA + volume) — unchanged |
| BID | −4% from `last_buy`, max 3, size `max(invested/2, ticket)` |
| Sell count | 1 / day |
| Sell pick | Highest unrealized % among eligible |
| Sell gate | `Kite LTP >= broker_avg × 1.0638` |
| Sell qty | Full position |
| Sell order | LIMIT @ `LTP × 0.999` |
| Sell universe | ETF universe ∩ holdings |
| Charges | Kite `/charges/orders` total (+ DP if missing); formula fallback |
| Growth | `max(0, sell_value − cost_basis − sell_charges)` · 100% to WC · no self-div · no tax haircut |
| Ticket timing | Growth from sell @ 15:05 applies to **next** buy session |
| Cron | Buy 15:00 IST · Sell **15:05 IST** (re-enable) · token unchanged |
| PR #6 tweak | Revert 6.28→3.14 capital-double paths |
| Manual sell | **M2:** if ETF left holdings but was in state → match Kite **day trades** SELL → book charges + growth (same ledger path). If no trade found → Telegram warn, no growth. Manual sell does **not** consume the bot’s one-sell-per-day slot. |

---

## 3. Runtime architecture

```text
cron 15:00  → jobs/buy  → engine(run_buy=True)
cron 15:05  → jobs/sell → engine(run_sell=True)
cron token  → server_generate_token (unchanged)

engine
  ├─ config + compound_ledger (WC, ticket, Σ growth)
  ├─ calendar (IST session)
  ├─ kite (token, LTP, holdings, orders, charges)
  ├─ ranking (DMA-dip)          # buys only
  ├─ state (positions_state.json)
  ├─ sell_select + place limit
  ├─ compound_on_sell_fill
  └─ buy_new + bid (sized by ticket)
```

**v1 packaging:** keep flat layout under `fire_shop/` (no big folder move). Split only if a file grows painful; org plan stays deferred.

---

## 4. Data model

### 4.1 `config.json` (knobs)

```json
{
  "initial_capital": 300000,
  "parts": 50,
  "profit_eligibility_pct": 0.0638,
  "sell_limit_buffer": 0.001,
  "bid_threshold": 0.04,
  "max_bid": 3,
  "buy_limit_buffer": 0.001,
  "order_fill_timeout_sec": 90,
  "dp_flat_fallback": 15.34
}
```

- Remove reliance on decaying 6.28/floor/3.14 as live sell math.  
- `investment_per_tx` becomes **derived** from ledger ticket (may keep field as bootstrap default only).

### 4.2 `compound_ledger.json` (new)

```json
{
  "initial_capital": 300000,
  "working_capital": 300000,
  "parts": 50,
  "ticket": 6000,
  "total_growth": 0,
  "sells": []
}
```

Each booked sell appends a small record: date, code, qty, avg, ltp, limit, fill, charges, growth, wc_after, ticket_after.

### 4.3 `positions_state.json` (existing)

Keep: `last_buy`, `bid_count`, `invested`, `last_sip`, and cache **`broker_avg`** (for M2 manual-sell cost basis).  
Drop need for `original_invested` capital-double fields (revert).  
Invested continues to reconcile from broker holdings for ETFs.

---

## 5. Flows

### 5.1 Sell (15:05 IST)

0. Run **§5.4 manual-sell reconcile** first.  
1. Market session open? else skip + Telegram.  
2. Load holdings; keep symbols in `etf_universe.json` only.  
3. Batch/quote **Kite LTP** for those symbols.  
4. Eligible if `ltp >= avg * 1.0638`.  
5. Pick max `(ltp/avg - 1)`. If none → exit quietly (optional Telegram “no sell”).  
6. Place CNC LIMIT sell, qty=full, price=`round(ltp*0.999, 1)` (tick rules as today).  
7. Wait fill (same timeout/cancel pattern as today).  
8. On FILLED:  
   - `sell_value = fill * qty`  
   - `cost_basis = avg * qty`  
   - `charges = kite.get_virtual_contract_note(...)` → `charges.total` (+ DP fallback)  
   - else formula fallback  
   - `growth = max(0, sell_value - cost_basis - charges)`  
   - `working_capital += growth`; `ticket = working_capital / parts`  
   - persist ledger (`source: "bot"`); remove symbol from state  
   - Telegram: sold / fill / charges / growth / next ticket  
9. Hard-stop on account errors (unchanged).

### 5.2 Buy (15:00 IST)

0. Run **§5.4 manual-sell reconcile** first (so overnight/manual exits book growth before sizing).  
1. Session + calendar checks (unchanged).  
2. Read **ticket** from compound ledger.  
3. Rank via DMA-dip (unchanged).  
4. NEW candidates: not in holdings; `qty = ceil(ticket / cmp)`.  
5. BID candidates: current rules with **ticket** instead of fixed 6k.  
6. Try until one FILLED; update state; Telegram.  
7. Does **not** re-read growth from a same-day bot sell (bot sell runs later at 15:05).

### 5.3 Bootstrap / migration

1. If `compound_ledger.json` missing → create with WC=300000, ticket=6000.  
2. Existing ETF holdings unchanged.  
3. Revert any capital-double helpers/tests from provisional work.  
4. Non-ETF holdings ignored by sell scan and WC.  
5. On reconcile, cache `broker_avg` into state so manual-sell cost basis is available.

### 5.4 Manual sell detection (M2)

Runs at start of sell **and** buy jobs (after loading holdings + state):

1. For each ETF symbol in `positions_state` that is **missing** from broker holdings (or qty→0):  
2. Query Kite **today’s trades** (`kite.trades()` / order trades) for CNC SELL on that symbol.  
3. If found:  
   - `sell_value` from trade fill(s) (sum if partials)  
   - `cost_basis` from cached `broker_avg` / `invested` in state  
   - charges via contract note (or formula fallback)  
   - `apply_growth` → append ledger row with `source: "manual"`  
   - remove from state  
   - Telegram: manual sell booked / growth / next ticket  
4. If **not** found (sold on a prior day, etc.):  
   - remove from state (reconcile)  
   - Telegram: “manual/external exit — **no growth booked**”  
   - do **not** invent a price  
5. Manual booking does **not** count as the bot’s S1 one-sell-per-day (bot may still sell another eligible name the same afternoon).

**Limit:** Kite day trade book is **same-day**. A manual sell yesterday that we only notice tomorrow → state cleaned, **no** auto growth (warn only).

---

## 6. Modules (implementation map)

| Module / file | Responsibility |
|---|---|
| `config.json` + loaders | Knobs above |
| `compound_ledger.py` (new) | Load/save WC, ticket, append sell records, `apply_growth` |
| `charges.py` (new) | Kite contract note + DP + formula fallback |
| `engine.py` | Orchestrate buy/sell; wire ticket + compound; revert 3.14 paths |
| `buy_engine.py` / `sell_engine.py` | Thin entrypoints (keep names for cron) |
| `fire_shop_automation.py` | Ranking only (unchanged behavior) |
| `test_compound_ledger.py` / `test_charges.py` / sell select tests | Unit tests without live broker |

---

## 7. Cron (Oracle) — target

```cron
# Buy 15:00 IST = 09:30 UTC
30 9 * * 1-5  ... buy_engine.py ...

# Sell 15:05 IST = 09:35 UTC  (RE-ENABLE)
35 9 * * 1-5  ... sell_engine.py ...
```

Token / notify / weekly jobs unchanged unless you ask.

---

## 8. Telegram (minimum)

- Sell fill: symbol, qty, fill, charges, growth, WC, next ticket  
- Sell fail / hard-stop  
- Buy fill with ticket used  
- Optional: “no eligible sell”

---

## 9. Test plan (before live cron)

1. Unit: eligibility 6.38%, ranking of best %, limit price LTP×0.999  
2. Unit: growth math with mock charges; ticket = WC/50  
3. Unit: BID sizing uses ticket  
4. Dry/ Tol: one paper sell path with token if available in env — else mock kite  
5. Deploy: update `engine` + ledger on VM; enable sell cron at 15:05; watch one session  

---

## 10. Non-goals (v1)

- Auto bank withdrawal  
- RSI shop ranking  
- Folder reorg / archive sweep  
- Inflating WC to cash+ETF market value  
- Same-day ticket bump after 15:05 sell  

---

## 11. Acceptance

Reply **“design accepted”** (or request edits).  
Only then: implement full cutover on branch, tests, then you re-enable/verify sell cron on Oracle.
