import os
import json
from datetime import datetime
from pathlib import Path
import requests

# ================= CONFIG =================
SYMBOL = "NIFTYBEES"
EXCHANGE = "NSE"

WEEKLY_INVEST = 20000
PROFIT_THRESHOLD = 4000

DRY_RUN = False   # 🔴 SET True for testing, False for live trading

# ================= PATHS =================
BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "pkp_state.json"
TOKEN_FILE = BASE_DIR / ".kite_token"

# ================= ENV =================
KITE_API_KEY = os.environ.get("KITE_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


# ================= KITE =================
def get_kite():
    from kiteconnect import KiteConnect

    if not TOKEN_FILE.exists():
        raise Exception("❌ Token file missing")

    data = json.loads(TOKEN_FILE.read_text())
    token = data.get("access_token")

    if not token:
        raise Exception("❌ Empty access token")

    kite = KiteConnect(api_key=KITE_API_KEY)
    kite.set_access_token(token)

    return kite


# ================= TELEGRAM =================
def notify(msg):
    if not TELEGRAM_BOT_TOKEN:
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg
        })
    except:
        pass


# ================= STATE =================
def load_state():
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except:
        print("⚠️ State file corrupted, resetting")

    return {
        "units": 0,
        "pkp_investment": 0,
        "realized_profit": 0,
        "last_buy_date": ""
    }


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ================= HELPERS =================
def is_monday():
    return datetime.utcnow().weekday() == 0


def today_str():
    return datetime.utcnow().strftime("%Y-%m-%d")


def is_market_open():
    now = datetime.utcnow()
    hour = now.hour
    minute = now.minute

    # Market open: 03:45 to 10:00 UTC
    if hour == 3 and minute >= 45:
        return True
    if 4 <= hour < 10:
        return True
    return False


# ================= ORDER =================
def place_order(kite, qty, txn_type, price):
    if DRY_RUN:
        print(f"[DRY RUN] {txn_type} {qty} @ ₹{price:.2f}")
        return

    kite.place_order(
        variety="regular",
        exchange=EXCHANGE,
        tradingsymbol=SYMBOL,
        transaction_type=txn_type,
        quantity=qty,
        order_type="MARKET",
        product="CNC"
    )


# ================= STRATEGY =================
def run():
    if not is_market_open():
        print("⏸ Market closed, skipping execution")
        return

    kite = get_kite()
    state = load_state()

    # ----- PRICE -----
    quote = kite.ltp(f"{EXCHANGE}:{SYMBOL}")
    price = quote[f"{EXCHANGE}:{SYMBOL}"]["last_price"]

    units = state["units"]
    investment = state["pkp_investment"]

    print(f"\nPrice: ₹{price}")
    print(f"Units: {units}, Investment: {investment}")

    # ================= BUY =================
    if is_monday() and state["last_buy_date"] != today_str():
        qty = int(WEEKLY_INVEST / price)

        if qty > 0:
            place_order(kite, qty, "BUY", price)

            state["units"] += qty
            state["pkp_investment"] += WEEKLY_INVEST
            state["last_buy_date"] = today_str()

            notify(f"🟢 BUY {qty} @ ₹{price:.2f}")

    # ================= CALCULATIONS =================
    units = state["units"]
    investment = state["pkp_investment"]

    current_value = units * price
    profit = current_value - investment

    print(f"Current Value: ₹{current_value:.2f}")
    print(f"Profit: ₹{profit:.2f}")

    # ================= MULTI-CYCLE SELL =================
    if profit >= PROFIT_THRESHOLD:
        cycles = int(profit // PROFIT_THRESHOLD)
        amount = cycles * PROFIT_THRESHOLD

        qty_to_sell = int(amount / price)
        qty_to_sell = min(qty_to_sell, units)

        if qty_to_sell > 0:
            place_order(kite, qty_to_sell, "SELL", price)

            realized = qty_to_sell * price

            state["units"] -= qty_to_sell
            state["realized_profit"] += realized

            notify(
                f"🔴 SELL {qty_to_sell} @ ₹{price:.2f}\n"
                f"Cycles: {cycles}\n"
                f"Booked: ₹{realized:.0f}"
            )

    save_state(state)


# ================= ENTRY =================
if __name__ == "__main__":
    run()
