from data_engine.fetcher import DataFetcher

fetcher = DataFetcher()

df = fetcher.get_data("nifty")

print(df.tail())
