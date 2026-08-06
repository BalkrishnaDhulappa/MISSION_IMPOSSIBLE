#!/usr/bin/env python3
"""
Delta Exchange — BTC Options Seller (Supertrend Strategy)
==========================================================

Strategy:
  - Signal : Supertrend(16, 1.5) on BTC futures 90-min chart
  - Entry  : Supertrend color flip
               Green flip → sell PE at supertrend-value strike
               Red   flip → sell CE at supertrend-value strike
  - Strike : Supertrend value rounded to nearest $500
  - Expiry : 0 DTE if best premium > $300, else next daily expiry
  - SL     : Supertrend flips again (opposite color) → close position
  - TP     : Premium decays to ≤ $10 (near-zero) → close position
  - Size   : Capital-based (CAPITAL_USD × RISK_PCT per trade)

Usage:
  python delta_btc_options.py --mode signal   # check for new entry signal
  python delta_btc_options.py --mode monitor  # check SL / TP on open position
  python delta_btc_options.py --mode status   # print current state

Cron (every 90 min on weekdays, and monitor every 15 min):
  0 */1 * * 1-7  cd /home/ubuntu/algo_trader/claude && . /home/ubuntu/.env_fire_shop && \
    /home/ubuntu/fire_shop/venv/bin/python3 delta_btc_options.py --mode signal \
    >> /home/ubuntu/algo_trader/logs/delta_btc_options_signal.log 2>&1

  */15 * * * 1-7  cd /home/ubuntu/algo_trader/claude && . /home/ubuntu/.env_fire_shop && \
    /home/ubuntu/fire_shop/venv/bin/python3 delta_btc_options.py --mode monitor \
    >> /home/ubuntu/algo_trader/logs/delta_btc_options_monitor.log 2>&1

Note: BTC options trade 24/7 on Delta Exchange.
"""

import argparse
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────

DELTA_API_KEY    = os.environ.get("DELTA_API_KEY", "")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET", "")
BASE_URL         = "https://api.india.delta.exchange"

