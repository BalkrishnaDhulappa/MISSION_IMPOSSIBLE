#!/usr/bin/env python3
"""FIRE Shop live engine — buy (RSI rank + BID) + sell (6.38% / compound)."""

from __future__ import annotations

import json
import math
import os
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from charges import fetch_sell_charges, formula_sell_charges
from compound_ledger import apply_growth, compute_growth, load_ledger, save_ledger

BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "config.json"
TOKEN_FILE = BASE_DIR / ".kite_token"
ETF_FILE = BASE_DIR / "etf_universe.json"
STATE_FILE = BASE_DIR / "positions_state.json"
LEDGER_FILE = BASE_DIR / "compound_ledger.json"

KITE_API_KEY = os.environ.get("KITE_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ORDER_FILL_TIMEOUT = 90
IST = ZoneInfo("Asia/Kolkata")

DEFAULT_CONFIG = {
    "initial_capital": 300000,
    "parts": 50,
    "profit_eligibility_pct": 0.0638,
    "sell_limit_buffer": 0.001,
    "bid_threshold": 0.04,
    "max_bid": 3,
    "buy_rank_mode": "rsi",  # "rsi" = lowest RSI(14); "dma" = deepest 20DMA dip
    "rsi_period": 14,
    "buy_limit_buffer": 0.001,
    "limit_price_buffer": 0.001,  # legacy alias for buy
    "order_fill_timeout_sec": 90,
    "dp_flat_fallback": 15.34,
    "investment_per_tx": 6000,  # bootstrap only; live ticket from ledger
}


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)
    data = json.loads(CONFIG_FILE.read_text())
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def load_etf_map() -> dict:
    return json.loads(ETF_FILE.read_text())


def market_calendar_path(for_date=None):
    target_date = for_date or datetime.now(IST).date()
    return BASE_DIR / f"market_calendar_{target_date.year}.json"


def load_market_calendar(for_date=None):
    calendar_path = market_calendar_path(for_date)
    if not calendar_path.exists():
        raise FileNotFoundError(f"Missing market calendar file: {calendar_path}")
    data = json.loads(calendar_path.read_text())
    holidays = {date.fromisoformat(d) for d in data["holidays"]}
    open_hour, open_minute = [int(part) for part in data["market_open"].split(":")]
    close_hour, close_minute = [int(part) for part in data["market_close"].split(":")]
    return {
        "calendar_path": str(calendar_path),
        "holidays": holidays,
        "market_open_minutes": open_hour * 60 + open_minute,
        "market_close_minutes": close_hour * 60 + close_minute,
    }


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def send_telegram(msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception:
        pass


def get_kite():
    from kiteconnect import KiteConnect

    data = json.loads(TOKEN_FILE.read_text())
    kite = KiteConnect(api_key=KITE_API_KEY)
    kite.set_access_token(data["access_token"])
    return kite


def get_order_status(kite, order_id):
    try:
        for o in kite.orders():
            if str(o["order_id"]) == str(order_id):
                avg_price = o.get("average_price") or 0
                return (
                    o.get("status"),
                    float(avg_price) if avg_price else None,
                    o.get("status_message") or o.get("status_message_raw"),
                )
    except Exception:
        pass
    return None, None, None


def is_market_session_open(kite=None, probe_code="NSE:NIFTYBEES"):
    now_ist = datetime.now(IST)
    today_ist = now_ist.date()
    calendar = load_market_calendar(today_ist)
    if now_ist.weekday() >= 5:
        return False
    if today_ist in calendar["holidays"]:
        return False
    now_minutes = now_ist.hour * 60 + now_ist.minute
    return calendar["market_open_minutes"] <= now_minutes <= calendar["market_close_minutes"]


def cancel_order(kite, order_id):
    try:
        from kiteconnect import KiteConnect

        kite.cancel_order(variety=KiteConnect.VARIETY_REGULAR, order_id=order_id)
        print(f"  🚫 Cancelled order {order_id}")
    except Exception as e:
        print(f"  ⚠️  Could not cancel order {order_id}: {e}")


def place_order(kite, code, cmp, qty, buffer, side):
    from kiteconnect import KiteConnect

    symbol = code.replace("NSE:", "")
    variety = KiteConnect.VARIETY_REGULAR

    if side == "BUY":
        price = round(cmp * (1 + buffer), 1)
    else:
        price = round(cmp * (1 - buffer), 1)

    txn = (
        KiteConnect.TRANSACTION_TYPE_BUY
        if side == "BUY"
        else KiteConnect.TRANSACTION_TYPE_SELL
    )

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
            validity=KiteConnect.VALIDITY_DAY,
        )
        print(f"✅ {side} {code} @ {price}  order_id={oid}")
        send_telegram(f"✅ {side} {code} @ {price}")
        return oid, price
    except Exception as e:
        err = str(e)
        if "upper circuit" in err.lower() and side == "BUY":
            try:
                price = round(cmp, 1)
                oid = kite.place_order(
                    variety=variety,
                    exchange=KiteConnect.EXCHANGE_NSE,
                    tradingsymbol=symbol,
                    transaction_type=txn,
                    quantity=int(qty),
                    order_type=KiteConnect.ORDER_TYPE_LIMIT,
                    price=price,
                    product=KiteConnect.PRODUCT_CNC,
                    validity=KiteConnect.VALIDITY_DAY,
                )
                print(f"✅ RETRY BUY {code} @ {price}")
                send_telegram(f"✅ RETRY BUY {code} @ {price}")
                return oid, price
            except Exception as e2:
                print(f"❌ Retry failed {code}: {e2}")
                return None, price

        print(f"❌ Order failed {code}: {e}")
        send_telegram(f"❌ Order failed {code}: {e}")
        if any(
            kw in err.lower()
            for kw in ("insufficient", "margin", "balance", "funds", "after market order", "amo")
        ):
            return "HARD_STOP", price
        return None, price


