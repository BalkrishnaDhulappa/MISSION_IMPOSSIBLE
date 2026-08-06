import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

from data_engine.validator import DataValidator


SYMBOL_MAP = {
    "nifty": "^NSEI",
    "banknifty": "^NSEBANK",
    "reliance": "RELIANCE.NS"
}


DATA_DIR = Path("data")


class DataFetcher:

    def __init__(self):
        DATA_DIR.mkdir(exist_ok=True)

    def _get_file_path(self, instrument):
        folder = DATA_DIR / instrument
        folder.mkdir(exist_ok=True)
        return folder / "daily.csv"

    def _download_data(self, symbol, start, end):
        df = yf.download(symbol, start=start, end=end, interval="1d", progress=False)

        if df.empty:
            raise ValueError(f"No data fetched for {symbol}")

        # Reset index
        df = df.reset_index()

        # Handle MultiIndex columns (yfinance issue)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]

        # Normalize column names
        df.columns = [str(col).lower() for col in df.columns]

        required_cols = ["date", "open", "high", "low", "close", "volume"]

        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            raise ValueError(f"Missing columns after fetch: {missing}")

        df = df[required_cols]

        return df

    def get_data(self, instrument):
        symbol = SYMBOL_MAP[instrument.lower()]
        file_path = self._get_file_path(instrument)
        validator = DataValidator()

        # ---------- FIRST TIME FETCH ----------
        if not file_path.exists():
            print(f"[INFO] Fetching full data for {instrument}")

            df = self._download_data(symbol, "2000-01-01", datetime.today())

            validator.validate(df)

            df.to_csv(file_path, index=False)
            return df

        # ---------- INCREMENTAL UPDATE ----------
        print(f"[INFO] Updating data for {instrument}")

        existing_df = pd.read_csv(file_path)
        existing_df["date"] = pd.to_datetime(existing_df["date"])

        last_date = existing_df["date"].max()
        start_date = last_date + timedelta(days=1)

        # Already up-to-date
        if start_date.date() >= datetime.today().date():
            validator.validate(existing_df)
            return existing_df

        new_data = self._download_data(symbol, start_date, datetime.today())

        if not new_data.empty:
            updated_df = pd.concat([existing_df, new_data])
            updated_df.drop_duplicates(subset=["date"], inplace=True)

            try:
                validator.validate(updated_df)
            except Exception as e:
                print(f"[ERROR] Validation failed: {e}")
                print("[INFO] Refetching full data...")

                df = self._download_data(symbol, "2000-01-01", datetime.today())
                validator.validate(df)

                df.to_csv(file_path, index=False)
                return df

            updated_df.to_csv(file_path, index=False)
            return updated_df

        # No new data
        validator.validate(existing_df)
        return existing_df