TELEGRAM_BOT_TOKEN = os.environ.get("CRYPTO_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("CRYPTO_TELEGRAM_CHAT_ID",   "")

# Strategy parameters
ST_PERIOD      = 16       # Supertrend ATR period
ST_MULTIPLIER  = 1.5      # Supertrend multiplier
CANDLE_RES     = "30m"    # fetch 30-min candles, resample to 90min in code
CANDLE_LIMIT   = 300      # fetch 300×30min = 150hrs; resample → 100×90min
STRIKE_STEP    = 500      # BTC option strike increment in USD
MIN_PREMIUM    = 300      # USD — use 0 DTE if premium > this, else next expiry
CLOSE_PREMIUM  = 10       # USD — close position if premium decays below this
CAPITAL_USD    = 500      # total paper/live capital in USD
RISK_PCT       = 0.10     # fraction of capital per trade (10% = $50)

BASE_DIR   = Path(__file__).resolve().parent
PAPER_DIR  = BASE_DIR / "paper_trades"
PAPER_DIR.mkdir(exist_ok=True)
STATE_FILE = PAPER_DIR / "delta_btc_options_state.json"
TRADES_FILE = PAPER_DIR / "delta_btc_options_trades.json"

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(msg: str):
    if not TELEGRAM_BOT_TOKEN:
        print(f"  [TG] {msg}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"  ⚠️  Telegram failed: {e}")

# ── Delta Exchange API auth ───────────────────────────────────────────────────

def _sign(method: str, path: str, query: str, body: str, timestamp: str) -> str:
    """HMAC-SHA256 signature as required by Delta Exchange API v2."""
    message = method + timestamp + path + query + body
    return hmac.new(
        DELTA_API_SECRET.encode(), message.encode(), hashlib.sha256
    ).hexdigest()


def delta_get(path: str, params: dict = None) -> dict:
    """Authenticated GET request to Delta Exchange."""
    params    = params or {}
    timestamp = str(int(time.time()))
    query_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    query_part = ("?" + query_str) if query_str else ""
    sig = _sign("GET", path, query_part, "", timestamp)

    headers = {
        "api-key":   DELTA_API_KEY,
        "timestamp": timestamp,
        "signature": sig,
        "Content-Type": "application/json",
    }
    url = BASE_URL + path + query_part
    r   = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


def delta_post(path: str, body: dict) -> dict:
    """Authenticated POST request to Delta Exchange."""
    timestamp  = str(int(time.time()))
    body_str   = json.dumps(body, separators=(",", ":"))
    sig = _sign("POST", path, "", body_str, timestamp)

    headers = {
        "api-key":      DELTA_API_KEY,
        "timestamp":    timestamp,
        "signature":    sig,
        "Content-Type": "application/json",
    }
    r = requests.post(BASE_URL + path, headers=headers, data=body_str, timeout=10)
    r.raise_for_status()
    return r.json()

# ── Market data ───────────────────────────────────────────────────────────────

def get_btc_futures_candles(resolution: str = CANDLE_RES, limit: int = CANDLE_LIMIT) -> list:
    """
    Fetch BTC perpetual futures 30-min candles from Delta Exchange and
    resample to 90-min candles.

    Delta Exchange does not natively serve 90-min resolution.
    Supported: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d etc.
    We fetch 30-min candles and resample every 3 candles → 1 × 90-min candle.
    """
    symbol   = "BTCUSD"
    end_ts   = int(time.time())
    # 30min × 300 candles = 150 hours of history
    start_ts = end_ts - (30 * 60 * limit)

    try:
        resp = delta_get("/v2/history/candles", {
            "symbol":     symbol,
            "resolution": resolution,   # "30m"
            "start":      start_ts,
            "end":        end_ts,
        })
        raw = []
        for c in resp.get("result", []):
            raw.append({
                "ts":     int(c["time"]),
                "open":   float(c["open"]),
                "high":   float(c["high"]),
                "low":    float(c["low"]),
                "close":  float(c["close"]),
                "volume": float(c.get("volume", 0)),
            })
        raw.sort(key=lambda x: x["ts"])
        print(f"  📊 Fetched {len(raw)} × 30min candles → resampling to 90min")

        # Resample: group every 3 × 30-min candles into 1 × 90-min candle
        candles_90 = []
        # Align to 90-min boundary: drop leading candles so groups start cleanly
        offset = len(raw) % 3
        raw    = raw[offset:]
        for i in range(0, len(raw) - 2, 3):
            group = raw[i:i+3]
            candles_90.append({
                "ts":     group[0]["ts"],
                "open":   group[0]["open"],
                "high":   max(c["high"]   for c in group),
                "low":    min(c["low"]    for c in group),
                "close":  group[2]["close"],
                "volume": sum(c["volume"] for c in group),
            })

        print(f"  📊 Resampled to {len(candles_90)} × 90min candles")
        return candles_90

    except Exception as e:
        print(f"  ❌ Candle fetch failed: {e}")
        return []


def get_btc_spot_price() -> float:
    """Fetch current BTC mark price from Delta Exchange (no auth needed)."""
    try:
        r = requests.get(f"{BASE_URL}/v2/tickers/BTCUSDT", timeout=8)
        data = r.json()
        price = float(data["result"]["mark_price"])
        print(f"  📍 BTC mark price: ${price:,.2f}")
        return price
    except Exception as e:
        print(f"  ❌ BTC price fetch failed: {e}")
        return None

# ── Supertrend calculation ────────────────────────────────────────────────────

def calculate_supertrend(candles: list, period: int = ST_PERIOD,
                          multiplier: float = ST_MULTIPLIER) -> list:
    """
    Calculate Supertrend indicator.
    Returns list of dicts with keys:
      ts, close, supertrend, direction
      direction: 1 = green (bullish), -1 = red (bearish)
    """
    if len(candles) < period + 1:
        return []

    # Step 1 — True Range and ATR
    tr_list = []
    for i in range(1, len(candles)):
        high  = candles[i]["high"]
        low   = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)

    # Wilder's smoothed ATR
    atr = [0.0] * len(tr_list)
    atr[period - 1] = sum(tr_list[:period]) / period
    for i in range(period, len(tr_list)):
        atr[i] = (atr[i - 1] * (period - 1) + tr_list[i]) / period

    # Step 2 — Basic upper/lower bands
    result = []
    prev_upper = prev_lower = prev_st = None
    prev_dir   = 1

    for i in range(period, len(candles)):
        idx   = i - 1          # atr index (offset by 1 due to TR calc)
        hl2   = (candles[i]["high"] + candles[i]["low"]) / 2
        close = candles[i]["close"]

        basic_upper = hl2 + multiplier * atr[idx]
        basic_lower = hl2 - multiplier * atr[idx]

        # Final bands with persistence
        if prev_upper is None:
            final_upper = basic_upper
            final_lower = basic_lower
        else:
            final_upper = (basic_upper
                           if basic_upper < prev_upper or candles[i-1]["close"] > prev_upper
                           else prev_upper)
            final_lower = (basic_lower
                           if basic_lower > prev_lower or candles[i-1]["close"] < prev_lower
                           else prev_lower)

        # Direction
        if prev_st is None:
            direction = 1
            st_val    = final_lower
        elif prev_st == prev_upper:
            direction = -1 if close <= final_upper else 1
            st_val    = final_lower if direction == 1 else final_upper
        else:
            direction = 1 if close >= final_lower else -1
            st_val    = final_lower if direction == 1 else final_upper

        result.append({
            "ts":         candles[i]["ts"],
            "close":      close,
            "supertrend": round(st_val, 2),
            "direction":  direction,
        })

        prev_upper = final_upper
        prev_lower = final_lower
        prev_st    = st_val
        prev_dir   = direction

    return result


def detect_flip(st_data: list) -> dict | None:
    """
    Detect if the latest candle has a supertrend direction flip.
    Returns {"flip": "green"|"red", "strike": int, "st_value": float}
    or None if no flip.
    """
    if len(st_data) < 2:
        return None

    prev = st_data[-2]
    curr = st_data[-1]

    if prev["direction"] == curr["direction"]:
        return None   # no flip

    flip_type  = "green" if curr["direction"] == 1 else "red"
    st_value   = curr["supertrend"]
    strike     = round(st_value / STRIKE_STEP) * STRIKE_STEP

    print(f"  🔀 Supertrend FLIP → {flip_type.upper()}  ST={st_value:.2f}  Strike=${strike:,}")
    return {
        "flip":     flip_type,
        "st_value": st_value,
        "strike":   strike,
        "close":    curr["close"],
        "ts":       curr["ts"],
    }

# ── Options chain ─────────────────────────────────────────────────────────────

def get_options_chain(strike: int, option_type: str) -> list:
    """
    Fetch available BTC options from Delta Exchange for a given strike.

    Delta Exchange option symbols follow the format:
      C-BTC-<strike>-<DDMMYY>   e.g. C-BTC-84000-160425
      P-BTC-<strike>-<DDMMYY>   e.g. P-BTC-84000-160425

    option_type: "call" or "put"
    Returns list of contracts sorted by expiry (nearest first).
    """
    prefix = "C" if option_type == "call" else "P"
    try:
        # Fetch all live BTC options — filter by strike and type locally
        resp = delta_get("/v2/products", {
            "contract_types": "call_options" if option_type == "call" else "put_options",
            "states":         "live",
            "page_size":      "500",
        })
        contracts = []
        for p in resp.get("result", []):
            sym = p.get("symbol", "")
            # Symbol format: C-BTC-84000-160425
            if not sym.startswith(f"{prefix}-BTC-"):
                continue
            try:
                parts      = sym.split("-")   # ["C", "BTC", "84000", "160425"]
                sym_strike = int(parts[2])
            except (IndexError, ValueError):
                continue
            if sym_strike != strike:
                continue
            expiry_str = p.get("settlement_time", "")
            contracts.append({
                "symbol":      sym,
                "product_id":  p["id"],
                "strike":      sym_strike,
                "option_type": option_type,
                "expiry":      expiry_str,
                "expiry_dt":   _parse_expiry(expiry_str),
            })
        contracts.sort(key=lambda x: x["expiry_dt"])
        print(f"  📋 Found {len(contracts)} {option_type.upper()} contracts at strike ${strike:,}")
        return contracts
    except Exception as e:
        print(f"  ❌ Options chain fetch failed: {e}")
        return []


def _parse_expiry(expiry_str: str) -> datetime:
    """Parse Delta Exchange expiry timestamp string to datetime."""
    try:
        # Delta returns ISO format: "2024-04-15T12:00:00Z"
        return datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc) + timedelta(days=365)


