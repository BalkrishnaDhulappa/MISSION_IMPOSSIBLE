#!/usr/bin/env python3
"""
Algo Trader — Delta Exchange Adapter
Mirrors the get_kite() / place_order() pattern from FIRE Shop engine.py exactly.
No daily token refresh needed — API key + secret are permanent.

Env vars required (add to .env_fire_shop or separate .env_algo):
  DELTA_API_KEY    — Delta Exchange India API key
  DELTA_API_SECRET — Delta Exchange India API secret

Delta Exchange India base URL: https://api.india.delta.exchange
"""

import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent

DELTA_API_KEY    = os.environ.get("DELTA_API_KEY", "")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET", "")
DELTA_BASE_URL   = "https://api.india.delta.exchange"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "")

# ── Instrument map ────────────────────────────────────────────────────────────
# product_id from Delta Exchange India (verify these via GET /v2/products)
PRODUCT_IDS = {
    "BTCUSD":  "BTCUSD",   # BTC perpetual — symbol used in API calls
    "ETHUSD":  "ETHUSD",
}

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN:
        print(f"  [Telegram] {msg}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"  ⚠️  Telegram failed: {e}")

# ── Auth — HMAC signature ─────────────────────────────────────────────────────

def _sign_request(method, path, query_string="", body=""):
    """
    Delta Exchange HMAC-SHA256 signature.
    Format: method + timestamp + path + query_string + body
    """
    timestamp   = str(int(time.time()))
    msg         = method + timestamp + path + query_string + body
    signature   = hmac.new(
        DELTA_API_SECRET.encode(),
        msg.encode(),
        hashlib.sha256
    ).hexdigest()
    return timestamp, signature

def _headers(method, path, query_string="", body=""):
    ts, sig = _sign_request(method, path, query_string, body)
    return {
        "api-key":        DELTA_API_KEY,
        "timestamp":      ts,
        "signature":      sig,
        "Content-Type":   "application/json",
        "User-Agent":     "python-3/algo-trader",
    }

# ── REST helpers ──────────────────────────────────────────────────────────────

def _get(path, params=None):
    qs = ""
    if params:
        from urllib.parse import urlencode
        qs = "?" + urlencode(params)
    url  = DELTA_BASE_URL + path + (qs if qs else "")
    hdrs = _headers("GET", path, qs)
    r    = requests.get(url, headers=hdrs, timeout=10)
    r.raise_for_status()
    return r.json()

def _post(path, payload):
    body = json.dumps(payload, separators=(",", ":"))
    hdrs = _headers("POST", path, "", body)
    r    = requests.post(DELTA_BASE_URL + path, headers=hdrs, data=body, timeout=10)
    r.raise_for_status()
    return r.json()

def _delete(path, payload=None):
    body = json.dumps(payload, separators=(",", ":")) if payload else ""
    hdrs = _headers("DELETE", path, "", body)
    r    = requests.delete(DELTA_BASE_URL + path, headers=hdrs,
                           data=body if body else None, timeout=10)
    r.raise_for_status()
    return r.json()

# ── Public: get live price ────────────────────────────────────────────────────

def get_ticker(symbol="BTCUSD"):
    """Get latest ticker for a symbol. No auth needed."""
    try:
        data = requests.get(
            f"{DELTA_BASE_URL}/v2/tickers/{symbol}",
            timeout=8
        ).json()
        return data.get("result", {})
    except Exception as e:
        print(f"  ❌ Ticker fetch failed: {e}")
        return {}

def get_cmp(symbol="BTCUSD"):
    """Current market price — mark price preferred for perpetuals."""
    ticker = get_ticker(symbol)
    return float(ticker.get("mark_price") or ticker.get("close", 0))

# ── Account ───────────────────────────────────────────────────────────────────

def get_balance():
    """Get wallet balance. Returns dict of {asset: available_balance}."""
    try:
        data = _get("/v2/wallet/balances")
        result = {}
        for item in data.get("result", []):
            result[item["asset_symbol"]] = float(item.get("available_balance", 0))
        return result
    except Exception as e:
        print(f"  ❌ Balance fetch failed: {e}")
        return {}

def get_positions():
    """Get all open positions."""
    try:
        data = _get("/v2/positions/margined")
        return data.get("result", [])
    except Exception as e:
        print(f"  ❌ Position fetch failed: {e}")
        return []

def get_position(symbol="BTCUSD"):
    """Get position for a specific symbol."""
    positions = get_positions()
    for p in positions:
        if p.get("product_symbol") == symbol:
            return p
    return None

# ── Orders ────────────────────────────────────────────────────────────────────

def place_order(symbol, side, qty, order_type="limit_order",
                price=None, reduce_only=False, label=""):
    """
    Place an order on Delta Exchange.
    Mirrors FIRE Shop place_order() signature and behaviour.

    symbol      — e.g. "BTCUSD"
    side        — "buy" or "sell"
    qty         — number of contracts (1 contract = $1 of BTC at mark price)
    order_type  — "limit_order" or "market_order"
    price       — required for limit orders
    reduce_only — True to only reduce an existing position
    label       — description for Telegram alert
    """
    payload = {
        "product_symbol": symbol,
        "order_type":     order_type,
        "side":           side,
        "size":           int(qty),
        "reduce_only":    reduce_only,
        "time_in_force":  "gtc",
    }

    if order_type == "limit_order" and price:
        payload["limit_price"] = str(round(price, 1))
    elif order_type == "market_order":
        pass  # no price for market orders

    display_price = f"@ {price:.1f}" if price else "@ market"
    tag = f"[{label}] " if label else ""

    try:
        data   = _post("/v2/orders", payload)
        result = data.get("result", {})
        oid    = result.get("id", "?")

        msg = f"✅ {tag}{side.upper()} {qty} {symbol} {display_price}"
        print(f"  {msg}  (order_id={oid})")
        send_telegram(msg)
        return oid, price

    except Exception as e:
        err = str(e)
        msg = f"❌ Order failed {tag}{symbol}: {err}"
        print(f"  {msg}")
        send_telegram(msg)
        return None, price

# ── Stop loss ─────────────────────────────────────────────────────────────────

def place_stop_loss(symbol, side, qty, stop_price):
    """Place a stop-loss order (stop_market)."""
    payload = {
        "product_symbol":  symbol,
        "order_type":      "market_order",
        "stop_order_type": "stop_loss_market",
        "side":            side,
        "size":            int(qty),
        "stop_price":      str(round(stop_price, 1)),
        "reduce_only":     True,
        "time_in_force":   "gtc",
    }
    try:
        data = _post("/v2/orders", payload)
        oid  = data.get("result", {}).get("id", "?")
        print(f"  🛑 Stop-loss placed {symbol} {side} @ {stop_price:.1f}  (id={oid})")
        return oid
    except Exception as e:
        print(f"  ❌ Stop-loss failed {symbol}: {e}")
        return None

# ── Cancel ────────────────────────────────────────────────────────────────────

def cancel_order(order_id, symbol):
    try:
        _delete(f"/v2/orders/{order_id}", {"product_symbol": symbol})
        print(f"  🗑️  Order {order_id} cancelled")
    except Exception as e:
        print(f"  ⚠️  Cancel failed {order_id}: {e}")

def cancel_all_orders(symbol):
    try:
        _delete("/v2/orders/all", {"product_symbol": symbol, "cancel_limit_orders": True})
        print(f"  🗑️  All orders cancelled for {symbol}")
    except Exception as e:
        print(f"  ⚠️  Cancel all failed: {e}")

# ── Quantity helper ───────────────────────────────────────────────────────────

def calc_qty(capital_inr, cmp, leverage=1):
    """
    Calculate number of Delta contracts for a given capital.
    Delta perpetual contracts: 1 contract = 1 USD notional of BTC.
    At 1x leverage: qty = capital_in_USD (approx capital_inr / 85 for USD rate).
    For simplicity we compute qty = floor(capital_inr / cmp_inr * leverage).
    Adjust INR_PER_USD based on current rate.
    """
    INR_PER_USD = float(os.environ.get("INR_PER_USD", "85"))
    capital_usd = capital_inr / INR_PER_USD
    qty         = int(capital_usd * leverage)   # 1 contract = $1 notional
    return max(1, qty)

# ── Diagnostics ───────────────────────────────────────────────────────────────

def print_account_summary(symbol="BTCUSD"):
    """Print live account state. Useful for debugging."""
    cmp      = get_cmp(symbol)
    balance  = get_balance()
    position = get_position(symbol)

    print(f"\n  {'─'*45}")
    print(f"  Delta Exchange Account — {symbol}")
    print(f"  {'─'*45}")
    print(f"  CMP       : {cmp:,.2f}")
    print(f"  Balances  : {balance}")
    if position:
        size = float(position.get("size", 0))
        ep   = float(position.get("entry_price", 0))
        upnl = float(position.get("unrealized_pnl", 0))
        print(f"  Position  : {size} contracts @ {ep:.2f}  (uPnL: {upnl:.4f})")
    else:
        print(f"  Position  : flat")
    print(f"  {'─'*45}\n")


if __name__ == "__main__":
    # Quick diagnostics when run directly
    print("🔍 Delta Exchange Adapter — diagnostics")
    print_account_summary("BTCUSD")
