# MTF Nursery (Zerodha)

Automated MTF→F&O nursery system per course rules. **v1 = dry-run / demo only.**

## Phase C4 (current) — LIQUIDCASE funding dry-run

Sells **LIQUIDCASE (CNC) → cash** when free cash cannot cover EMI obligation + fire_shop ₹6k reserve.

| Module / job | Purpose |
|---|---|
| `src/liquid_funding.py` | Shortfall calc, `plan_liquid_topup`, qty-sized `OrderIntent` |
| `jobs/run_liquid_funding.py` | Standalone job — logs intent to `orders_log`, Telegram |
| `jobs/run_emi_funding.py` | EMI verify + calls liquid funding (09:45 cron) |

**Dry-run:** logs exact `qty` and ₹ estimate; no live order until `live_liquid_topup=true` + C6 sign-off.

```bash
# Manual test (needs Kite token on VM)
python3 jobs/run_liquid_funding.py \
  --env-file /home/ubuntu/.env_fire_shop \
  --token-path /home/ubuntu/fire_shop/.kite_token \
  --json

# Via cron wrapper
./scripts/mtf_cron.sh liquid_funding
```

Idempotency: one `LIQUIDCASE` sell intent per day (`YYYY-MM-DD|liquid|LIQUIDCASE`).

## Phase C5 — Cron beside fire_shop

Six scheduled jobs on Oracle VM (all **dry-run** until C6):

| Cron job | IST | Script | Purpose |
|---|---|---|---|
| `emi_funding` | 09:45 | `jobs/run_emi_funding.py` | EMI due + LIQUIDCASE funding (C4) |
| `rms_guard` | 10:00 | `jobs/run_rms_guard.py` | Margin/RMS Telegram |
| `scan` | 14:30 | `jobs/run_scan.py` | F&O D1=A scan → `data/last_scan.json` |
| `sell` | 14:40 | `jobs/run_sell.py` | Winner ≥6.28% sell intent |
| `buy` | 14:50 | `jobs/run_buy.py` | Top scan buy intent (before fire_shop 15:00) |
| `status` | 16:00 | `jobs/run_status_day.py` | EOD Telegram summary |

Install (once on VM):

```bash
cd ~/MISSION_IMPOSSIBLE/mtf_nursery
git pull
chmod +x scripts/mtf_cron.sh
mkdir -p logs

# Backup existing crontab, merge MTF lines (keep all fire_shop lines!)
crontab -l > ~/crontab_backup_$(date +%Y%m%d).txt
cat crontab_mtf_nursery.snippet >> ~/crontab_backup_$(date +%Y%m%d).txt
crontab ~/crontab_backup_$(date +%Y%m%d).txt
```

Manual test:

```bash
./scripts/mtf_cron.sh scan
tail -30 logs/scan.log
```

Logs: `mtf_nursery/logs/<job>.log`. Locks: `/tmp/mtf_<job>.lock` (via `flock` in crontab).

## Phase C2 — FO scanner (D1=A)

Scans **210 F&O stocks** via yfinance (same rules as course + colab, with D1=A filters).

```bash
# Full scan (~5–15 min) + Telegram top pick
python3 jobs/run_scan.py

# Quick test (first 10 symbols only)
python3 jobs/run_scan.py --limit 10 --no-telegram

# Output: data/last_scan.json (used by dry-run for top buy candidate)
```

Filters: rising CAR (10d) · CMP > 30 & 200 DMA · 30 DMA > 50 DMA · dist_200 ≤ 10% · rank by dist_200 asc.

## Phase C3 — real dry-run (read Zerodha, no orders)

Reads your live Kite portfolio/margins, verifies EMI repays, sends Telegram alerts. **Never places orders.**

| Module / job | Purpose |
|---|---|
| `src/kite_client.py` | Kite auth from `.kite_token` + `KITE_API_KEY` |
| `src/broker_read.py` | Holdings, MTF funded estimate, cash, LIQUIDCASE |
| `src/dry_run.py` | EMI verify + RMS + buy-gate orchestration |
| `src/executor.py` | Blocks all live orders (`mode=dry_run` only) |
| `src/notify.py` | Telegram `[MTF][INFO/WARN/CRITICAL]` |
| `jobs/run_dry_run.py` | **Main job** — run on VM after market open |
| `jobs/run_broker_snapshot.py` | Quick JSON snapshot (no telegram) |

### Oracle VM setup (real dry-run)

```bash
cd ~/MISSION_IMPOSSIBLE && git pull
cd mtf_nursery && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json   # paths already point to fire_shop

# Uses same secrets as fire_shop:
#   /home/ubuntu/.env_fire_shop  → KITE_API_KEY, TELEGRAM_*
#   /home/ubuntu/fire_shop/.kite_token  → daily token from 09:00 job

# 1) See live account (no telegram)
python3 jobs/run_broker_snapshot.py \
  --env-file /home/ubuntu/.env_fire_shop \
  --token-path /home/ubuntu/fire_shop/.kite_token

# 2) Full dry-run (telegram on) — sync MTF holdings into ledger first time
python3 jobs/run_dry_run.py \
  --env-file /home/ubuntu/.env_fire_shop \
  --token-path /home/ubuntu/fire_shop/.kite_token \
  --sync-mtf

# 3) JSON report, no telegram (testing)
python3 jobs/run_dry_run.py --no-telegram --json \
  --env-file /home/ubuntu/.env_fire_shop \
  --token-path /home/ubuntu/fire_shop/.kite_token
```

**Safety:** `mode` is locked to `dry_run` in the job. Live MTF orders require C6 + `LIVE_CONFIRM=YES`.

## Phase C1

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
| LIQUIDCASE sell dry-run | C4 | **Done** — `run_liquid_funding.py` + qty intent |
| Live MTF orders | C6 | Explicit sign-off + `LIVE_CONFIRM=YES` |

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

- **C6** — Live MTF (optional, separate approval)

See `../DESIGN.md` for full architecture.