def get_option_premium(symbol: str) -> float:
    """Fetch current mark price (premium) for an option symbol."""
    try:
        r = requests.get(f"{BASE_URL}/v2/tickers/{symbol}", timeout=8)
        data  = r.json()
        price = float(data["result"]["mark_price"])
        return price
    except Exception as e:
        print(f"  ❌ Premium fetch failed for {symbol}: {e}")
        return 0.0


def select_contract(strike: int, option_type: str) -> dict | None:
    """
    Select the right contract:
      - Fetch all live contracts at this strike
      - Check 0 DTE premium: if > MIN_PREMIUM → use it
      - Else use next daily expiry
    Returns selected contract dict with premium, or None.
    """
    now_utc = datetime.now(timezone.utc)
    today_end = now_utc.replace(hour=12, minute=0, second=0, microsecond=0)
    if now_utc > today_end:
        today_end += timedelta(days=1)

    contracts = get_options_chain(strike, option_type)
    if not contracts:
        print(f"  ❌ No contracts found for {option_type.upper()} strike ${strike:,}")
        return None

    # Find 0 DTE contract (expires today)
    zero_dte = None
    next_exp = None
    for c in contracts:
        diff_hrs = (c["expiry_dt"] - now_utc).total_seconds() / 3600
        if 0 < diff_hrs <= 8:          # expires within 8 hours = 0 DTE
            zero_dte = c
        elif next_exp is None and diff_hrs > 8:
            next_exp = c

    # Check 0 DTE premium
    if zero_dte:
        prem = get_option_premium(zero_dte["symbol"])
        zero_dte["premium"] = prem
        print(f"  📋 0 DTE {zero_dte['symbol']}  premium=${prem:.2f}")
        if prem >= MIN_PREMIUM:
            print(f"  ✅ Using 0 DTE (premium ${prem:.2f} ≥ ${MIN_PREMIUM})")
            return zero_dte
        else:
            print(f"  ⬇️  0 DTE premium ${prem:.2f} < ${MIN_PREMIUM} — checking next expiry")

    # Fall back to next expiry
    if next_exp:
        prem = get_option_premium(next_exp["symbol"])
        next_exp["premium"] = prem
        print(f"  📋 Next expiry {next_exp['symbol']}  premium=${prem:.2f}")
        if prem > 0:
            print(f"  ✅ Using next expiry")
            return next_exp

    print(f"  ❌ No suitable contract found")
    return None

