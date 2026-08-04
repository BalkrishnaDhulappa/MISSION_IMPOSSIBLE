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

## Setup

```bash
cd mtf_nursery
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json   # edit locally; never commit
```

## Run tests

```bash
cd mtf_nursery
python -m pytest tests/ -v
```

## Run status (C1)

```bash
cd mtf_nursery
python3 jobs/run_status.py --init-step
```

## Next phases

- **C2** — Scanner port with yfinance
- **C3** — Kite read-only + dry-run executor + Telegram
- **C4** — LIQUIDCASE funding dry-run
- **C5** — Cron on Oracle beside `fire_shop`

See `../DESIGN.md` for full architecture.
