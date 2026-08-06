#!/usr/bin/env python3
"""
FIRE Shop 3.0 — Test Suite
Tests all conditions without placing orders or waiting.
Run this to verify logic before going live.

Usage:
    python3 test_fire_shop.py --xlsx "FIRE shop 3.0 ....xlsx"
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_script_dir))

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"
INFO = "ℹ️  INFO"

results = []

def test(name, condition, detail="", warn_only=False):
    status = PASS if condition else (WARN if warn_only else FAIL)
    results.append((status, name, detail))
    print(f"  {status}  {name}")
    if detail:
        print(f"         {detail}")

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ──────────────────────────────────────────────────────────────────────────────
# 1. IMPORTS
# ──────────────────────────────────────────────────────────────────────────────
section("1. IMPORTS & DEPENDENCIES")

try:
    import openpyxl
    test("openpyxl installed", True)
except ImportError:
    test("openpyxl installed", False, "Run: pip install openpyxl")

try:
    import requests
    test("requests installed", True)
except ImportError:
    test("requests installed", False, "Run: pip install requests")

try:
    from kiteconnect import KiteConnect
    test("kiteconnect installed", True)
except ImportError:
    test("kiteconnect installed", False, "Run: pip install kiteconnect")

try:
    import pyotp
    test("pyotp installed", True)
except ImportError:
    test("pyotp installed", False, "Run: pip install pyotp")

try:
    from fire_shop_automation import (
        get_nse_session, rank_instruments, load_investment_per_tx,
        load_current_holdings, load_sold_codes, apply_holdings_filter,
        MASTER_ETFS
    )
    test("fire_shop_automation.py found", True)
except ImportError as e:
    test("fire_shop_automation.py found", False, str(e))
    print("\n❌ Cannot continue without fire_shop_automation.py")
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# 2. CONFIG
# ──────────────────────────────────────────────────────────────────────────────
section("2. CONFIGURATION")

try:
    from zerodha_auto_buy import (
        KITE_API_KEY, KITE_API_SECRET, ZERODHA_USER_ID,
        ZERODHA_PASSWORD, ZERODHA_TOTP_SECRET,
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
        TOKEN_FILE, ORDER_LOG_FILE, BALANCE_LOG_FILE,
        MAX_ORDER_VALUE, MAX_DAILY_SPEND, MIN_AVAILABLE_CASH,
        LIMIT_PRICE_BUFFER
    )
    test("zerodha_auto_buy.py found", True)
except ImportError as e:
    test("zerodha_auto_buy.py found", False, str(e))
    sys.exit(1)

test("KITE_API_KEY set", bool(KITE_API_KEY) and KITE_API_KEY != "your_api_key_here",
     f"Value: {KITE_API_KEY[:6]}...")
test("KITE_API_SECRET set", bool(KITE_API_SECRET) and KITE_API_SECRET != "your_api_secret_here",
     f"Value: {KITE_API_SECRET[:6]}...")
test("ZERODHA_USER_ID set", bool(ZERODHA_USER_ID) and ZERODHA_USER_ID != "your_zerodha_user_id",
     f"Value: {ZERODHA_USER_ID}")
test("ZERODHA_PASSWORD set", bool(ZERODHA_PASSWORD) and ZERODHA_PASSWORD != "your_zerodha_password")
test("ZERODHA_TOTP_SECRET set", bool(ZERODHA_TOTP_SECRET) and len(ZERODHA_TOTP_SECRET) > 10,
     f"Length: {len(ZERODHA_TOTP_SECRET)}")
test("TELEGRAM_BOT_TOKEN set", bool(TELEGRAM_BOT_TOKEN))
test("TELEGRAM_CHAT_ID set", bool(TELEGRAM_CHAT_ID))

# Safety limits
test("MAX_ORDER_VALUE reasonable", 1000 <= MAX_ORDER_VALUE <= 50000,
     f"₹{MAX_ORDER_VALUE:,}")
test("MAX_DAILY_SPEND reasonable", 1000 <= MAX_DAILY_SPEND <= 100000,
     f"₹{MAX_DAILY_SPEND:,}")
test("MIN_AVAILABLE_CASH reasonable", MIN_AVAILABLE_CASH >= 1000,
     f"₹{MIN_AVAILABLE_CASH:,}")


# ──────────────────────────────────────────────────────────────────────────────
# 3. TOTP
# ──────────────────────────────────────────────────────────────────────────────
section("3. TOTP VERIFICATION")

try:
    import pyotp
    totp = pyotp.TOTP(ZERODHA_TOTP_SECRET)
    code = totp.now()
    test("TOTP generates valid code", len(code) == 6 and code.isdigit(), f"Code: {code}")
    test("TOTP secret valid format", len(ZERODHA_TOTP_SECRET) >= 16,
         f"Length: {len(ZERODHA_TOTP_SECRET)}")
except Exception as e:
    test("TOTP generates valid code", False, str(e))


# ──────────────────────────────────────────────────────────────────────────────
# 4. TELEGRAM
# ──────────────────────────────────────────────────────────────────────────────
section("4. TELEGRAM CONNECTION")

try:
    import requests as req
    r = req.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=5)
    data = r.json()
    test("Telegram bot reachable", data.get("ok"), f"Bot: @{data.get('result', {}).get('username', '?')}")
except Exception as e:
    test("Telegram bot reachable", False, str(e))


# ──────────────────────────────────────────────────────────────────────────────
# 5. TOKEN FILE
# ──────────────────────────────────────────────────────────────────────────────
section("5. KITE ACCESS TOKEN")

token_exists = TOKEN_FILE.exists()
test("Token file exists", token_exists, str(TOKEN_FILE))

if token_exists:
    try:
        token_data = json.loads(TOKEN_FILE.read_text())
        token_date = token_data.get("date")
        is_today   = token_date == date.today().isoformat()
        test("Token is from today", is_today, f"Token date: {token_date}, Today: {date.today()}")
        test("Access token present", bool(token_data.get("access_token")),
             f"Token: {token_data.get('access_token', '')[:10]}...")
    except Exception as e:
        test("Token file readable", False, str(e))
else:
    test("Token is from today", False, "Run server_generate_token.py first", warn_only=True)


# ──────────────────────────────────────────────────────────────────────────────
# 6. KITE API CONNECTION
# ──────────────────────────────────────────────────────────────────────────────
section("6. KITE API CONNECTION")

kite = None
if token_exists:
    try:
        from kiteconnect import KiteConnect
        token_data   = json.loads(TOKEN_FILE.read_text())
        access_token = token_data.get("access_token")
        kite = KiteConnect(api_key=KITE_API_KEY)
        kite.set_access_token(access_token)
        profile = kite.profile()
        test("Kite API connected", True, f"User: {profile.get('user_name')} ({profile.get('user_id')})")
    except Exception as e:
        test("Kite API connected", False, str(e))
        kite = None
else:
    test("Kite API connected", False, "No token available", warn_only=True)

# Check balance
if kite:
    try:
        margins   = kite.margins(segment="equity")
        available = float(margins["available"]["opening_balance"])
        test("Balance fetch works", True, f"Available: ₹{available:,.0f}")
        test("Sufficient balance", available >= MIN_AVAILABLE_CASH,
             f"Available ₹{available:,.0f} vs minimum ₹{MIN_AVAILABLE_CASH:,.0f}")
    except Exception as e:
        test("Balance fetch works", False, str(e))


# ──────────────────────────────────────────────────────────────────────────────
# 7. EXCEL FILE
# ──────────────────────────────────────────────────────────────────────────────
section("7. EXCEL FILE & HOLDINGS")

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--xlsx", required=True)
args = parser.parse_args()

xlsx_path = args.xlsx
xlsx_exists = Path(xlsx_path).exists()
test("Excel file exists", xlsx_exists, xlsx_path)

if xlsx_exists:
    try:
        investment_per_tx = load_investment_per_tx(xlsx_path)
        test("Investment per tx readable", investment_per_tx > 0, f"₹{investment_per_tx:,.0f}")
    except Exception as e:
        test("Investment per tx readable", False, str(e))

    try:
        holdings = load_current_holdings(xlsx_path)
        test("Holdings loaded", True, f"{len(holdings)} positions")
        for code, h in holdings.items():
            avg       = h.get("avg_price")
            next_bid  = h.get("next_bid")
            bid_price = h.get("next_bid")
            has_avg   = avg is not None and avg > 0
            has_bid   = next_bid is not None
            test(f"  {code} has avg price", has_avg,
                 f"avg=₹{avg}" if has_avg else "avg=None ← needs fix!", warn_only=not has_avg)
            test(f"  {code} has next BID", has_bid,
                 f"next_bid={next_bid}" if has_bid else "next_bid=None ← needs fix!", warn_only=not has_bid)
    except Exception as e:
        test("Holdings loaded", False, str(e))

    try:
        sold_codes = load_sold_codes(xlsx_path)
        test("Sold codes loaded", True, f"{len(sold_codes)} sold positions")
    except Exception as e:
        test("Sold codes loaded", False, str(e))


# ──────────────────────────────────────────────────────────────────────────────
# 8. YAHOO FINANCE DATA
# ──────────────────────────────────────────────────────────────────────────────
section("8. YAHOO FINANCE DATA (sample 3 ETFs)")

try:
    from fire_shop_automation import fetch_etf_data
    session = get_nse_session()
    sample  = [("NSE:NIFTYBEES", "NIFTY 50"), ("NSE:PSUBNKBEES", "PSU Bank"), ("NSE:ITBEES", "IT")]
    for code, name in sample:
        try:
            cmp, dma20, vol = fetch_etf_data(session, code)
            ok  = cmp is not None and dma20 is not None
            pct = round((cmp - dma20) / dma20 * 100, 2) if ok else None
            test(f"  {code} data fetched", ok,
                 f"CMP=₹{cmp}, 20DMA=₹{dma20}, Δ={pct}%, Vol={vol:,}" if ok else "fetch failed")
        except Exception as e:
            test(f"  {code} data fetched", False, str(e)[:80])
except Exception as e:
    test("Yahoo Finance fetch", False, str(e))


# ──────────────────────────────────────────────────────────────────────────────
# 9. HOLDINGS FILTER & AVG DOWN RULE
# ──────────────────────────────────────────────────────────────────────────────
section("9. HOLDINGS FILTER & AVG DOWN RULE")

if xlsx_exists:
    try:
        holdings   = load_current_holdings(xlsx_path)
        sold_codes = load_sold_codes(xlsx_path)

        print(f"\n  Current holdings ({len(holdings)}):")
        for code, h in holdings.items():
            avg       = h.get("avg_price")
            next_bid  = h.get("next_bid")
            # Fetch live CMP
            try:
                from fire_shop_automation import fetch_etf_data
                sess2 = get_nse_session()
                cmp, _, _ = fetch_etf_data(sess2, code)
            except Exception:
                cmp = None

            if avg and next_bid and cmp:
                pct_from_avg   = round((cmp - avg) / avg * 100, 2)
                # next_bid is the price (col 18), not the percentage
                below_next_bid = cmp <= next_bid
                status = "🔁 AVG DOWN eligible" if below_next_bid else "⛔ Suppressed (holding)"
                print(f"    {code:<25} avg=₹{avg:.2f}  next_bid=₹{next_bid:.2f}  CMP=₹{cmp:.2f}  Δavg={pct_from_avg}%  → {status}")
            else:
                print(f"    {code:<25} avg=None or CMP unavailable ← needs fix!")

    except Exception as e:
        test("Holdings filter check", False, str(e))


# ──────────────────────────────────────────────────────────────────────────────
# 10. ORDER LOG
# ──────────────────────────────────────────────────────────────────────────────
section("10. ORDER LOG")

if ORDER_LOG_FILE.exists():
    try:
        log    = json.loads(ORDER_LOG_FILE.read_text())
        today  = date.today().isoformat()
        today_orders  = [e for e in log if e.get("date") == today]
        live_orders   = [e for e in today_orders if e.get("status") != "paper"]
        paper_orders  = [e for e in today_orders if e.get("status") == "paper"]
        test("Order log readable", True, f"Total: {len(log)}, Today: {len(today_orders)}")
        if live_orders:
            print(f"\n  Today's live orders:")
            for o in live_orders:
                print(f"    {o['code']} × {o['qty']} @ ₹{o['limit_price']} → {o['status']}")
        if paper_orders:
            print(f"\n  Today's paper orders (will be skipped in live):")
            for o in paper_orders:
                print(f"    {o['code']} × {o['qty']} → {o['status']}")
    except Exception as e:
        test("Order log readable", False, str(e))
else:
    test("Order log exists", False, "No orders placed yet", warn_only=True)


# ──────────────────────────────────────────────────────────────────────────────
# 11. MARKET HOURS CHECK
# ──────────────────────────────────────────────────────────────────────────────
section("11. MARKET HOURS & SCHEDULE")

now = datetime.now()
# Server is UTC, IST = UTC + 5:30
from datetime import timezone, timedelta
ist = timezone(timedelta(hours=5, minutes=30))
now_ist = datetime.now(ist)
market_open  = now_ist.replace(hour=9,  minute=15, second=0, microsecond=0)
market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
is_weekday   = now_ist.weekday() < 5
is_market    = market_open <= now_ist <= market_close

print(f"\n  Current time (IST): {now_ist.strftime('%d %b %Y %H:%M:%S')}")
print(f"  Current time (UTC): {datetime.utcnow().strftime('%d %b %Y %H:%M:%S')}")
test("Today is a weekday", is_weekday, now_ist.strftime("%A"))
test("Market is open now", is_market,
     f"{now_ist.strftime('%H:%M')} IST ({'within' if is_market else 'outside'} 9:15–15:30)",
     warn_only=True)

print(f"\n  Cron schedule (UTC):")
print(f"    Token generation : 30 5 * * 1-5  = 10:30 AM IST")
print(f"    Auto buy         : 30 9 * * 1-5  =  3:00 PM IST")


# ──────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────────────────────────────
section("SUMMARY")

passed  = sum(1 for s, _, _ in results if s == PASS)
failed  = sum(1 for s, _, _ in results if s == FAIL)
warned  = sum(1 for s, _, _ in results if s == WARN)

print(f"\n  ✅ Passed : {passed}")
print(f"  ❌ Failed : {failed}")
print(f"  ⚠️  Warned : {warned}")

if failed == 0:
    print("\n  🎉 All checks passed — system is ready for live trading!")
else:
    print(f"\n  ❌ {failed} check(s) failed — fix these before going live:")
    for status, name, detail in results:
        if status == FAIL:
            print(f"     • {name}: {detail}")