# ── Order placement ───────────────────────────────────────────────────────────

def place_sell_order(contract: dict, size: int = 1) -> dict | None:
    """
    Place a sell (short) market order for the option.
    size = number of contracts.
    Returns order response or None.
    """
    try:
        body = {
            "product_id":   contract["product_id"],
            "size":         size,
            "side":         "sell",
            "order_type":   "market_order",
            "time_in_force": "ioc",        # immediate or cancel for market
        }
        resp = delta_post("/v2/orders", body)
        print(f"  📤 Order placed: {resp.get('result', {}).get('id', '?')}")
        return resp.get("result")
    except Exception as e:
        print(f"  ❌ Order placement failed: {e}")
        return None


def place_close_order(contract: dict, size: int = 1) -> dict | None:
    """Close (buy back) an existing short option position."""
    try:
        body = {
            "product_id":    contract["product_id"],
            "size":          size,
            "side":          "buy",
            "order_type":    "market_order",
            "time_in_force": "ioc",
        }
        resp = delta_post("/v2/orders", body)
        print(f"  📤 Close order placed: {resp.get('result', {}).get('id', '?')}")
        return resp.get("result")
    except Exception as e:
        print(f"  ❌ Close order failed: {e}")
        return None

# ── Position sizing ───────────────────────────────────────────────────────────

