#!/usr/bin/env python3

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ETF_FILE = BASE_DIR / "etf_universe.json"

def load_etf_map():
    if not ETF_FILE.exists():
        print("❌ etf_universe.json missing")
        exit(1)
    return json.loads(ETF_FILE.read_text())

def main():

    print("🚀 Testing ETF ranking...\n")

    etf_map = load_etf_map()

    # 🔥 FIX: Convert into (code, name) format
    instruments = [(code, code.replace("NSE:", "")) for code in etf_map.keys()]

    print(f"📦 Total ETFs loaded: {len(instruments)}")

    from fire_shop_automation import get_nse_session, rank_instruments

    session = get_nse_session()

    print("\n📡 Fetching and ranking ETFs...\n")

    ranked = rank_instruments(instruments, session, "ETF")

    # ─────────────────────────────────────────
    # COVERAGE CHECK
    # ─────────────────────────────────────────
    ranked_codes = set(r["code"] for r in ranked)
    all_codes = set(code for code, _ in instruments)

    missing = all_codes - ranked_codes

    print("\n==============================")
    print("📊 COVERAGE CHECK")
    print("==============================")
    print(f"Total ranked: {len(ranked_codes)}")
    print(f"Missing ETFs: {len(missing)}")

    if missing:
        print("❌ Missing:")
        for m in sorted(missing):
            print("  ", m)
    else:
        print("✅ All ETFs covered")

    # ─────────────────────────────────────────
    # TOP 20
    # ─────────────────────────────────────────
    print("\n==============================")
    print("🏆 TOP 20 RANKED ETFs")
    print("==============================")

    for i, r in enumerate(ranked[:20], 1):
        code = r.get("code")
        cmp = r.get("cmp")
        sector = etf_map.get(code, "UNKNOWN")

        print(f"{i:02d}. {code:20} | CMP={cmp:<10} | Sector={sector}")

    # ─────────────────────────────────────────
    # RAW DEBUG
    # ─────────────────────────────────────────
    print("\n==============================")
    print("🔬 RAW DATA (TOP 5)")
    print("==============================")

    for r in ranked[:5]:
        print(r)

    # ─────────────────────────────────────────
    # CMP VALIDATION
    # ─────────────────────────────────────────
    print("\n==============================")
    print("💰 CMP VALIDATION")
    print("==============================")

    bad_cmp = [r["code"] for r in ranked if not r.get("cmp") or r["cmp"] <= 0]

    if bad_cmp:
        print("❌ Bad CMP values:")
        for b in bad_cmp:
            print("  ", b)
    else:
        print("✅ All CMP values valid")

    # ─────────────────────────────────────────
    # BUY SIMULATION
    # ─────────────────────────────────────────
    print("\n==============================")
    print("🧪 BUY DECISION SIMULATION")
    print("==============================")

    fake_holdings = {
        "NSE:ITBEES": {"sector": "IT"},
        "NSE:BANKBEES": {"sector": "BANKING"}
    }

    held_sectors = set(h["sector"] for h in fake_holdings.values())

    print("Held sectors:", held_sectors)

    selected = None

    for r in ranked:
        code = r["code"]
        sector = etf_map.get(code)

        if code in fake_holdings:
            continue

        if sector in held_sectors:
            continue

        selected = r
        break

    if selected:
        print("\n🟢 SELECTED ETF FOR BUY:")
        print(selected)
    else:
        print("\n⚠️ No ETF selected")

    print("\n✅ Ranking test complete")


if __name__ == "__main__":
    main()
