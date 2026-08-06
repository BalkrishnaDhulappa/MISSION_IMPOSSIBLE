import pandas as pd


class DataValidator:

    REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]

    def validate(self, df: pd.DataFrame):

        # -------- 1. Column check --------
        if not all(col in df.columns for col in self.REQUIRED_COLUMNS):
            raise ValueError(f"Missing required columns: {df.columns}")

        # -------- 2. Null check --------
        if df[self.REQUIRED_COLUMNS].isnull().any().any():
            raise ValueError("Null values found in data")

        # -------- 3. Duplicate check --------
        if df.duplicated(subset=["date"]).any():
            raise ValueError("Duplicate dates found")

        # -------- 4. Minimum length --------
        if len(df) < 200:
            raise ValueError("Not enough data")

        # -------- 5. Date handling --------
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")

        gaps = df["date"].diff().dt.days
        gaps = gaps.dropna()

        # -------- 6. Gap validation (REALISTIC) --------
        # Allow weekends + holidays (~10 days max)
        # Only fail if something clearly wrong (like missing months)
        if (gaps > 10).any():
            raise ValueError("Unusual large gaps in data")

        return True