def calc_contracts(premium: float) -> int:
    """
    Calculate number of contracts to sell based on capital and risk %.
    1 BTC option contract on Delta = 0.001 BTC notional typically.
    Premium is per contract in USD.
    We size by: max_loss_budget / premium_collected
    Starting conservative: 1 contract minimum, scale with capital.
    """
    budget = CAPITAL_USD * RISK_PCT          # e.g. $500 × 10% = $50
    if premium <= 0:
        return 1
    contracts = max(1, int(budget / premium))
    print(f"  📐 Sizing: budget=${budget:.0f}  premium=${premium:.2f}  contracts={contracts}")
    return contracts

# ── State management ──────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "position":    None,   # active position dict or None
        "capital_usd": CAPITAL_USD,
        "trade_count": 0,
    }


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def load_trades() -> list:
    if TRADES_FILE.exists():
        return json.loads(TRADES_FILE.read_text())
    return []


def save_trade(trade: dict):
    trades = load_trades()
    trades.append(trade)
    TRADES_FILE.write_text(json.dumps(trades, indent=2))

# ── Signal mode ───────────────────────────────────────────────────────────────

def run_signal():
    """
    Check for supertrend flip → enter new position.
    Skipped if a position is already open.
    """
    print(f"\n🔵 Delta BTC Options — mode=signal  {_now_ist()}")

    state = load_state()
    if state["position"]:
        sym = state["position"]["symbol"]
        print(f"  ℹ️  Position already open: {sym} — skipping signal check")
        return

    # Fetch candles and calculate supertrend
    candles = get_btc_futures_candles()
    if len(candles) < ST_PERIOD + 5:
        print(f"  ❌ Insufficient candles ({len(candles)})")
        return

    st_data = calculate_supertrend(candles)
    if not st_data:
        print("  ❌ Supertrend calculation failed")
        return

    curr = st_data[-1]
    print(f"  ST={curr['supertrend']:.2f}  Dir={'🟢 GREEN' if curr['direction']==1 else '🔴 RED'}  "
          f"Close={curr['close']:.2f}")

    # Check for flip
    flip = detect_flip(st_data)
    if not flip:
        print(f"  💤 No supertrend flip — no action")
        return

    # Determine option type to sell
    option_type = "put" if flip["flip"] == "green" else "call"
    strike      = flip["strike"]
    print(f"  🎯 Sell {option_type.upper()}  Strike=${strike:,}")

    # Select contract
    contract = select_contract(strike, option_type)
    if not contract:
        return

    # Size
    contracts = calc_contracts(contract["premium"])

    # Place order
    order = place_sell_order(contract, size=contracts)
    if not order:
        return

    ts = _now_ist()
    position = {
        "symbol":      contract["symbol"],
        "product_id":  contract["product_id"],
        "option_type": option_type,
        "strike":      strike,
        "expiry":      contract["expiry"],
        "entry_premium": contract["premium"],
        "contracts":   contracts,
        "open_ts":     ts,
        "flip_type":   flip["flip"],
        "st_at_entry": flip["st_value"],
        "order_id":    order.get("id"),
    }
    state["position"]    = position
    state["trade_count"] = state.get("trade_count", 0) + 1
    save_state(state)

    msg = (
        f"📄 <b>DELTA BTC OPTIONS — SELL {option_type.upper()}</b>\n"
        f"Symbol   : {contract['symbol']}\n"
        f"Strike   : ${strike:,}\n"
        f"Expiry   : {contract['expiry'][:10]}\n"
        f"Premium  : ${contract['premium']:.2f}\n"
        f"Contracts: {contracts}\n"
        f"ST Flip  : {flip['flip'].upper()} @ {flip['st_value']:.2f}\n"
        f"⏰ {ts}"
    )
    print(f"\n{msg}")
    send_telegram(msg)


