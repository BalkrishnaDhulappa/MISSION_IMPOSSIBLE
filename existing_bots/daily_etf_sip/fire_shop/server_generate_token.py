#!/usr/bin/env python3
"""
FIRE Shop 3.0 — Server Token Generator (No Browser)
Uses requests + pyotp to login to Zerodha and generate access token.
Works on headless servers — no Chrome/Selenium needed.
"""

import hashlib
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import pyotp
import requests

# ── Config — all credentials from environment variables ──────────────────────
_script_dir = Path(__file__).resolve().parent

KITE_API_KEY        = os.environ.get("KITE_API_KEY", "")
KITE_API_SECRET     = os.environ.get("KITE_API_SECRET", "")
ZERODHA_USER_ID     = os.environ.get("ZERODHA_USER_ID", "")
ZERODHA_PASSWORD    = os.environ.get("ZERODHA_PASSWORD", "")
ZERODHA_TOTP_SECRET = os.environ.get("ZERODHA_TOTP_SECRET", "")
TELEGRAM_BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
TOKEN_FILE          = _script_dir / ".kite_token"


def send_telegram(message):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"  ⚠️  Telegram failed: {e}")


def save_token(access_token):
    TOKEN_FILE.write_text(json.dumps({
        "access_token": access_token,
        "date": date.today().isoformat()
    }))
    print(f"  ✅ Token saved.")


def generate_token():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"
    })

    print("  🔐 Starting Zerodha API login...")

    # Step 1 — Login with user ID and password
    try:
        r = session.post("https://kite.zerodha.com/api/login", data={
            "user_id":  ZERODHA_USER_ID,
            "password": ZERODHA_PASSWORD,
        }, timeout=10)
        data = r.json()
        print(f"  Login response: {data.get('status')} — {data.get('message', '')}")

        if data.get("status") != "success":
            raise Exception(f"Login failed: {data}")

        request_id = data["data"]["request_id"]
        print(f"  ✅ Login successful. Request ID: {request_id}")

    except Exception as e:
        msg = f"❌ Login step failed: {e}"
        print(msg)
        send_telegram(f"🔐 <b>FIRE Shop — Token FAILED</b>\n{msg}")
        sys.exit(1)

    # Step 2 — Submit TOTP
    try:
        totp = pyotp.TOTP(ZERODHA_TOTP_SECRET).now()
        print(f"  🔑 TOTP generated: {totp}")

        r = session.post("https://kite.zerodha.com/api/twofa", data={
            "user_id":    ZERODHA_USER_ID,
            "request_id": request_id,
            "twofa_value": totp,
            "twofa_type": "totp",
            "skip_session": ""
        }, timeout=10)
        data = r.json()
        print(f"  TOTP response: {data.get('status')} — {data.get('message', '')}")

        if data.get("status") != "success":
            raise Exception(f"TOTP failed: {data}")

        print(f"  ✅ TOTP verified.")

    except Exception as e:
        msg = f"❌ TOTP step failed: {e}"
        print(msg)
        send_telegram(f"🔐 <b>FIRE Shop — Token FAILED</b>\n{msg}")
        sys.exit(1)

    # Step 3 — Get request token by following redirects
    import re
    request_token = None

    try:
        # Disable SSL verification to handle external redirect SSL issues
        session.get(
            f"https://kite.zerodha.com/connect/login?api_key={KITE_API_KEY}&v=3",
            allow_redirects=True,
            verify=False,
            timeout=10
        )
    except Exception as e:
        # Extract request_token from URL in exception message
        err_str = str(e)
        match = re.search(r"request_token=([A-Za-z0-9]+)", err_str)
        if match:
            request_token = match.group(1)
            print(f"  ✅ Request token extracted from redirect")

    # If still not found, try without redirects
    if not request_token:
        try:
            r = session.get(
                f"https://kite.zerodha.com/connect/login?api_key={KITE_API_KEY}&v=3",
                allow_redirects=False,
                verify=False,
                timeout=10
            )
            location = r.headers.get("location", "")
            for _ in range(10):
                match = re.search(r"request_token=([A-Za-z0-9]+)", location)
                if match:
                    request_token = match.group(1)
                    break
                if not location or not location.startswith("https://kite.zerodha.com"):
                    break
                r = session.get(location, allow_redirects=False, verify=False, timeout=10)
                location = r.headers.get("location", "")
        except Exception as e:
            err_str = str(e)
            match = re.search(r"request_token=([A-Za-z0-9]+)", err_str)
            if match:
                request_token = match.group(1)

    if not request_token:
        msg = "❌ Could not capture request_token"
        print(msg)
        send_telegram(f"🔐 <b>FIRE Shop — Token FAILED</b>\n{msg}")
        sys.exit(1)

    print(f"  ✅ Request token: {request_token[:10]}...")

    # Step 4 — Exchange request token for access token
    try:
        from kiteconnect import KiteConnect
        kite    = KiteConnect(api_key=KITE_API_KEY)
        sess    = kite.generate_session(request_token, api_secret=KITE_API_SECRET)
        access_token = sess["access_token"]
        save_token(access_token)

        send_telegram(
            "🔐 <b>FIRE Shop — Morning Login ✅</b>\n"
            f"📅 Token generated successfully for today.\n"
            f"⏰ Auto-buy will run at 3:00 PM IST."
        )
        print("  ✅ Access token saved. Ready for 3 PM run.")

    except Exception as e:
        msg = f"❌ Access token exchange failed: {e}"
        print(msg)
        send_telegram(f"🔐 <b>FIRE Shop — Token FAILED</b>\n{msg}")
        sys.exit(1)


if __name__ == "__main__":
    generate_token()
