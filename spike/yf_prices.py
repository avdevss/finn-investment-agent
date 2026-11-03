# Spike 2: confirm yfinance returns a non-empty daily frame for two NSE tickers
# and that the derived columns the alert engine needs can be computed from it.

import sys
import warnings

import yfinance as yf

# yfinance 1.7 internals trip pandas 3.1 deprecations; not our call sites.
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=r".*inplace keyword.*")

TICKERS = ["RELIANCE.NS", "TCS.NS"]


def main() -> int:
    try:
        df = yf.download(TICKERS, period="1y", interval="1d", group_by="ticker",
                         auto_adjust=False, threads=False, progress=False)
    except Exception as e:
        print(f"FAIL  yf.download raised {type(e).__name__}: {e}")
        return 1

    if df is None or df.empty:
        print("FAIL  empty frame (yfinance fails silently, this is the real risk)")
        return 1

    print(f"frame {df.shape[0]} rows x {df.shape[1]} cols, {df.index.min().date()} -> {df.index.max().date()}")

    for t in TICKERS:
        if t not in df.columns.get_level_values(0):
            print(f"FAIL  {t} missing from frame")
            return 1
        sub = df[t].dropna(subset=["Close"])
        if sub.empty:
            print(f"FAIL  {t} has no close prices")
            return 1
        pct = sub["Close"].pct_change() * 100
        vol_ratio = sub["Volume"] / sub["Volume"].rolling(20).mean()
        last = sub.index[-1].date()
        print(f"  {t:<14} {len(sub):>4} rows  last {last}  "
              f"close {sub['Close'].iloc[-1]:.2f}  pct {pct.iloc[-1]:+.2f}%  vol_ratio {vol_ratio.iloc[-1]:.2f}")

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