# ── Monitor mode ──────────────────────────────────────────────────────────────

def run_monitor():
    """
    Monitor open position for:
      1. SL: supertrend flip in opposite direction → close
      2. TP: premium decays to ≤ CLOSE_PREMIUM → close
      3. Expiry: contract expired → log and clear state
    """
    print(f"\n🟡 Delta BTC Options — mode=monitor  {_now_ist()}")

    state = load_state()
    pos   = state.get("position")
    if not pos:
        print("  ℹ️  No open position")
        return

    print(f"  📌 Open: {pos['symbol']}  entry=${pos['entry_premium']:.2f}  "
          f"contracts={pos['contracts']}")

    # Check expiry
    now_utc    = datetime.now(timezone.utc)
    expiry_dt  = _parse_expiry(pos["expiry"])
    if now_utc >= expiry_dt:
        print("  ⏰ Contract expired — clearing position")
        _record_close(state, pos, exit_premium=0.0, reason="expired")
        return

    # Current premium
    curr_prem = get_option_premium(pos["symbol"])
    print(f"  💰 Current premium: ${curr_prem:.2f}  (entry=${pos['entry_premium']:.2f})")

    # TP: premium near zero
    if curr_prem <= CLOSE_PREMIUM:
        print(f"  ✅ TP hit — premium ${curr_prem:.2f} ≤ ${CLOSE_PREMIUM}")
        _close_position(state, pos, curr_prem, reason=f"premium_decay_${curr_prem:.2f}")
        return

    # SL: supertrend flip
    candles = get_btc_futures_candles()
    if len(candles) < ST_PERIOD + 5:
        print("  ⚠️  Insufficient candles for SL check")
        return

    st_data = calculate_supertrend(candles)
    if not st_data:
        return

    curr_st = st_data[-1]
    flip    = detect_flip(st_data)

    print(f"  ST={curr_st['supertrend']:.2f}  "
          f"Dir={'🟢 GREEN' if curr_st['direction']==1 else '🔴 RED'}")

    if flip:
        # Check if flip is opposite to our entry direction
        entry_flip = pos["flip_type"]   # "green" or "red"
        new_flip   = flip["flip"]
        if new_flip != entry_flip:
            print(f"  🛑 SL triggered — supertrend flipped {new_flip.upper()} "
                  f"(was {entry_flip.upper()})")
            _close_position(state, pos, curr_prem,
                            reason=f"sl_st_flip_{new_flip}")
            return

    print(f"  💤 No exit condition met — holding position")


def _close_position(state: dict, pos: dict, exit_premium: float, reason: str):
    """Place close order and record the trade."""
    order = place_close_order(
        {"product_id": pos["product_id"], "symbol": pos["symbol"]},
        size=pos["contracts"]
    )
    _record_close(state, pos, exit_premium, reason, order)


