#!/usr/bin/env python3

import argparse
import json
import os
import sys
import time
import requests
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# FILES
# ─────────────────────────────────────────────────────────────

CONFIG_FILE = Path("config.json")
TOKEN_FILE = Path(".kite_token")
ORDER_LOG_FILE = Path("order_log.json")
BALANCE_LOG_FILE = Path("balance_log.csv")

# ENV VARIABLES
KITE_API_KEY = os.environ.get("KITE_API_KEY", "")
KITE_API_SECRET = os.environ.get("KITE_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

def load_config():
    if not CONFIG_FILE.exists():
        print("❌ config.json not found")
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text())


# ─────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        print("Telegram failed:", e)


# ─────────────────────────────────────────────────────────────
# TOKEN
# ─────────────────────────────────────────────────────────────

def load_token():
    if not TOKEN_FILE.exists():
        return None
    data = json.loads(TOKEN_FILE.read_text())
    if data.get("date") != date.today().isoformat():
        return None
    return data["access_token"]


def get_kite_client(access_token):
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=KITE_API_KEY)
    kite.set_access_token(access_token)
    return kite


# ─────────────────────────────────────────────────────────────
# ORDER LOG
# ─────────────────────────────────────────────────────────────

def load_order_log():
    if ORDER_LOG_FILE.exists():
        return json.loads(ORDER_LOG_FILE.read_text())
    return []


def save_order_log(log):
    ORDER_LOG_FILE.write_text(json.dumps(log, indent=2))


def already_ordered_today(code):
    log = load_order_log()
    today = date.today().isoformat()
    return any(e["date"] == today and e["code"] == code for e in log)


def total_spent_today():
    log = load_order_log()
    today = date.today().isoformat()
    return sum(e.get("invested", 0) for e in log if e["date"] == today)


# ─────────────────────────────────────────────────────────────
# BALANCE
# ─────────────────────────────────────────────────────────────

def fetch_balance(kite):
    margins = kite.margins(segment="equity")
    available = float(margins["available"]["opening_balance"])
    used = float(margins["utilised"]["debits"])
    total = float(margins["net"])
    return {"available": available, "used": used, "total": total}


def log_balance(label, balance):
    row = (f"{date.today().isoformat()},"
           f"{datetime.now().strftime('%H:%M:%S')},"
           f"{label},"
           f"{balance['available']:.2f},"
           f"{balance['used']:.2f},"
           f"{balance['total']:.2f}\n")
    if not BALANCE_LOG_FILE.exists():
        BALANCE_LOG_FILE.write_text("date,time,label,available,used,total\n")
    with open(BALANCE_LOG_FILE, "a") as f:
        f.write(row)


# ─────────────────────────────────────────────────────────────
# HOLDINGS
# ─────────────────────────────────────────────────────────────

def load_holdings_from_zerodha(kite):
    positions = kite.holdings()
    holdings = {}

    for p in positions:
        if p["exchange"] != "NSE":
            continue

        code = f"NSE:{p['tradingsymbol']}"
        avg = float(p["average_price"])
        qty = float(p["quantity"]) + float(p["t1_quantity"])

        if qty <= 0:
            continue

        holdings[code] = {
            "avg_price": avg,
            "total_qty": qty,
            "total_invested": avg * qty,
            "next_bid": avg * 0.97
        }

    print(f"Loaded {len(holdings)} holdings from Zerodha")
    return holdings


# ─────────────────────────────────────────────────────────────
# SELL LOGIC
# ─────────────────────────────────────────────────────────────

def get_sell_candidate(holdings, live_cmp, profit_pct):
    eligible = []

    for code, h in holdings.items():
        avg = h["avg_price"]
        cmp = live_cmp.get(code)
        qty = h["total_qty"]

        if not cmp or not avg:
            continue

        if cmp >= avg * (1 + profit_pct):
            eligible.append({
                "code": code,
                "cmp": cmp,
                "avg": avg,
                "qty": qty,
                "invested": h["total_invested"]
            })

    if not eligible:
        return None

    eligible.sort(key=lambda x: x["invested"], reverse=True)
    return eligible[0]