def place_and_confirm(kite, code, cmp, qty, buffer, side, timeout=ORDER_FILL_TIMEOUT):
    oid, price = place_order(kite, code, cmp, qty, buffer, side)
    if oid == "HARD_STOP":
        return "HARD_STOP", None, None
    if oid is None:
        return "FAILED", None, None

    print(f"  ⏳ Waiting up to {timeout}s for fill...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(10)
        status, fill_price, status_message = get_order_status(kite, oid)
        if status == "COMPLETE":
            actual_price = fill_price or price
            print(f"  ✅ Order {oid} filled @ {actual_price}!")
            return "FILLED", actual_price, oid
        if status in {"CANCELLED", "REJECTED"}:
            detail = f" ({status_message})" if status_message else ""
            print(f"  ❌ Order {oid} {status.lower()}{detail}")
            return "FAILED", None, oid

    print(f"  ⌛ Order {oid} not filled after {timeout}s — cancelling")
    send_telegram(f"⌛ {code} not filled in {timeout}s — cancelled, trying next ETF")
    cancel_order(kite, oid)
    return "FAILED", None, oid


def load_holdings(kite) -> dict:
    holdings = {}
    for p in kite.holdings():
        qty = float(p["quantity"]) + float(p["t1_quantity"])
        if qty <= 0:
            continue
        code = f"NSE:{p['tradingsymbol']}"
        holdings[code] = {
            "qty": qty,
            "avg": float(p["average_price"]),
        }
    return holdings


def get_ltps(kite, codes: list[str]) -> dict[str, float]:
    """Return {NSE:SYM: ltp} via kite.ltp."""
    if not codes:
        return {}
    try:
        raw = kite.ltp(codes)
    except Exception as e:
        print(f"⚠️  LTP fetch failed: {e}")
        return {}
    out = {}
    for code in codes:
        row = raw.get(code) or raw.get(code.replace("NSE:", "NSE:"))
        if not row:
            # kite sometimes keys without exchange prefix variants
            for k, v in raw.items():
                if k.endswith(code.replace("NSE:", "")) or k == code:
                    row = v
                    break
        if row and row.get("last_price"):
            out[code] = float(row["last_price"])
    return out


def reconcile_state_with_holdings(state, holdings, etf_universe: set[str]):
    """Align state with holdings; cache broker_avg for ETFs. Returns (changed, removed, added)."""
    removed = []
    added = []
    changed = False

    for code, h in holdings.items():
        if code not in etf_universe:
            continue
        s = state.setdefault(code, {})
        if not s.get("last_buy"):
            s["last_buy"] = h["avg"]
            added.append(code)
            changed = True
        if "bid_count" not in s:
            s["bid_count"] = 0
            changed = True
        fresh_invested = h["avg"] * h["qty"]
        if abs(float(s.get("invested", 0)) - fresh_invested) > 1e-9:
            s["invested"] = fresh_invested
            changed = True
        if abs(float(s.get("broker_avg", 0) or 0) - h["avg"]) > 1e-9:
            s["broker_avg"] = h["avg"]
            changed = True
        if "last_sip" not in s:
            s["last_sip"] = None
            changed = True
        # Drop provisional capital-double field if present
        if "original_invested" in s:
            del s["original_invested"]
            changed = True

    return changed, removed, added


def pick_sell_candidate(holdings, ltps, etf_universe, eligibility_pct: float):
    """Among ETF holdings with LTP >= avg*(1+pct), pick highest unrealized %."""
    best = None
    for code, h in holdings.items():
        if code not in etf_universe:
            continue
        ltp = ltps.get(code)
        avg = float(h["avg"])
        if not ltp or avg <= 0:
            continue
        # Compare on paise-rounded target to avoid float drift at exact 6.38%.
        target_px = round(avg * (1.0 + eligibility_pct), 2)
        if ltp < target_px:
            continue
        profit_pct = (ltp / avg) - 1.0
        candidate = {
            "code": code,
            "ltp": ltp,
            "qty": h["qty"],
            "avg": avg,
            "profit_pct": profit_pct,
        }
        if best is None or candidate["profit_pct"] > best["profit_pct"]:
            best = candidate
    return best


def _symbol(code: str) -> str:
    return code.replace("NSE:", "")


def find_today_sell_trades(kite, code: str) -> list[dict]:
    """Return today's CNC SELL trades for symbol (may be empty)."""
    symbol = _symbol(code)
    trades = []
    try:
        for t in kite.trades():
            if str(t.get("tradingsymbol")) != symbol:
                continue
            if str(t.get("transaction_type", "")).upper() != "SELL":
                continue
            product = str(t.get("product", "")).upper()
            if product and product != "CNC":
                continue
            trades.append(t)
    except Exception as e:
        print(f"⚠️  trades() failed: {e}")
    return trades


def book_sell_growth(
    *,
    ledger: dict,
    config: dict,
    code: str,
    qty: float,
    avg: float,
    fill_price: float,
    order_id: str | None,
    kite,
    source: str,
    ltp: float | None = None,
    limit_price: float | None = None,
) -> dict:
    sell_value = float(qty) * float(fill_price)
    cost_basis = float(qty) * float(avg)
    dp_flat = float(config.get("dp_flat_fallback", 15.34))
    if order_id and kite is not None:
        charges = fetch_sell_charges(
            kite,
            order_id=str(order_id),
            tradingsymbol=_symbol(code),
            quantity=int(qty),
            average_price=float(fill_price),
            dp_flat=dp_flat,
        )
    else:
        charges = formula_sell_charges(sell_value, dp_flat=dp_flat)

    growth = compute_growth(sell_value, cost_basis, charges["total"])
    record = {
        "source": source,
        "code": code,
        "qty": qty,
        "avg": avg,
        "ltp": ltp,
        "limit": limit_price,
        "fill": fill_price,
        "sell_value": round(sell_value, 2),
        "cost_basis": round(cost_basis, 2),
        "charges": charges,
        "order_id": order_id,
    }
    apply_growth(ledger, growth=growth, sell_record=record)
    save_ledger(LEDGER_FILE, ledger)
    return {"growth": growth, "charges": charges, "ledger": ledger}


def reconcile_manual_sells(kite, state, holdings, etf_universe, ledger, config):
    """M2: state ETFs missing from holdings → try today's SELL trades → book growth."""
    holding_codes = set(holdings.keys())
    booked = []
    cleaned = []

    for code in list(state.keys()):
        if code not in etf_universe:
            continue
        if code in holding_codes and holdings[code]["qty"] > 0:
            continue

        s = state.get(code) or {}
        avg = float(s.get("broker_avg") or s.get("last_buy") or 0)
        invested = float(s.get("invested") or 0)
        qty_hint = (invested / avg) if avg > 0 else 0

        trades = find_today_sell_trades(kite, code)
        if trades:
            # Aggregate fills
            total_qty = sum(float(t.get("quantity") or 0) for t in trades)
            if total_qty <= 0:
                total_qty = qty_hint
            notional = sum(
                float(t.get("quantity") or 0) * float(t.get("average_price") or t.get("price") or 0)
                for t in trades
            )
            fill = (notional / total_qty) if total_qty else 0
            order_id = str(trades[-1].get("order_id") or trades[-1].get("trade_id") or "manual")
            if avg <= 0 or fill <= 0 or total_qty <= 0:
                print(f"⚠️  Manual exit {code}: incomplete trade data — no growth")
                send_telegram(f"⚠️ FIRE Shop — manual/external exit {code}: incomplete data, no growth")
            else:
                result = book_sell_growth(
                    ledger=ledger,
                    config=config,
                    code=code,
                    qty=total_qty,
                    avg=avg,
                    fill_price=fill,
                    order_id=order_id,
                    kite=kite,
                    source="manual",
                )
                msg = (
                    f"📒 FIRE Shop — manual sell booked {code}\n"
                    f"qty={total_qty} fill=₹{fill:.2f} charges=₹{result['charges']['total']}\n"
                    f"growth=₹{result['growth']} ticket=₹{ledger['ticket']}"
                )
                print(msg)
                send_telegram(msg)
                booked.append(code)
        else:
            msg = f"⚠️ FIRE Shop — manual/external exit {code}: no today trade — no growth booked"
            print(msg)
            send_telegram(msg)
            cleaned.append(code)

        state.pop(code, None)

    if booked or cleaned:
        save_state(state)
    return booked, cleaned


def main(run_sell=True, run_buy=True):
    print("🚀 Starting ETF engine...")

    config = load_config()
    etf_map = load_etf_map()
    etf_universe = set(etf_map.keys())
    state = load_state()
    ledger = load_ledger(
        LEDGER_FILE,
        initial_capital=float(config.get("initial_capital", 300000)),
        parts=int(config.get("parts", 50)),
    )
    ticket = float(ledger.get("ticket") or config.get("investment_per_tx") or 6000)
    timeout = int(config.get("order_fill_timeout_sec", ORDER_FILL_TIMEOUT))
    eligibility = float(config.get("profit_eligibility_pct", 0.0638))
    sell_buffer = float(config.get("sell_limit_buffer", 0.001))
    buy_buffer = float(
        config.get("buy_limit_buffer", config.get("limit_price_buffer", 0.001))
    )

    kite = get_kite()

    try:
        load_market_calendar()
    except FileNotFoundError as e:
        msg = f"🛑 FIRE Shop — {e}"
        print(msg)
        send_telegram(msg)
        return

    if (run_sell or run_buy) and not is_market_session_open(kite):
        print("⏸ Market closed, skipping execution")
        send_telegram("⏸ FIRE Shop — Market closed. Skipping run.")
        return

    holdings = load_holdings(kite)
    changed, _, added = reconcile_state_with_holdings(state, holdings, etf_universe)
    if changed:
        save_state(state)
        print("🧾 Reconciled ETF state with broker holdings")
        if added:
            print(f"  initialized: {', '.join(sorted(added))}")

    # M2 — always before buy/sell actions
    reconcile_manual_sells(kite, state, holdings, etf_universe, ledger, config)
    # Refresh holdings/ticket after possible manual booking
    holdings = load_holdings(kite)
    ticket = float(ledger.get("ticket") or ticket)

    # ── SELL ───────────────────────────────────────────────
    if run_sell:
        etf_holdings = {c: h for c, h in holdings.items() if c in etf_universe}
        ltps = get_ltps(kite, list(etf_holdings.keys()))
        winner = pick_sell_candidate(etf_holdings, ltps, etf_universe, eligibility)
        if not winner:
            print("ℹ️  No ETF eligible to sell today")
        else:
            code = winner["code"]
            ltp = winner["ltp"]
            print(
                f"🔴 SELL {code} (pnl={round(winner['profit_pct']*100, 2)}%, "
                f"gate={round(eligibility*100, 2)}%, ltp=₹{ltp})"
            )
            status, fill_price, order_id = place_and_confirm(
                kite,
                code,
                ltp,
                winner["qty"],
                sell_buffer,
                "SELL",
                timeout=timeout,
            )
            if status == "HARD_STOP":
                send_telegram("🛑 FIRE Shop — Hard stop during sell.")
                return
            if status == "FILLED":
                fill = fill_price or ltp
                result = book_sell_growth(
                    ledger=ledger,
                    config=config,
                    code=code,
                    qty=winner["qty"],
                    avg=winner["avg"],
                    fill_price=fill,
                    order_id=order_id,
                    kite=kite,
                    source="bot",
                    ltp=ltp,
                    limit_price=round(ltp * (1 - sell_buffer), 1),
                )
                state.pop(code, None)
                save_state(state)
                msg = (
                    f"✅ FIRE Shop SOLD {code}\n"
                    f"fill=₹{fill} charges=₹{result['charges']['total']} "
                    f"growth=₹{result['growth']}\n"
                    f"WC=₹{ledger['working_capital']} next ticket=₹{ledger['ticket']}"
                )
                print(msg)
                send_telegram(msg)
            return  # one sell attempt per run regardless of fill

    # ── BUY ────────────────────────────────────────────────
    if run_buy:
        from fire_shop_automation import get_nse_session, rank_instruments

        session = get_nse_session()
        instruments = [(code, code.replace("NSE:", "")) for code in etf_map.keys()]
        rank_mode = config.get("buy_rank_mode", "rsi")
        rsi_period = int(config.get("rsi_period", 14))
        ranked = rank_instruments(
            instruments,
            session,
            "ETF",
            rank_mode=rank_mode,
            rsi_period=rsi_period,
        )
        print(f"📊 Buy rank mode: {rank_mode.upper()}" + (f"({rsi_period})" if rank_mode == "rsi" else ""))

        buy_candidates = []

        for r in ranked:
            code = r["code"]
            if code in holdings:
                continue
            qty = int(math.ceil(ticket / r["cmp"]))
            if qty > 0:
                buy_candidates.append(
                    {
                        "type": "NEW",
                        "code": code,
                        "cmp": r["cmp"],
                        "qty": qty,
                        "state": None,
                        "label": "NEW BUY",
                    }
                )

        for code, h in holdings.items():
            if code not in etf_universe:
                continue
            if code not in state:
                invested = h["avg"] * h["qty"]
                state[code] = {
                    "last_buy": h["avg"],
                    "bid_count": 0,
                    "invested": invested,
                    "broker_avg": h["avg"],
                    "last_sip": None,
                }
            s = state[code]
            cmp = next((r["cmp"] for r in ranked if r["code"] == code), None)
            if not cmp:
                continue
            if cmp <= s["last_buy"] * (1 - config["bid_threshold"]) and s[
                "bid_count"
            ] < config["max_bid"]:
                amt = max(float(s.get("invested") or 0) / 2, ticket)
                qty = int(math.ceil(amt / cmp))
                if qty > 0:
                    buy_candidates.append(
                        {
                            "type": "BID",
                            "code": code,
                            "cmp": cmp,
                            "qty": qty,
                            "state": s,
                            "label": f"BID level {s['bid_count']+1}",
                        }
                    )

        print(f"🎫 Using ticket ₹{ticket}")

        for candidate in buy_candidates:
            code = candidate["code"]
            cmp = candidate["cmp"]
            qty = candidate["qty"]
            label = candidate["label"]
            print()
            print(f"{'🔁' if candidate['type'] == 'BID' else '🟢'} {label}: {code} × {qty} @ ₹{cmp}")

            buy_status, fill_price, _oid = place_and_confirm(
                kite, code, cmp, qty, buy_buffer, "BUY", timeout=timeout
            )
            if buy_status == "HARD_STOP":
                send_telegram("🛑 FIRE Shop — Hard stop. No further buys attempted.")
                return
            if buy_status == "FILLED":
                executed_price = fill_price or cmp
                executed_value = qty * executed_price
                if candidate["type"] == "BID":
                    s = candidate["state"]
                    s["last_buy"] = executed_price
                    s["invested"] = float(s.get("invested") or 0) + executed_value
                    s["bid_count"] = int(s.get("bid_count") or 0) + 1
                    # broker_avg refreshed on next reconcile
                else:
                    state[code] = {
                        "last_buy": executed_price,
                        "bid_count": 0,
                        "invested": executed_value,
                        "broker_avg": executed_price,
                        "last_sip": None,
                    }
                save_state(state)
                print(f"✅ Done — {code} bought @ ₹{executed_price} (ticket ₹{ticket})")
                send_telegram(f"✅ FIRE Shop BUY {code} @ ₹{executed_price} (ticket ₹{ticket})")
                return
            print(f"⏭️  {code} not filled — trying next candidate...")

        print("⚠️  No ETF could be bought today — all candidates timed out or failed")
        send_telegram("⚠️ FIRE Shop — No ETF bought today. All candidates timed out.")


if __name__ == "__main__":
    main()