def _record_close(state: dict, pos: dict, exit_premium: float,
                  reason: str, order: dict = None):
    """Record closed trade, update state, send alert."""
    entry = pos["entry_premium"]
    n     = pos["contracts"]
    # Selling options: profit = entry premium - exit premium (we sold high, buy back low)
    pnl   = (entry - exit_premium) * n
    ts    = _now_ist()

    trade = {
        "symbol":        pos["symbol"],
        "option_type":   pos["option_type"],
        "strike":        pos["strike"],
        "expiry":        pos["expiry"],
        "open_ts":       pos["open_ts"],
        "close_ts":      ts,
        "entry_premium": round(entry, 4),
        "exit_premium":  round(exit_premium, 4),
        "contracts":     n,
        "pnl_usd":       round(pnl, 2),
        "reason":        reason,
        "order_id":      order.get("id") if order else None,
    }
    save_trade(trade)

    state["position"]    = None
    state["capital_usd"] = round(state.get("capital_usd", CAPITAL_USD) + pnl, 2)
    save_state(state)

    emoji = "✅" if pnl >= 0 else "❌"
    msg = (
        f"{emoji} <b>DELTA BTC OPTIONS — CLOSED</b>\n"
        f"Symbol   : {pos['symbol']}\n"
        f"Entry    : ${entry:.2f}  →  Exit: ${exit_premium:.2f}\n"
        f"P&L      : ${pnl:+.2f}\n"
        f"Reason   : {reason}\n"
        f"Capital  : ${state['capital_usd']:,.2f}\n"
        f"⏰ {ts}"
    )
    print(f"\n{msg}")
    send_telegram(msg)

# ── Status mode ───────────────────────────────────────────────────────────────

def run_status():
    """Print full status report."""
    print(f"\n📊 Delta BTC Options — mode=status  {_now_ist()}")

    state  = load_state()
    trades = load_trades()
    pos    = state.get("position")

    total_pnl  = sum(t["pnl_usd"] for t in trades)
    wins       = sum(1 for t in trades if t["pnl_usd"] > 0)
    win_rate   = wins / len(trades) * 100 if trades else 0

    print(f"\n  Capital  : ${state['capital_usd']:,.2f}")
    print(f"  Trades   : {len(trades)}  |  Win rate: {win_rate:.1f}%")
    print(f"  Total P&L: ${total_pnl:+.2f}")

    if pos:
        curr_prem = get_option_premium(pos["symbol"])
        unreal    = (pos["entry_premium"] - curr_prem) * pos["contracts"]
        print(f"\n  Open position:")
        print(f"    Symbol     : {pos['symbol']}")
        print(f"    Strike     : ${pos['strike']:,}")
        print(f"    Entry prem : ${pos['entry_premium']:.2f}")
        print(f"    Curr prem  : ${curr_prem:.2f}")
        print(f"    Unrealised : ${unreal:+.2f}")
        print(f"    Opened     : {pos['open_ts']}")
    else:
        print(f"\n  Open position: none")

    if trades:
        print(f"\n  Last 5 trades:")
        for t in trades[-5:]:
            emoji = "✅" if t["pnl_usd"] > 0 else "❌"
            print(f"    {emoji} {t['symbol']}  entry=${t['entry_premium']:.2f}  "
                  f"exit=${t['exit_premium']:.2f}  P&L=${t['pnl_usd']:+.2f}  [{t['reason']}]")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_ist() -> str:
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%d %H:%M IST")

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Delta Exchange BTC Options — Supertrend Seller"
    )
    parser.add_argument(
        "--mode", required=True,
        choices=["signal", "monitor", "status"],
        help="signal=check entry | monitor=check SL/TP | status=print state"
    )
    parser.add_argument(
        "--paper", action="store_true",
        help="Paper mode — log signals but skip actual order placement"
    )
    args = parser.parse_args()

    if args.paper:
        # Monkey-patch order functions to simulate without placing
        global place_sell_order, place_close_order
        def place_sell_order(contract, size=1):
            print(f"  📄 [PAPER] SELL {size}x {contract['symbol']} @ ${contract['premium']:.2f}")
            return {"id": "paper_" + str(int(time.time()))}
        def place_close_order(contract, size=1):
            print(f"  📄 [PAPER] BUY BACK {size}x {contract['symbol']}")
            return {"id": "paper_close_" + str(int(time.time()))}

    if args.mode == "signal":
        run_signal()
    elif args.mode == "monitor":
        run_monitor()
    elif args.mode == "status":
        run_status()


if __name__ == "__main__":
    main()
