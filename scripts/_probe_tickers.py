"""Quick probe: which Yahoo tickers exist for the book's exotic pairs."""
import yfinance as yf

for t in ["USDCZK=X", "CZK=X", "USDSKX=X"]:
    try:
        df = yf.Ticker(t).history(interval="60m", period="730d", auto_adjust=False)
        n = 0 if df is None else len(df)
        rng = f"{df.index[0]} -> {df.index[-1]}  last_close={float(df['Close'].iloc[-1]):.5f}" if n else ""
        print(f"{t:12} bars={n}  {rng}")
    except Exception as e:
        print(f"{t:12} ERROR {type(e).__name__}: {e}")
