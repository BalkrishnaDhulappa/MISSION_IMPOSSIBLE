#!/usr/bin/env python3

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / ".kite_token"

VALID_FILE = BASE_DIR / "valid_etfs.json"
INVALID_FILE = BASE_DIR / "invalid_etfs.json"

KITE_API_KEY = os.environ.get("KITE_API_KEY", "")

MASTER_ETFS = [
"NSE:NIFTYBEES","NSE:METALIETF","NSE:PHARMABEES",
"NSE:MIDCAPETF","NSE:PVTBANIETF","NSE:MODEFENCE","NSE:SMALLCAP",
"NSE:HDFCSML250","NSE:PSUBNKBEES","NSE:ALPHA","NSE:QUAL30IETF",
"NSE:ALPL30IETF","NSE:SML100CASE","NSE:NEXT50IETF","NSE:MONIFTY500",
"NSE:TOP100CASE","NSE:MOMENTUM50","NSE:BANKBEES","NSE:MOM30IETF",
"NSE:FMCGIETF","NSE:CPSEETF","NSE:OILIETF","NSE:GROWWPOWER",
"NSE:MON100","NSE:MOM100","NSE:MOREALTY","NSE:LOWVOLIETF",
"NSE:MIDCAP","NSE:FINIETF","NSE:AUTOIETF","NSE:MULTICAP",
"NSE:ALPHAETF","NSE:MIDSMALL","NSE:VAL30IETF","NSE:MOCAPITAL",
"NSE:TOP10ADD","NSE:ICICIB22","NSE:BSE500IETF","NSE:HEALTHY",
"NSE:GROWWRAIL","NSE:GROWWNET","NSE:MOMENTUM30","NSE:AONETOTAL",
"NSE:MASPTOP50","NSE:NIFTY100EW","NSE:INFRAIETF","NSE:ENERGY",
"NSE:MIDSELIETF","NSE:MAFANG","NSE:NV20IETF","NSE:MSCIINDIA",
"NSE:BFSI","NSE:SBINEQWETF","NSE:TOP15IETF","NSE:CHEMICAL",
"NSE:GROWWEV","NSE:MONQ50","NSE:AXISVALUE","NSE:HDFCSENSEX",
"NSE:MAHKTECH","NSE:CONSUMER","NSE:AONETMMQ50","NSE:MOMIDMTM",
"NSE:MOVALUE","NSE:GROWWN200","NSE:CONSUMBEES","NSE:GROWWHOSPI",
"NSE:ABSLPSE","NSE:HNGSNGBEES","NSE:DEFENCE","NSE:MAKEINDIA",
"NSE:FLEXIADD","NSE:TNIDETF","NSE:ELM250"
]

def get_kite():
    from kiteconnect import KiteConnect

    data = json.loads(TOKEN_FILE.read_text())
    token = data.get("access_token")

    kite = KiteConnect(api_key=KITE_API_KEY)
    kite.set_access_token(token)

    return kite

def main():

    print("🚀 Fetching Zerodha instruments...")

    kite = get_kite()
    instruments = kite.instruments("NSE")

    tradable = set()

    for i in instruments:
        tradable.add(f"NSE:{i['tradingsymbol']}")

    valid = []
    invalid = []

    for code in MASTER_ETFS:
        if code in tradable:
            valid.append(code)
            print(f"✅ {code}")
        else:
            invalid.append(code)
            print(f"❌ {code}")

    VALID_FILE.write_text(json.dumps(valid, indent=2))
    INVALID_FILE.write_text(json.dumps(invalid, indent=2))

    print("\n📊 SUMMARY")
    print(f"✅ Valid: {len(valid)}")
    print(f"❌ Invalid: {len(invalid)}")

if __name__ == "__main__":
    main()
