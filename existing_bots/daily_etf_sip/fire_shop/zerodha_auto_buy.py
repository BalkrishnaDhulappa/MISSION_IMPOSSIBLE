#!/usr/bin/env python3

import json
import os
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "config.json"
TOKEN_FILE = BASE_DIR / ".kite_token"
ETF_FILE = BASE_DIR / "etf_universe.json"
STATE_FILE = BASE_DIR / "positions_state.json"

KITE_API_KEY = os.environ.get("KITE_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
def load_config():
    if not CONFIG_FILE.exists():
        return {
            "investment_per_tx": 3000,
            "profit_target_pct": 0.0628,
            "limit_price_buffer": 0.001
        }
    return json.loads(CONFIG_FILE.read_text())

def load_etf_map():
    return json.loads(ETF_FILE.read_text())

# ─────────────────────────────────────────────
# STATE (PERSISTENCE)
# ─────────────────────────────────────────────
def load_state():
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text())

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print("Telegram error:", e)

# ─────────────────────────────────────────────
# KITE
# ─────────────────────────────────────────────
def get_kite():
    from kiteconnect import KiteConnect

    data = json.loads(TOKEN_FILE.read_text())
    kite = KiteConnect(api_key=KITE_API_KEY)
    kite.set_access_token(data["access_token"])
    return kite

# ─────────────────────────────────────────────
# ORDER
# ─────────────────────────────────────────────
def place_order(kite, code, cmp, qty, buffer, side):
    from kiteconnect import KiteConnect

    symbol = code.replace("NSE:", "")
    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)

    variety = KiteConnect.VARIETY_REGULAR if 9.15 <= now.hour <= 15.30 else KiteConnect.VARIETY_AMO

    price = round(cmp * (1 + buffer), 1) if side == "BUY" else round(cmp * (1 - buffer), 1)

    txn = KiteConnect.TRANSACTION_TYPE_BUY if side == "BUY" else KiteConnect.TRANSACTION_TYPE_SELL

    try:
        oid = kite.place_order(
            variety=variety,
            exchange=KiteConnect.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=txn,
            quantity=int(qty),
            order_type=KiteConnect.ORDER_TYPE_LIMIT,
            price=price,
            product=KiteConnect.PRODUCT_CNC,
            validity=KiteConnect.VALIDITY_DAY
        )
        print(f"✅ {side} {code} @ {price}")
        return oid, price
    except Exception as e:
        print(f"❌ Order failed {code}: {e}")
        return None, price

# ─────────────────────────────────────────────
# HOLDINGS
# ─────────────────────────────────────────────
def load_holdings(kite):
    holdings = {}

    for p in kite.holdings():
        qty = float(p["quantity"]) + float(p["t1_quantity"])
        if qty <= 0:
            continue

        code = f"NSE:{p['tradingsymbol']}"

        holdings[code] = {
            "qty": qty,
            "avg": float(p["average_price"])
        }

    return holdings

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():

    print("🚀 Starting ETF engine...")

    config = load_config()
    etf_map = load_etf_map()
    state = load_state()
    kite = get_kite()

    from fire_shop_automation import get_nse_session, rank_instruments

    session = get_nse_session()
    instruments = [(code, code.replace("NSE:", "")) for code in etf_map.keys()]
    ranked = rank_instruments(instruments, session, "ETF")

    holdings = load_holdings(kite)

    now = datetime.now()

    # ───────────────────────── SELL ─────────────────────────
    for code, h in holdings.items():
        cmp = next((r["cmp"] for r in ranked if r["code"] == code), None)
        if not cmp:
            continue

        if cmp >= h["avg"] * (1 + config["profit_target_pct"]):
            print(f"🔴 SELL {code}")

            place_order(kite, code, cmp, h["qty"], config["limit_price_buffer"], "SELL")

            state.pop(code, None)
            save_state(state)
            return

    # ───────────────────────── BID ─────────────────────────
    for code, h in holdings.items():

        if code not in state:
            state[code] = {
                "last_buy": h["avg"],
                "bid_count": 0,
                "invested": h["avg"] * h["qty"],
                "last_sip": None
            }

        s = state[code]
        cmp = next((r["cmp"] for r in ranked if r["code"] == code), None)

        if not cmp:
            continue

        if cmp <= s["last_buy"] * 0.975 and s["bid_count"] < 3:

            amt = s["invested"] / 2
            qty = int(amt // cmp)

            if qty > 0:
                print(f"🔁 BID {code} (level {s['bid_count']+1})")

                place_order(kite, code, cmp, qty, config["limit_price_buffer"], "BUY")

                s["last_buy"] = cmp
                s["invested"] += amt
                s["bid_count"] += 1

                save_state(state)
                return

    # ───────────────────────── SIP ─────────────────────────
    for code, s in state.items():

        if s["bid_count"] < 3:
            continue

        last = s["last_sip"]
        if last:
            last_dt = datetime.fromisoformat(last)
            if last_dt.year == now.year and last_dt.month == now.month:
                continue

        cmp = next((r["cmp"] for r in ranked if r["code"] == code), None)
        if not cmp:
            continue

        qty = int(config["investment_per_tx"] // cmp)

        if qty > 0:
            print(f"📅 SIP BUY {code}")

            place_order(kite, code, cmp, qty, config["limit_price_buffer"], "BUY")

            s["last_sip"] = now.isoformat()
            save_state(state)
            return

    # ───────────────────────── NEW BUY ─────────────────────────
    for r in ranked:
        code = r["code"]

        if code in holdings:
            continue

        qty = int(config["investment_per_tx"] // r["cmp"])

        if qty > 0:
            print(f"🟢 NEW BUY {code}")

            place_order(kite, code, r["cmp"], qty, config["limit_price_buffer"], "BUY")

            state[code] = {
                "last_buy": r["cmp"],
                "bid_count": 0,
                "invested": qty * r["cmp"],
                "last_sip": None
            }

            save_state(state)
            return

    print("⚠️ Nothing to do")

# ─────────────────────────────────────────────
if __name__ == "__main__":
    main()
