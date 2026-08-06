#!/usr/bin/env python3
"""
Quick diagnostic script to understand what GFS is actually doing.
Shows the first 10 trades in detail — entry conditions, prices, exit.
"""
import warnings
warnings.filterwarnings("ignore")
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, date

RSI_PERIOD = 14

def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

print("Fetching Reliance data...")
raw = yf.download("RELIANCE.NS", start="2004-01-01", progress=False, auto_adjust=True)
if isinstance(raw.columns, pd.MultiIndex):
    raw.columns = raw.columns.get_level_values(0)
df = raw[["Open","High","Low","Close","Volume"]].copy()
df.columns = ["open","high","low","close","volume"]
df.index = pd.to_datetime(df.index)
df = df.dropna()

c = df["close"]
df["rsi_daily"]  = calc_rsi(c, RSI_PERIOD)
df["sma50"]      = c.rolling(50).mean()
df["sma200"]     = c.rolling(200).mean()
df["ema50"]      = c.ewm(span=50, adjust=False).mean()
df["vol_ma"]     = df["volume"].rolling(20).mean()

h, l = df["high"], df["low"]
tr   = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
up   = h.diff(); dn = -l.diff()
pdm  = np.where((up>dn)&(up>0),up,0.0)
mdm  = np.where((dn>up)&(dn>0),dn,0.0)
atr_ = tr.ewm(span=14,adjust=False).mean()
pdi  = 100*pd.Series(pdm,index=df.index).ewm(span=14,adjust=False).mean()/atr_
mdi  = 100*pd.Series(mdm,index=df.index).ewm(span=14,adjust=False).mean()/atr_
dx   = 100*(pdi-mdi).abs()/(pdi+mdi+1e-9)
df["adx"] = dx.ewm(span=14,adjust=False).mean()

# Weekly RSI
wc = df["close"].resample("W-FRI").last().dropna()
wr = calc_rsi(wc, RSI_PERIOD)
df["rsi_weekly"] = wr.reindex(df.index, method="ffill")

# Monthly RSI
mc = df["close"].resample("ME").last().dropna()
mr = calc_rsi(mc, RSI_PERIOD)
df["rsi_monthly"] = mr.reindex(df.index, method="ffill")

df = df.dropna(subset=["rsi_daily","rsi_weekly","rsi_monthly","sma200","ema50","adx"])

print(f"\nTotal bars after warmup: {len(df)}")
print(f"Date range: {df.index[0].date()} → {df.index[-1].date()}")

# Show distribution of daily RSI
print(f"\nDaily RSI statistics:")
print(f"  Mean:   {df['rsi_daily'].mean():.1f}")
print(f"  Median: {df['rsi_daily'].median():.1f}")
print(f"  Bars with RSI 35-45: {((df['rsi_daily']>=35)&(df['rsi_daily']<45)).sum()}")
print(f"  Bars with monthly RSI>60: {(df['rsi_monthly']>60).sum()}")
print(f"  Bars with weekly RSI>60:  {(df['rsi_weekly']>60).sum()}")

# GFS Basic signals
gfs_mask = (
    (df["rsi_monthly"] > 60) &
    (df["rsi_weekly"]  > 60) &
    (df["rsi_daily"]   < 45) &
    (df["rsi_daily"]  >= 35)
)
print(f"\nGFS Basic signals fired: {gfs_mask.sum()} times")
print(f"  = {gfs_mask.sum()/len(df)*100:.1f}% of all bars")

if gfs_mask.sum() > 0:
    print(f"\nFirst 15 GFS Basic signal dates:")
    signal_dates = df[gfs_mask].head(15)
    for idx, row in signal_dates.iterrows():
        print(f"  {idx.date()}  Price:{row['close']:.1f}  "
              f"RSI_D:{row['rsi_daily']:.1f}  "
              f"RSI_W:{row['rsi_weekly']:.1f}  "
              f"RSI_M:{row['rsi_monthly']:.1f}  "
              f"prevHigh:{df.loc[idx,'high']:.1f}")

# Check what happens AFTER a GFS signal — does RSI reach 70?
print(f"\nPost-signal RSI behaviour (max RSI in next 60 days after signal):")
signal_idx = df[gfs_mask].index
hit_70 = 0
hit_60 = 0
total  = 0
for sig_date in signal_idx[:30]:
    pos = df.index.get_loc(sig_date)
    future = df.iloc[pos:pos+60]["rsi_daily"]
    max_rsi = future.max()
    if max_rsi >= 70: hit_70 += 1
    if max_rsi >= 60: hit_60 += 1
    total += 1

if total > 0:
    print(f"  Signals where RSI reached 60+ within 60d: {hit_60}/{total} ({hit_60/total*100:.0f}%)")
    print(f"  Signals where RSI reached 70+ within 60d: {hit_70}/{total} ({hit_70/total*100:.0f}%)")

# Check entry price vs prev high
print(f"\nEntry logic check — is 'buy above prev high' the issue?")
print("  (If entry price >> close, we're often entering way above signal)")
consecutive = 0
for i in range(1, len(df)):
    if gfs_mask.iloc[i]:
        consecutive += 1
    else:
        consecutive = 0
    if consecutive > 1 and gfs_mask.iloc[i]:
        pass  # signal persists multiple days

# Check if signals cluster
signal_df = df[gfs_mask].copy()
if len(signal_df) > 1:
    gaps = signal_df.index.to_series().diff().dt.days.dropna()
    print(f"  Avg days between signals: {gaps.mean():.1f}")
    print(f"  Signals within 5 days of each other: {(gaps<=5).sum()}")
    print(f"  → If many signals cluster, we have re-entry on same move")