# ─────────────────────────────────────────────────────────────
# ORDER
# ─────────────────────────────────────────────────────────────

def place_limit_order(kite, code, cmp, qty, buffer, side="BUY"):
    from kiteconnect import KiteConnect

    symbol = code.replace("NSE:", "")

    if side == "BUY":
        price = round(cmp * (1 + buffer), 1)
        txn = KiteConnect.TRANSACTION_TYPE_BUY
    else:
        price = round(cmp * (1 - buffer), 1)
        txn = KiteConnect.TRANSACTION_TYPE_SELL

    order_id = kite.place_order(
        variety=KiteConnect.VARIETY_REGULAR,
        exchange=KiteConnect.EXCHANGE_NSE,
        tradingsymbol=symbol,
        transaction_type=txn,
        quantity=int(qty),
        order_type=KiteConnect.ORDER_TYPE_LIMIT,
        price=price,
        product=KiteConnect.PRODUCT_CNC,
        validity=KiteConnect.VALIDITY_DAY,
    )

    return order_id, price


# ─────────────────────────────────────────────────────────────
# SAFETY
# ─────────────────────────────────────────────────────────────

def run_safety_checks(kite, picks, config, force=False):
    if force:
        return True, "Force mode enabled"

    cash = fetch_balance(kite)["available"]

    if cash < config["min_available_cash"]:
        return False, "Low cash"

    spent_today = total_spent_today()
    total_new_spend = sum(p["cmp"] * p["qty"] for p in picks)

    if spent_today + total_new_spend > config["max_daily_spend"]:
        return False, "Daily spend limit exceeded"

    IST = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(IST)

    if now_ist.weekday() >= 5:
        return False, "Weekend"

    if now_ist.hour < 9 or now_ist.hour > 15:
        return False, "Outside market hours"

    return True, "OK"


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    config = load_config()

    access_token = load_token()
    if not access_token:
        print("No token")
        sys.exit(1)

    kite = get_kite_client(access_token)

    from fire_shop_automation import get_nse_session, rank_instruments, MASTER_ETFS

    session = get_nse_session()
    etf_ranked = rank_instruments(MASTER_ETFS, session, "ETFs")

    live_cmp = {r["code"]: r["cmp"] for r in etf_ranked}

    holdings = load_holdings_from_zerodha(kite)

    # SELL
    sell_candidate = get_sell_candidate(
        holdings,
        live_cmp,
        config["profit_target_pct"]
    )

    if sell_candidate:
        print("Selling:", sell_candidate["code"])
        place_limit_order(
            kite,
            sell_candidate["code"],
            sell_candidate["cmp"],
            sell_candidate["qty"],
            config["limit_price_buffer"],
            side="SELL"
        )

    # BUY
    today_pick = None
    for r in etf_ranked:
        if not already_ordered_today(r["code"]):
            today_pick = r
            break

    if not today_pick:
        print("No buy today")
        return

    qty = max(1, int(config["investment_per_tx"] // today_pick["cmp"]))
    today_pick["qty"] = qty

    safe, reason = run_safety_checks(kite, [today_pick], config, args.force)
    print("Safety:", reason)

    if not safe:
        return

    order_id, price = place_limit_order(
        kite,
        today_pick["code"],
        today_pick["cmp"],
        qty,
        config["limit_price_buffer"],
        side="BUY"
    )

    log = load_order_log()
    log.append({
        "date": date.today().isoformat(),
        "code": today_pick["code"],
        "qty": qty,
        "price": price,
        "order_id": order_id,
        "invested": price * qty
    })
    save_order_log(log)

    send_telegram(f"BUY {today_pick['code']} Qty {qty} @ {price}")

    print("Done")


if __name__ == "__main__":
    main()
