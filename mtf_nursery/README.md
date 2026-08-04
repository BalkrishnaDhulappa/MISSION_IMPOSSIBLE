# MTF Nursery (Zerodha)

Automated MTF→F&O nursery system per course rules. **v1 = dry-run / demo only.**

## Phase C1 (current)

SQLite ledger + EMI persistence + gates wired to stored state (no broker):

| Module | Purpose |
|---|---|
| `src/ledger.py` | Positions, 16-week EMI rows, steps, orders_log, cash reservations |
| `src/emi_verify.py` | Repay verification (funded drop) + EMI status enum |
| `jobs/run_status.py` | JSON status summary CLI |

EMI states: `scheduled` → `due` → `pending_repay` → `verified` (or `overdue` until paid).

Manual Repay MTF on Zerodha is confirmed via API funded drop (C3) or `confirm_emi_manual()`.

## Phase C0

Scaffold + pure-math modules with unit tests:

| Module | Purpose |
|---|---|
| `src/emi.py` | 10% buffer + 16 weekly EMIs, obligation gate |
| `src/costs.py` | Zerodha interest, brokerage, pledge fees |
| `src/compounding.py` | 6.28% win → 50/50 split, Force/RF |
| `src/scanner.py` | D1=A filter logic (no live data fetch) |
| `src/gates.py` | Cash, pace, RMS buy block |
| `src/rms_guard.py` | Margin crunch severity + LIQUIDCASE sizing |
| `src/config.py` | JSON config loader |

## Setup (laptop or Oracle VM)

```bash
# 1) Get the code from GitHub
git clone https://github.com/BalkrishnaDhulappa/MISSION_IMPOSSIBLE.git
cd MISSION_IMPOSSIBLE
git checkout cursor/mtf-fo-trading-system-e0df

# 2) Python env (only pytest needed for C0/C1 tests)
cd mtf_nursery
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3) Optional local config (not required for tests/demo)
cp config.example.json config.json
```

No Zerodha API key, token, or Telegram needed until **C3**.

---

## What you can test today (no broker)

| Test | Command | What it proves |
|---|---|---|
| All unit tests (48) | `python3 -m pytest tests/ -v` | EMI math, costs, gates, ledger, scanner filters |
| Empty ledger status | `python3 jobs/run_status.py --init-step` | SQLite + step ₹15k ticket |
| **Full C1 paper demo** | `python3 jobs/demo_ledger.py --fresh` | Position → EMI due → verify fail/pass → buy gate |
| Re-open demo DB | `python3 jobs/run_status.py --db data/demo_ledger.sqlite` | Persisted state |

### Quick demo (copy-paste)

```bash
cd mtf_nursery
source .venv/bin/activate
python3 -m pytest tests/ -v
python3 jobs/demo_ledger.py --fresh
```

You should see 7 steps: add position, EMI due alert payload, failed verify (still pending), successful verify after simulated repay, buy gate blocked/allowed.

### On your Oracle VM (same steps)

```bash
cd ~
git clone https://github.com/BalkrishnaDhulappa/MISSION_IMPOSSIBLE.git
cd MISSION_IMPOSSIBLE && git checkout cursor/mtf-fo-trading-system-e0df
cd mtf_nursery && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 jobs/demo_ledger.py --fresh
```

This does **not** touch your existing `fire_shop` bot or Zerodha token.

---

## What you cannot test yet

| Feature | Phase | Needs |
|---|---|---|
| Live stock scanner | C2 | `yfinance`, network |
| Read Zerodha holdings/margins | C3 | Kite token, API key |
| Telegram EMI reminders | C3 | Bot token + chat id |
| LIQUIDCASE sell dry-run | C4 | Kite read access |
| Cron on VM beside fire_shop | C5 | C3+C4 done |

---

## Run tests only

```bash
cd mtf_nursery
python3 -m pytest tests/ -v
```

## Run status (empty or existing DB)

```bash
cd mtf_nursery
python3 jobs/run_status.py --init-step
python3 jobs/run_status.py --db data/demo_ledger.sqlite --as-of 2026-01-13
```

## Next phases

- **C2** — Scanner port with yfinance
- **C3** — Kite read-only + dry-run executor + Telegram
- **C4** — LIQUIDCASE funding dry-run
- **C5** — Cron on Oracle beside `fire_shop`

See `../DESIGN.md` for full architecture.
