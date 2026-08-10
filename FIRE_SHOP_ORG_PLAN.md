# FIRE Shop — Code & File Organization Plan

**Status:** 🟡 DRAFT — agree before any moves/refactors  
**Scope:** `existing_bots/daily_etf_sip/fire_shop` only  
**Rule:** No strategy rule changes in this pass — **layout + ownership only**  
**After accept:** mechanical moves + thin wrappers so Oracle cron keeps working  

---

## 1. Problem (agreed)

Flat folder mixes:
- **Live ETF SIP** (buy/sell/token)
- **Legacy** copies (`zerodha_auto_buy*`, many `*.bak_*`)
- **Other strategies** (PKP, GFS, F&O notify, backtests)
- **Runtime state** next to source
- **Tests / results** mixed in

Hard to know what is production vs archive.

---

## 2. Target layout

```text
fire_shop/
├── README.md                 # what is live, how to run, cron map
├── pyproject.toml or requirements.txt   # optional; keep simple if you prefer
│
├── config/
│   ├── config.json           # live strategy knobs
│   ├── etf_universe.json
│   └── market_calendar_2026.json
│
├── src/fire_shop/            # ONE package = live ETF SIP
│   ├── __init__.py
│   ├── config.py             # load config paths
│   ├── calendar_ist.py
│   ├── kite_auth.py          # token load / get_kite
│   ├── notify.py             # Telegram
│   ├── market_data.py        # Yahoo fetch (pulled from automation)
│   ├── ranking.py            # volume + dip rank
│   ├── state.py              # positions_state load/save/reconcile
│   ├── orders.py             # place / confirm / cancel
│   ├── buy.py                # new + BID candidates
│   ├── sell.py               # eligibility + one sell/day
│   ├── compounding.py        # stub for later Class-3 melt (empty/pass-through now)
│   └── engine.py             # orchestrates buy/sell session
│
├── jobs/                     # cron entrypoints only (thin)
│   ├── run_buy.py            # was buy_engine.py
│   ├── run_sell.py           # was sell_engine.py
│   ├── run_token.py          # was server_generate_token.py
│   └── run_weekly_summary.py # was weekly_market_summary.py (if kept live)
│
├── data/                     # runtime / local only (gitkeep; secrets ignored)
│   ├── .kite_token           # gitignored
│   ├── positions_state.json
│   ├── order_log.json
│   └── logs/                 # or keep logs outside package on VM
│
├── tests/
│   ├── test_ranking.py
│   ├── test_state.py
│   ├── test_sell.py
│   └── test_buy.py
│
├── archive/                  # not imported by live jobs
│   ├── legacy_engines/       # zerodha_auto_buy*.py, engine.py.bak_*
│   ├── excel_automation/     # fire_shop_automation.py (Excel/email era)
│   ├── pkp/                  # pkp_engine, pkp_dashboard, pkp_state
│   ├── gfs/                  # gfs_*, results/gfs*
│   ├── fo_notify/            # daily_notify, fo_backtest, fo_state, strategies_*
│   ├── backtests/            # backtest_etf, backtest_porfolio, ranking_test
│   └── config_bak/           # config.json.bak_*
│
└── docs/
    ├── LIVE.md               # cron, env, deploy checklist
    └── STRATEGY.md           # current rules (pointer; Class-3 melt later)
```

### Design principles
| Principle | Meaning |
|---|---|
| One live product | Only `src/fire_shop` + `jobs/` are production |
| Thin jobs | Cron files import package; no business logic in jobs |
| Archive ≠ delete | Move sideways first; delete only after you confirm unused |
| Config vs state | Knobs in `config/`; broker-synced runtime in `data/` |
| Cron stability | Keep shim names `buy_engine.py` / `sell_engine.py` at root **or** update crontab in same change |
| No strategy melt yet | Compounding / 4.71% / ÷50 stay for later plan |

---

## 3. What is LIVE today (must keep working)

| Cron (Oracle) | Script today | Becomes |
|---|---|---|
| Token ~09:00 IST | `server_generate_token.py` | `jobs/run_token.py` (+ root shim optional) |
| Buy 15:00 IST | `buy_engine.py` → `engine.main(buy)` | `jobs/run_buy.py` |
| Sell 15:15 IST (disabled) | `sell_engine.py` → `engine.main(sell)` | `jobs/run_sell.py` |
| Weekly summary | `weekly_market_summary.py` | keep live **or** archive (O3) |
| Morning notify | `daily_notify.py` | **not** ETF SIP — archive under `fo_notify/` (O2) |

Shared: `.env_fire_shop`, `.kite_token`, Telegram, `etf_universe.json`, `positions_state.json`, calendar.

---

## 4. Organization decisions (freeze these)

### O1 — Package style
- **A.** `src/fire_shop/` package (recommended)  
- **B.** Flat `fire_shop/lib/` without package name  
- **C.** Minimal: only folders `live/`, `archive/`, `data/` — little split of engine  

**Recommendation:** **A**

### O2 — `daily_notify.py` (F&O / multi-strategy Telegram)
- **A.** Archive out of live FIRE path  
- **B.** Keep as separate live job under `jobs/notify_fo.py` (not inside ETF engine)  
- **C.** Leave in place for now  

**Recommendation:** **A** or **B** (not mixed into ETF package)

### O3 — `weekly_market_summary.py`
- **A.** Keep as live job  
- **B.** Archive  

**Recommendation:** **A** if you still read Sundays; else **B**

### O4 — Cron compatibility
- **A.** Root shims forever (`buy_engine.py` one-liner → jobs) so VM crontab unchanged  
- **B.** Update crontab paths in same PR + document  
- **C.** Both: shims + new paths documented  

**Recommendation:** **C**

### O5 — Runtime data on VM
- **A.** Move state under `data/` and point code there  
- **B.** Keep state files at deploy root (current habit) for less VM pain  

**Recommendation:** **B** for first reorg; **A** later if you want purity

### O6 — Scope of first PR
- **A.** Move/archive + split modules + shims + README (no rule changes)  
- **B.** Folders only (archive + live/), don’t split `engine.py` yet  
- **C.** Docs-only map first  

**Recommendation:** **B** then **A** (safer), or **A** if you want one clean cut

### O7 — Provisional capital-double code (earlier this session)
- **A.** Revert before reorg (clean baseline)  
- **B.** Keep but mark experimental; reorg around it  
- **C.** Decide with strategy plan later; reorg doesn’t touch sell math  

**Recommendation:** **A** or **C** — prefer clean baseline (**A**) before organising

---

## 5. Phased execution (after accept)

| Phase | Work | Risk |
|---|---|---|
| **P0** | Freeze O1–O7 | None |
| **P1** | Create `archive/`, move non-live files; add README map | Low |
| **P2** | Split live `engine` into package modules; jobs + shims | Medium — needs import smoke test |
| **P3** | Optional: `data/` paths, stricter `.gitignore` | Low |
| **P4** | Strategy melt (Class 3) — **separate plan**, not this one | — |

**Stop rule:** After each phase, buy dry-run / import check before next phase.

---

## 6. Acceptance gate

Reply with O1–O7, e.g.:

```text
O1 A
O2 A
O3 A
O4 C
O5 B
O6 B
O7 A
```

Then I write a short **signed org design** and only after you say **go** do we move files (still no Class-3 strategy code).
