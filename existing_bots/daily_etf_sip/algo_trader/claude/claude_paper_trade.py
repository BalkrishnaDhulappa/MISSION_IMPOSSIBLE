# --- ONLY SHOWING MODIFIED PART (FULL FUNCTION REPLACEMENT) ---

# ADD THIS NEW STRATEGY CLASS (TOP OF FILE or new module)
class TrendPullbackStrategy:
    def signal(self, candles, regime_result, pos):
        if regime_result["regime"] != "trending":
            return None

        if len(candles) < 210:
            return None

        import pandas as pd

        df = pd.DataFrame(candles)

        df["ema20"] = df["close"].ewm(span=20).mean()
        df["ema200"] = df["close"].ewm(span=200).mean()

        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df["rsi"] = 100 - (100 / (1 + rs))

        df["tr"] = (df[["high","close"]].max(axis=1) - df[["low","close"]].min(axis=1))
        df["atr"] = df["tr"].rolling(14).mean()

        row = df.iloc[-1]
        prev = df.iloc[-2]

        price = row["close"]
        ema20 = row["ema20"]
        ema200 = row["ema200"]
        rsi = row["rsi"]
        atr = row["atr"]

        long_trend = price > ema200 and ema20 > ema200
        short_trend = price < ema200 and ema20 < ema200

        if pos:
            return {"action": "hold"}

        if long_trend and abs(price - ema20)/ema20 < 0.02 and 40 < rsi < 60 and price > prev["high"]:
            return {
                "action": "buy",
                "price": price,
                "sl": price - 1.2 * atr,
                "tp": price + 2 * (price - (price - 1.2 * atr)),
                "qty_pct": 0.2,
                "reason": "Trend pullback long"
            }

        if short_trend and abs(price - ema20)/ema20 < 0.02 and 40 < rsi < 60 and price < prev["low"]:
            return {
                "action": "short",
                "price": price,
                "sl": price + 1.2 * atr,
                "tp": price - 2 * ((price + 1.2 * atr) - price),
                "qty_pct": 0.2,
                "reason": "Trend pullback short"
            }

        return {"action": "hold"}


# --- MODIFY BTC BLOCK IN run() FUNCTION ---

    # ── BTC: HYBRID STRATEGY (UPDATED) ───────────────────────────────
    if instrument == "btc":

        if regime_result["regime"] == "volatile":
            print(f"  ⚡ Volatile regime — no trade")
            return

        dispatcher = RegimeDispatcher(
            ema_strategy  = TrendPullbackStrategy(),   # replaced EMA
            grid_strategy = GridStrategy(grid_pct=0.015, num_levels=5, qty_per_level=0.05),
            dca_strategy  = None
        )

        signals = dispatcher.signal(candles, regime_result, state["positions"])

        for key, sig in signals.items():
            if not sig:
                continue

            action = sig.get("action", "hold")
            pos = state["positions"].get(key)

            if mode in ("buy", "both") and action in ("buy", "short") and pos is None:
                paper_open(instrument, state, sig, key)
                save_state(instrument, state)
                return

            if mode in ("sell", "both") and action in ("sell", "buy") and pos is not None:
                if (action == "sell" and pos["side"] == "long") or \
                   (action == "buy" and pos["side"] == "short"):
                    paper_close(instrument, state, sig, key)
                    save_state(instrument, state)
                    return

        print(f"  💤 No signal. Regime={regime_result['regime']} CMP={live_price}")
