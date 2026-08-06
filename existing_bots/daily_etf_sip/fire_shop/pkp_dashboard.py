import os
import json
import streamlit as st
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "pkp_state.json"
TOKEN_FILE = BASE_DIR / ".kite_token"

KITE_API_KEY = os.environ.get("KITE_API_KEY", "")

SYMBOL = "NIFTYBEES"
EXCHANGE = "NSE"


def get_kite():
    from kiteconnect import KiteConnect

    data = json.loads(TOKEN_FILE.read_text())
    token = data.get("access_token")

    kite = KiteConnect(api_key=KITE_API_KEY)
    kite.set_access_token(token)

    return kite


def load_state():
    return json.loads(STATE_FILE.read_text())


def get_price(kite):
    quote = kite.ltp(f"{EXCHANGE}:{SYMBOL}")
    return quote[f"{EXCHANGE}:{SYMBOL}"]["last_price"]


st.set_page_config(layout="wide")
st.title("🌳 PKP Dashboard")

kite = get_kite()
state = load_state()

price = get_price(kite)

units = state["units"]
investment = state["pkp_investment"]
realized = state.get("realized_profit", 0)

current_value = units * price
profit = current_value - investment
pkp_avg = investment / units if units > 0 else 0

diff = price - pkp_avg
diff_pct = (diff / pkp_avg * 100) if pkp_avg else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Price", f"₹{price:.2f}")
c2.metric("Units", units)
c3.metric("PKP Avg", f"₹{pkp_avg:.2f}")
c4.metric("Break-even", f"₹{pkp_avg:.2f}")

c5, c6, c7, c8 = st.columns(4)
c5.metric("Investment", f"₹{investment:,.0f}")
c6.metric("Value", f"₹{current_value:,.0f}")
c7.metric("Profit", f"₹{profit:,.0f}")
c8.metric("Realized", f"₹{realized:,.0f}")

if diff >= 0:
    st.success(f"In Profit: +₹{diff:.2f} ({diff_pct:.2f}%)")
else:
    st.error(f"In Loss: ₹{diff:.2f} ({diff_pct:.2f}%)")

threshold = 4000
cycles = int(profit // threshold) if profit > 0 else 0

st.subheader("🔁 Sell Cycles")
st.write(f"Cycles available: {cycles}")
st.write(f"Extractable: ₹{cycles * threshold}")
