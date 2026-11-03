# Loads a year of daily prices per company and the three derived columns the
# alert engine and materiality scoring read: pct_change, vol_20d_avg, vol_ratio.
# Computed once here, never recomputed on page load.
#
# An empty frame is a hard error. yfinance answers a dead symbol with an empty
# DataFrame rather than an exception, and a company carrying zero price rows
# would also zero the price and volume components of all its materiality scores.

from __future__ import annotations

import logging
import math
import time
import warnings
from typing import Any, Final

import pandas as pd
import yfinance as yf
from pandas.errors import PandasChangeWarning

from ingest.db import connect, upsert
from ingest.seed_companies import COMPANIES, Company

log = logging.getLogger(__name__)

PERIOD: Final = "1y"
BATCH_SIZE: Final = 5          # Yahoo rate limits above this with threads off
BATCH_PAUSE: Final = 2.0
VOL_WINDOW: Final = 20


def _clean(value: Any) -> Any:
    """NaN and inf are not valid numerics for Postgres."""
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _rows_for(symbol: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    df = frame.dropna(subset=["Close"]).sort_index()
    if df.empty:
        return []

    pct_change = df["Close"].pct_change() * 100
    vol_20d_avg = df["Volume"].rolling(VOL_WINDOW, min_periods=VOL_WINDOW).mean()
    vol_ratio = df["Volume"] / vol_20d_avg

    rows: list[dict[str, Any]] = []
    for ts in df.index:
        volume = df.at[ts, "Volume"]
        rows.append({
            "symbol": symbol,
            "date": ts.date(),
            "open": _clean(float(df.at[ts, "Open"])),
            "high": _clean(float(df.at[ts, "High"])),
            "low": _clean(float(df.at[ts, "Low"])),
            "close": _clean(float(df.at[ts, "Close"])),
            "volume": None if pd.isna(volume) else int(volume),
            "pct_change": _clean(float(pct_change[ts])),
            "vol_20d_avg": _clean(float(vol_20d_avg[ts])),
            "vol_ratio": _clean(float(vol_ratio[ts])),
        })
    return rows


def _download(tickers: list[str]) -> pd.DataFrame:
    # yfinance 1.7 internals still pass inplace= to pandas 3.1, which deprecated it.
    # Silenced around the call because the warnings come from its code, not ours.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", PandasChangeWarning)
        return yf.download(tickers, period=PERIOD, interval="1d", group_by="ticker",
                           auto_adjust=False, threads=False, progress=False)


def _frame_for(data: pd.DataFrame, ticker: str, batch_len: int) -> pd.DataFrame | None:
    if data is None or data.empty:
        return None
    if batch_len == 1 and not isinstance(data.columns, pd.MultiIndex):
        return data
    if ticker not in data.columns.get_level_values(0):
        return None
    return data[ticker]


def load(companies: list[Company]) -> tuple[int, list[str]]:
    written = 0
    empty: list[str] = []

    with connect() as conn:
        for start in range(0, len(companies), BATCH_SIZE):
            batch = companies[start:start + BATCH_SIZE]
            tickers = [f"{c.symbol}.NS" for c in batch]
            data = _download(tickers)

            for company in batch:
                frame = _frame_for(data, f"{company.symbol}.NS", len(batch))
                rows = _rows_for(company.symbol, frame) if frame is not None else []
                if not rows:
                    log.error("%s returned no price rows", company.symbol)
                    empty.append(company.symbol)
                    continue
                written += upsert(conn, "prices", rows, ["symbol", "date"])
                log.info("%-11s %4d rows  %s -> %s", company.symbol, len(rows),
                         rows[0]["date"], rows[-1]["date"])

            if start + BATCH_SIZE < len(companies):
                time.sleep(BATCH_PAUSE)

    return written, empty


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    written, empty = load(COMPANIES)
    log.info("wrote %d price rows for %d companies", written, len(COMPANIES) - len(empty))
    if empty:
        raise SystemExit("no price data, check the symbols are still listed: "
                         + ", ".join(empty))


if __name__ == "__main__":
    main()
