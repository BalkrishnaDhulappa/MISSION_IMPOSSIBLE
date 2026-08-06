#!/usr/bin/env python3
import openpyxl
from collections import defaultdict

XLSX_FILE = "FIRE shop 3.0 with Jewels - BID Investment - 24 JUL 2024 Balkrishna.xlsx"
SHEET_NAME = "Current Holdings"


def to_float(val):
    try:
        return float(val)
    except:
        return 0.0


def load_holdings(ws):
    holdings = {}

    for row in ws.iter_rows(min_row=7):
        code = row[2].value        # Column C
        total_qty = row[6].value   # Column G
        avg_price = row[7].value   # Column H
        total_inv = row[8].value   # Column I

        if not code:
            continue

        qty = to_float(total_qty)
        invested = to_float(total_inv)
        avg = to_float(avg_price)

        if qty > 0:
            holdings[code] = {
                "qty": qty,
                "avg": avg,
                "invested": invested,
                "row": row[0].row
            }

    return holdings


def main():
    print("\nOpening Excel...")
    wb = openpyxl.load_workbook(XLSX_FILE)
    ws = wb[SHEET_NAME]

    holdings = load_holdings(ws)

    print("\nCurrent Holdings Found:\n")

    rows_to_delete = []

    for code, data in holdings.items():
        print("-------------------------------------------------")
        print(f"ETF: {code}")
        print(f"Total Qty     : {data['qty']}")
        print(f"Avg Price     : ₹{data['avg']}")
        print(f"Total Invested: ₹{data['invested']}")
        print("-------------------------------------------------")

        ans = input(f"Delete this ETF entry? (y/n): ").lower()

        if ans == "y":
            rows_to_delete.append(data["row"])
            print(f"Marked {code} for deletion.\n")
        else:
            print(f"Keeping {code}.\n")

    # Delete rows
    for r in sorted(rows_to_delete, reverse=True):
        ws.delete_rows(r)

    wb.save(XLSX_FILE)

    print("\nExcel updated successfully.")
    print("Deleted ETFs:", len(rows_to_delete))


if __name__ == "__main__":
    main()
